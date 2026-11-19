"""Definition parsing and checks, mount planning and card rendering; no engine needed."""

import dataclasses
import shlex
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from mindslice_flow import FlowError, definition, run

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "manor"


class DefinitionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="flow-unit-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name in ("common", "a", "b"):
            (self.root / f"{name}.slice").mkdir()

    def write(self, text: str) -> None:
        (self.root / "flow.toml").write_text(text, encoding="utf-8")

    def test_example(self):
        flow = definition.load(EXAMPLE)
        self.assertEqual((flow.name, flow.state), ("迷雾庄园", "story"))
        self.assertEqual(flow.ids(), ["act1", "act2", "act3", "cellar"])
        self.assertEqual([s.id for s in flow.main()], ["act1", "act2", "act3"])
        self.assertEqual((flow.main_index("act3"), flow.main_index("cellar")), (2, None))
        self.assertEqual(flow.stages[2].premise, "玩家拿到了东翼的钥匙")
        self.assertTrue(flow.stages[3].side)
        self.assertEqual([p.name for p in flow.mounts(flow.stages[1])], ["world.slice", "act2.slice"])
        self.assertEqual([p.name for p in flow.mounts(None)], ["world.slice"])
        self.assertEqual(definition.warnings(flow), [])

    def test_defaults(self):
        self.write('[[stage]]\nid = "a"\nmount = ["a.slice"]\n')
        flow = definition.load(self.root)
        self.assertEqual((flow.name, flow.state, flow.mount), (self.root.name, "story", ()))
        self.assertEqual(flow.stages[0].title, "a")

    def test_errors(self):
        cases = {
            "": "at least one",
            '[[stage]]\nid = "a"\n[[stage]]\nid = "a"\n': "used twice",
            '[[stage]]\nid = "a b"\n': "`id`",
            '[[stage]]\nid = "a"\nmount = ["missing.slice"]\n': "does not exist",
            'mount = "common.slice"\n[[stage]]\nid = "a"\n': "list of paths",
            'state = "flow"\n[[stage]]\nid = "a"\n': "`state`",
            '[[stage]]\nid = "a"\npremise = 1\n': "`premise`",
            '[[stage]]\nid = "a"\nside = "yes"\n': "`side`",
            '[[stage]]\nid = "a"\nside = true\n': "main line",
            'mount = ["common.slice"]\n[[stage]]\nid = "a"\nmount = ["common.slice/../a.slice", "a.slice"]\n': None,
        }
        for text, message in cases.items():
            with self.subTest(text=text):
                self.write(text)
                if message is None:  # the same directory twice is not a collision, and mounts once
                    flow = definition.load(self.root)
                    self.assertEqual([p.name for p in flow.mounts(flow.stages[0])], ["common.slice", "a.slice"])
                    continue
                with self.assertRaises(FlowError) as caught:
                    definition.load(self.root)
                self.assertIn(message, str(caught.exception))

    def test_alias_collisions(self):
        (self.root / "b.slice" / "slice.toml").write_text('alias = "@a"\n', encoding="utf-8")
        self.write('[[stage]]\nid = "x"\nmount = ["a.slice", "b.slice"]\n')
        with self.assertRaisesRegex(FlowError, "both mount as @a"):
            definition.load(self.root)
        (self.root / "b.slice" / "slice.toml").write_text('alias = "story"\n', encoding="utf-8")
        with self.assertRaisesRegex(FlowError, "reserves"):
            definition.load(self.root)
        self.assertEqual(definition.alias(self.root / "a.slice"), "a")

    def test_alias_the_engine_rejects(self):
        (self.root / "世界.slice").mkdir()
        self.write('mount = ["世界.slice"]\n[[stage]]\nid = "x"\n')
        with self.assertRaisesRegex(FlowError, "cannot mount as @世界"):
            definition.load(self.root)
        (self.root / "世界.slice" / "slice.toml").write_text('alias = "@world"\n', encoding="utf-8")
        self.assertEqual(definition.alias(definition.load(self.root).mount[0]), "world")

    def test_always_loaded_warning(self):
        (self.root / "a.slice" / "director.md").write_text('+++\ntitle = "d"\nload = "always"\n+++\nx\n',
                                                          encoding="utf-8")
        (self.root / "b.slice" / "npc.md").write_text('+++\ntitle = "n"\n+++\nx\n', encoding="utf-8")
        self.write('[[stage]]\nid = "a"\nmount = ["a.slice"]\n[[stage]]\nid = "b"\nmount = ["b.slice"]\n')
        warnings = definition.warnings(definition.load(self.root))
        self.assertEqual(len(warnings), 1)
        self.assertIn("stage b", warnings[0])

    def test_unknown_stage(self):
        with self.assertRaisesRegex(FlowError, "stages: act1, act2, act3, cellar"):
            definition.load(EXAMPLE).index("nope")


def state(stage, done=(), reuse=(), generated=None):
    return {"stage": stage, "done": [{"stage": d, "version": None} for d in done], "reuse": list(reuse),
            "generated": generated or {}}


SIDE = {"side1": {"title": "海边小屋", "slice": "slices/side1.slice", "rejoin": "act2"}}


class RouteTest(unittest.TestCase):
    """Where `next` goes, and what `--reuse` may name, from each kind of stage."""

    def setUp(self):
        self.flow = definition.load(EXAMPLE)
        self.ws = Path("/ws")

    def default(self, st):
        target = run.default_next(self.ws, self.flow, st)
        return target.id if target else None

    def test_default_next(self):
        self.assertEqual(self.default(state("act1")), "act2")
        self.assertEqual(self.default(state("act3", ["act1", "act2"])), None)
        self.assertEqual(self.default(state("cellar", ["act1"])), "act2")  # back after the last main-line stage
        self.assertEqual(self.default(state("cellar", ["act1", "act2"])), "act3")
        self.assertEqual(self.default(state("side1", ["act1"], generated=SIDE)), "act2")  # its rejoin
        ended = {"side1": SIDE["side1"] | {"rejoin": None}}
        self.assertEqual(self.default(state("side1", ["act1"], generated=ended)), None)
        self.assertEqual(self.default(state("act1", ["act1", "act2", "act3"])), "act2")  # a replay goes on

    def test_reusable(self):
        flow, act2, act3 = self.flow, self.flow.stages[1], self.flow.stages[2]
        self.assertEqual(run.reusable(flow, ["act1"], act2), ["act1", "act2"])
        self.assertEqual(run.reusable(flow, ["act1", "side1"], act3), ["act1", "side1", "act2", "act3"])
        self.assertEqual(run.reusable(flow, ["act1", "act2", "act3"], None), ["act1", "act2", "act3"])

    def test_generated_stage(self):
        stage = run.stage_of(self.ws, self.flow, state("side1", generated=SIDE), "side1")
        self.assertEqual((stage.title, stage.side, stage.generated), ("海边小屋", True, True))
        self.assertEqual(stage.mount, (Path("/ws/slices/side1.slice"),))
        with self.assertRaisesRegex(FlowError, "stages: act1, act2, act3, cellar, side1"):
            run.stage_of(self.ws, self.flow, state("side1", generated=SIDE), "nope")


class CardsTest(unittest.TestCase):
    def setUp(self):
        self.flow = definition.load(EXAMPLE)
        self.ws = Path("/tmp/my run")

    def cards(self, st, *args):
        return run.cards(self.ws, self.flow, st, *args)

    def test_stage_cards(self):
        files = self.cards(state("act2", ["act1"]))
        self.assertEqual(set(files), {"slice.toml", "stage.md", "settlement.md"})
        for text in files.values():
            self.assertNotIn("{{", text)
        stage, settle = files["stage.md"], files["settlement.md"]
        self.assertIn('load = "always"', stage)
        self.assertIn("主线第 2 幕（共 3 幕）「第二幕：晚宴」", stage)
        self.assertIn("已走过的路线：「第一幕：抵达」 → 本幕。", stage)
        self.assertIn("入口条件是「玩家留在庄园过夜，打算查一查封闭的东翼」", stage)
        self.assertIn("写改编卡 `stages/act2/adaptation`", stage)
        self.assertIn("@story/summaries/ 下（act1）", stage)
        self.assertNotIn('load = "always"', settle)
        cmd = f"mindslice-flow -w {shlex.quote(str(self.ws))}"
        self.assertIn(f"下面的 `next …` 都是 `{cmd} next …` 的简写", settle)
        self.assertIn("默认去向「第三幕：真相」（入口条件：玩家拿到了东翼的钥匙）：`next`", settle)
        self.assertIn("`next --to <id>`，可选 `cellar`「酒窖」（入口条件：玩家发现了通往酒窖的暗门）", settle)
        self.assertIn('`next --new <新 id> --title "<标题>" [--rejoin <主线 id>]`（不写 --rejoin 时回到「第三幕：真相」；标题不必带「支线」二字）',
                      settle)
        self.assertIn("已经走过的幕（act1, act2）", settle)
        self.assertIn("适用于全剧的主持规则（如回复篇幅、节奏、不提幕后操作）也要写进方案", settle)
        self.assertIn("summaries/act2", settle)
        self.assertIn("没有本幕的改编卡 `stages/act2/adaptation`", settle)
        self.assertIn('alias = "@flow"', files["slice.toml"])

    def test_first_and_last(self):
        first = self.cards(state("act1"))
        self.assertIn("第一幕，还没有前情", first["stage.md"])
        self.assertNotIn("改编卡", first["stage.md"] + first["settlement.md"])
        self.assertNotIn("已走过的路线", first["stage.md"])
        self.assertIn("剧情已经满足主线后面某一幕的入口条件", first["settlement.md"])
        self.assertIn("`act3`「第三幕：真相」", first["settlement.md"])  # act2 is the default, act3 a jump
        self.assertIn("新会话", first["settlement.md"])
        last = self.cards(state("act3", ["act1", "act2", "act1"]))
        self.assertIn("（act1, act2）", last["stage.md"])
        self.assertIn("结束整个流程", last["settlement.md"])
        self.assertIn("回到流程结束", last["settlement.md"])

    def test_recap(self):
        body = "- 玩家拿到了钥匙。{{state}}\n"
        text = self.cards(state("act3", ["act1", "act2"]), ("act2", body))["stage.md"]
        self.assertIn("上一幕（act2）的摘要附在本卡末尾", text)
        self.assertIn("@story/summaries/ 下（act1）", text)
        self.assertTrue(text.endswith("## 上一幕摘要（@story/summaries/act2）\n\n- 玩家拿到了钥匙。{{state}}\n"))
        first = self.cards(state("act2", ["act1"]), ("act1", body))["stage.md"]
        self.assertNotIn("更早各幕", first)
        plain = self.cards(state("act2", ["act1"]))["stage.md"]
        self.assertTrue(plain.endswith("并照做。\n"))

    def test_card_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "plan.md"
            card.write_text('+++\ntitle = "方案"\nsummary = "一句话"\n+++\n\n- 要点\n', encoding="utf-8")
            self.assertEqual(run.card_text(card), "一句话\n\n- 要点")
            card.write_text('+++\ntitle = "方案"\n+++\n\n首段即摘要。\n\n- 要点\n', encoding="utf-8")
            self.assertEqual(run.card_text(card), "首段即摘要。\n\n- 要点")
            self.assertIsNone(run.card_text(Path(tmp) / "missing.md"))
        self.assertEqual(run.text(" 摘要 ", ""), "摘要")

    def test_generated_stage(self):
        st = state("side1", ["act1"], reuse=["act1"], generated=SIDE)
        files = self.cards(st, ("act1", "见[[player/vow]]"), "去[[stages/side1/hut|小屋]]，见[[@story/player/vow]]。")
        stage = files["stage.md"]
        self.assertIn("支线（本局生成）「海边小屋」", stage)
        self.assertIn("照导演方案主持", stage)
        self.assertIn("本幕不在预设剧本里", stage)
        self.assertIn("默认回到主线「第二幕：晚宴」（入口条件：玩家留在庄园过夜", stage)
        self.assertIn("本幕还挂载了 @act1 作素材", stage)
        self.assertNotIn("改编卡", stage)
        self.assertIn("## 本幕导演方案（@side1/stages/side1/plan）\n\n去[[@side1/stages/side1/hut|小屋]]，"
                      "见[[@story/player/vow]]。\n\n## 上一幕摘要", stage)  # the plan, then the recap
        self.assertTrue(stage.endswith("见[[@story/player/vow]]\n"))
        reused = files["reused.md"]
        meta = tomllib.loads(reused.split("+++\n")[1])
        self.assertEqual(meta["supersedes"], ["@act1/director"])
        self.assertNotIn("load", meta)
        self.assertIn("默认去向「第二幕：晚宴」", files["settlement.md"])
        self.assertNotIn("没有本幕的改编卡", files["settlement.md"])

    def test_reuse_without_director(self):
        files = self.cards(state("act3", ["act1", "act2"], reuse=["act3", "act2"]))
        self.assertIn("reused.md", files)  # act2's director card stands aside; act3 is the stage itself
        self.assertEqual(tomllib.loads(files["reused.md"].split("+++\n")[1])["supersedes"], ["@act2/director"])
        self.assertNotIn("reused.md", self.cards(state("act2", ["act1"])))

    def test_qualify_links(self):
        body = "[[player/status|叶凡]]、[[ 庞博 ]]、[[@world/npcs/x|甲]]与[[summaries/act1]]"
        self.assertEqual(run.qualify_links(body, "story"),
                         "[[@story/player/status|叶凡]]、[[@story/庞博]]、[[@world/npcs/x|甲]]与[[@story/summaries/act1]]")
        text = self.cards(state("act2", ["act1"]), ("act1", "见[[player/vow]]"))["stage.md"]
        self.assertIn("见[[@story/player/vow]]", text)

    def test_quotes_in_names(self):
        title = '第一幕："抵达" \\ {{state}}'
        stage = dataclasses.replace(self.flow.stages[0], title=title)
        flow = dataclasses.replace(self.flow, name='迷雾"庄园"', stages=(stage, *self.flow.stages[1:]))
        text = run.cards(self.ws, flow, state("act1"))["stage.md"]
        meta = tomllib.loads(text.split("+++\n")[1])
        self.assertEqual(meta["title"], f"当前阶段：{title}")
        self.assertIn('「迷雾"庄园"」', meta["summary"])
        self.assertIn(f"「{title}」", text.split("+++\n")[2])  # the body is filled as is, in one pass

    def test_finished(self):
        files = self.cards(state(None, ["act1", "act2", "act3"]))
        self.assertEqual(set(files), {"slice.toml", "stage.md"})
        self.assertIn("全部 3 幕已经结束", files["stage.md"])
        self.assertNotIn("{{", files["stage.md"])


class SyncTest(unittest.TestCase):
    """Which mounts change, with the engine replaced by a fake mount table."""

    def test_sync(self):
        flow = definition.load(EXAMPLE)
        ws = Path("/ws")
        slices = EXAMPLE / "slices"
        table = [
            {"alias": "@overlay", "path": "./overlay"},
            {"alias": "@story", "path": "./slices/story.slice"},
            {"alias": "@flow", "path": "./.flow/stage.slice"},
            {"alias": "@world", "path": str(slices / "world.slice")},
            {"alias": "@act1", "path": str(slices / "act1.slice")},
            {"alias": "@extra", "path": "/elsewhere/extra.slice"},
        ]
        calls = []
        want = run.wanted(flow, flow.stages[1], [flow.stages[0], flow.stages[1]])
        self.assertEqual([p.name for p in want], ["world.slice", "act2.slice", "act1.slice"])
        with mock.patch.object(run.engine, "status", return_value={"mounts": table}), \
                mock.patch.object(run.engine, "unmount", side_effect=lambda w, a: calls.append(("unmount", a))), \
                mock.patch.object(run.engine, "mount", side_effect=lambda w, p, prec, alias=None:
                                  calls.append(("mount", p.name, prec, alias)) or f"@{alias}"):
            added, removed = run.sync(ws, flow, want)
        self.assertEqual(removed, ["@extra"])
        self.assertEqual(added, ["@act2"])
        self.assertIn(("mount", "act2.slice", run.SCRIPT_PRECEDENCE, "act2"), calls)


class EngineCommandTest(unittest.TestCase):
    """The engine is called by the full path ``shutil.which`` finds, so a Windows ``.cmd`` shim works."""

    def test_full_path(self):
        shim = r"C:\tools\bin\mindslice.CMD"
        done = mock.Mock(returncode=0, stdout="mindslice 9.9.9\n", stderr="")
        with mock.patch.object(run.engine.shutil, "which", return_value=shim), \
                mock.patch.object(run.engine.subprocess, "run", return_value=done) as called:
            run.engine.version()
        self.assertEqual(called.call_args.args[0], [shim, "--version"])

    def test_missing(self):
        with mock.patch.object(run.engine.shutil, "which", return_value=None):
            with self.assertRaisesRegex(FlowError, "not on PATH"):
                run.engine.version()


if __name__ == "__main__":
    unittest.main()
