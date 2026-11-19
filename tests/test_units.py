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
        self.assertEqual(flow.ids(), ["act1", "act2", "act3"])
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
        with self.assertRaisesRegex(FlowError, "stages: act1, act2, act3"):
            definition.load(EXAMPLE).index("nope")


class CardsTest(unittest.TestCase):
    def setUp(self):
        self.flow = definition.load(EXAMPLE)
        self.ws = Path("/tmp/my run")

    def test_stage_cards(self):
        files = run.cards(self.ws, self.flow, self.flow.stages[1], ["act1"])
        self.assertEqual(set(files), {"slice.toml", "stage.md", "settlement.md"})
        for text in files.values():
            self.assertNotIn("{{", text)
        self.assertIn('load = "always"', files["stage.md"])
        self.assertIn("第 2 幕（共 3 幕）", files["stage.md"])
        self.assertIn("@story/summaries/ 下（act1）", files["stage.md"])
        self.assertNotIn('load = "always"', files["settlement.md"])
        self.assertIn(f"mindslice-flow -w {shlex.quote(str(self.ws))} next", files["settlement.md"])
        self.assertIn("summaries/act2", files["settlement.md"])
        self.assertIn('alias = "@flow"', files["slice.toml"])

    def test_first_and_last(self):
        first = run.cards(self.ws, self.flow, self.flow.stages[0], [])
        self.assertIn("第一幕，还没有前情", first["stage.md"])
        self.assertIn("新会话", first["settlement.md"])
        last = run.cards(self.ws, self.flow, self.flow.stages[2], ["act1", "act2", "act1"])
        self.assertIn("（act1, act2）", last["stage.md"])
        self.assertIn("最后一幕", last["settlement.md"])

    def test_recap(self):
        body = "- 玩家拿到了钥匙。{{state}}\n"
        text = run.cards(self.ws, self.flow, self.flow.stages[2], ["act1", "act2"], ("act2", body))["stage.md"]
        self.assertIn("上一幕（act2）的摘要附在本卡末尾", text)
        self.assertIn("@story/summaries/ 下（act1）", text)
        self.assertTrue(text.endswith("## 上一幕摘要（@story/summaries/act2）\n\n- 玩家拿到了钥匙。{{state}}\n"))
        first = run.cards(self.ws, self.flow, self.flow.stages[1], ["act1"], ("act1", body))["stage.md"]
        self.assertNotIn("更早各幕", first)
        plain = run.cards(self.ws, self.flow, self.flow.stages[1], ["act1"])["stage.md"]
        self.assertTrue(plain.endswith("并照做。\n"))

    def test_quotes_in_names(self):
        title = '第一幕："抵达" \\ {{state}}'
        stage = dataclasses.replace(self.flow.stages[0], title=title)
        flow = dataclasses.replace(self.flow, name='迷雾"庄园"', stages=(stage, *self.flow.stages[1:]))
        text = run.cards(self.ws, flow, stage, [])["stage.md"]
        meta = tomllib.loads(text.split("+++\n")[1])
        self.assertEqual(meta["title"], f"当前阶段：{title}")
        self.assertIn('「迷雾"庄园"」', meta["summary"])
        self.assertIn(f"「{title}」", text.split("+++\n")[2])  # the body is filled as is, in one pass

    def test_finished(self):
        files = run.cards(self.ws, self.flow, None, ["act1", "act2", "act3"])
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
        with mock.patch.object(run.engine, "status", return_value={"mounts": table}), \
                mock.patch.object(run.engine, "unmount", side_effect=lambda w, a: calls.append(("unmount", a))), \
                mock.patch.object(run.engine, "mount", side_effect=lambda w, p, prec, alias=None:
                                  calls.append(("mount", p.name, prec, alias)) or f"@{alias}"):
            added, removed = run.sync(ws, flow, flow.stages[1])
        self.assertEqual(removed, ["@act1", "@extra"])
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
