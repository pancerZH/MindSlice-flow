"""Whole runs of the example flow with the real ``mindslice`` command; no model is called.

The test plays the agent: it writes overlay cards with ``mindslice write`` and runs ``next``.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from mindslice_flow import FlowError, cli, run

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "manor"


@unittest.skipUnless(shutil.which("mindslice"), "needs the mindslice command")
class FunctionalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="flow-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "run"

    # -- helpers ------------------------------------------------------------

    def flow(self, *args):
        """Run the CLI in-process; returns (exit code, stdout, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([str(a) for a in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def ok(self, *args):
        code, out, err = self.flow(*args)
        self.assertEqual(code, 0, err)
        return out

    def init(self):
        return self.ok("init", EXAMPLE, "-o", self.ws)

    def next(self, *args):
        return self.ok("-w", self.ws, "next", *args)

    def engine(self, *args):
        done = subprocess.run([shutil.which("mindslice"), "-w", str(self.ws), *args], capture_output=True, text=True,
                              encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def write(self, card, content, *extra):
        self.engine("write", card, "--content", content, "--summary", content[:20], "--reason", "test",
                    "--json", *extra)

    def aliases(self):
        return {m["alias"] for m in json.loads(self.engine("status", "--json"))["mounts"]}

    def boot(self):
        return self.engine("boot", "--cards-only")

    def state(self):
        return json.loads((self.ws / run.STATE).read_text(encoding="utf-8"))

    def story_version(self):
        mounts = json.loads(self.engine("status", "--json"))["mounts"]
        path = next(self.ws / m["path"] for m in mounts if m["alias"] == "@story")
        return tomllib.loads((path / "slice.toml").read_text(encoding="utf-8"))["version"]

    def overlay(self):
        return sorted(p.name for p in (self.ws / "overlay").rglob("*.md"))

    def play_act1(self):
        self.write("summaries/act1", "玩家抵达庄园，立下[[player/vow|誓言]]，决定晚宴后调查东翼。")
        self.write("people/butler-secret", "格雷在玩家面前摸了摸腰间的钥匙。", "--supersedes", "@world/people/butler")
        self.write("player/vow", "玩家发誓查清老爵士的死因。", "--load", "always", "--no-stage")

    # -- tests --------------------------------------------------------------

    def test_full_run(self):
        out = self.init()
        self.assertIn("MINDSLICE_WORKSPACE=", out)
        self.assertEqual(self.aliases(), {"@overlay", "@world", "@act1", "@flow"})
        boot = self.boot()
        self.assertIn("第一幕导演卡", boot)
        self.assertIn("当前阶段：第一幕：抵达", boot)
        self.assertEqual(self.state(), {"flow": str(EXAMPLE), "state": "story", "stage": "act1", "done": []})

        self.play_act1()
        self.assertIn("overlay cards to settle (3)", self.ok("-w", self.ws, "status"))
        out = self.next()
        self.assertIn("baked 3 cards into @story 0.1.0", out)
        self.assertIn("unmounted @act1", out)
        self.assertEqual(self.story_version(), "0.1.0")
        self.assertEqual(self.overlay(), [])
        self.assertEqual(self.aliases(), {"@overlay", "@world", "@act2", "@flow", "@story"})
        boot = self.boot()
        self.assertIn("第二幕导演卡", boot)
        self.assertNotIn("第一幕导演卡", boot)
        self.assertIn("玩家发誓查清老爵士的死因", boot)  # the baked always-loaded card stays standing
        # the stage card carries the baked summary of act1 itself
        self.assertIn("上一幕（act1）的摘要附在本卡末尾", boot)
        # its links now point into @story, where they resolve
        self.assertIn("## 上一幕摘要（@story/summaries/act1）\n\n玩家抵达庄园，立下[[@story/player/vow|誓言]]", boot)
        # the baked fork still reads, and still hides the world card it supersedes
        read = json.loads(self.engine("read", "people/butler-secret", "--json"))
        self.assertEqual(read["address"], "@story/people/butler-secret")
        validate = json.loads(self.engine("validate", "--json"))
        self.assertEqual(validate["errors"], [])
        self.assertEqual([w for w in validate["warnings"] if w.get("address", "").startswith("@flow/")], [])

        self.write("summaries/act2", "玩家说服格雷交出了钥匙。")
        self.write("player/vow", "- 玩家答应替格雷保密。", "--op", "append", "--no-stage")
        self.assertIn("@story 0.1.1", self.next())
        self.assertEqual(self.story_version(), "0.1.1")
        boot = self.boot()
        self.assertIn("第三幕导演卡", boot)
        self.assertIn("上一幕摘要（@story/summaries/act2）\n\n玩家说服格雷交出了钥匙。", boot)
        self.assertNotIn("玩家抵达庄园", boot)  # an older summary is only named
        self.assertIn("@story/summaries/ 下（act1）", boot)
        self.assertEqual(self.overlay(), [])

        out = self.next()  # nothing new in act3's overlay
        self.assertIn("nothing in @overlay to bake", out)
        self.assertIn("is finished", out)
        self.assertEqual(self.aliases(), {"@overlay", "@world", "@flow", "@story"})
        self.assertIn("流程已结束", self.boot())
        self.assertFalse((self.ws / run.FLOW_SLICE / "settlement.md").exists())
        self.assertEqual(self.state()["done"], [{"stage": "act1", "version": "0.1.0"},
                                                {"stage": "act2", "version": "0.1.1"},
                                                {"stage": "act3", "version": "0.1.1"}])
        self.assertIsNone(self.state()["stage"])
        self.assertIn("the flow is finished", self.ok("-w", self.ws, "status"))

        code, _, err = self.flow("-w", self.ws, "next")
        self.assertEqual(code, 1)
        self.assertIn("--to", err)
        self.next("--to", "act2")
        self.assertEqual(self.state()["stage"], "act2")
        self.assertIn("第二幕导演卡", self.boot())

    def test_pending_review_blocks_next(self):
        self.init()
        self.write("summaries/act1", "摘要")
        self.write("director-note", "常驻笔记", "--load", "always")  # staged for approval
        before = (self.state(), self.aliases())
        code, _, err = self.flow("-w", self.ws, "next")
        self.assertEqual(code, 1)
        self.assertIn("waiting for review", err)
        self.assertIn("@overlay/director-note", err)
        self.assertEqual((self.state(), self.aliases()), before)
        self.assertEqual(self.overlay(), ["act1.md"])
        self.assertIn("pending review (1)", self.ok("-w", self.ws, "status"))

        self.engine("discard", "--json")
        self.assertIn("@story 0.1.0", self.next())

    def test_settlement_checks_block_next(self):
        """An overlay is baked only with the stage summary in it and without card errors."""
        self.init()
        self.write("people/ghost-note", "玩家怀疑东翼闹鬼。", "--extends", "@overlay/people/ghost")
        before = (self.state(), self.aliases(), self.overlay())
        code, _, err = self.flow("-w", self.ws, "next")
        self.assertEqual(code, 1)
        self.assertIn("summaries/act1", err)
        self.assertEqual((self.state(), self.aliases(), self.overlay()), before)

        self.write("summaries/act1", "玩家抵达庄园。")
        code, _, err = self.flow("-w", self.ws, "next")
        self.assertEqual(code, 1)
        self.assertIn("@overlay/people/ghost-note [broken_lineage]", err)
        self.assertEqual(self.state()["stage"], "act1")

        self.write("people/ghost", "东翼的传闻。")  # the extends target now exists
        self.assertIn("baked 3 cards into @story 0.1.0", self.next())

    def test_next_to(self):
        self.init()
        self.next("--to", "act3")
        self.assertEqual(self.state()["stage"], "act3")
        self.assertEqual(self.state()["done"], [{"stage": "act1", "version": None}])
        self.assertIn("@act3", self.aliases())
        self.assertNotIn("@story", self.aliases())  # nothing was baked yet
        self.assertIn("@story/summaries/ 下（act1）", self.boot())  # no baked summary to attach
        code, _, err = self.flow("-w", self.ws, "next", "--to", "nope")
        self.assertEqual(code, 1)
        self.assertIn("stages: act1, act2, act3", err)
        self.assertEqual(self.state()["stage"], "act3")

    def test_rerun_after_interrupt(self):
        self.init()
        self.play_act1()
        with mock.patch.object(run, "sync", side_effect=KeyboardInterrupt):
            code, _, _ = self.flow("-w", self.ws, "next")
        self.assertEqual(code, 130)
        self.assertEqual(self.state()["stage"], "act1")  # baked, but not switched
        self.assertEqual(self.overlay(), [])

        out = self.next()
        self.assertIn("nothing in @overlay to bake", out)
        self.assertEqual(self.state(), {"flow": str(EXAMPLE), "state": "story", "stage": "act2",
                                        "done": [{"stage": "act1", "version": "0.1.0"}]})
        self.assertEqual(self.aliases(), {"@overlay", "@world", "@act2", "@flow", "@story"})
        self.assertIn("第二幕导演卡", self.boot())

    def test_failed_init_leaves_no_workspace(self):
        with mock.patch.object(run, "sync", side_effect=FlowError("mount failed")):
            code, _, err = self.flow("init", EXAMPLE, "-o", self.ws)
        self.assertEqual(code, 1)
        self.assertIn("mount failed", err)
        self.assertFalse(self.ws.exists())
        self.init()  # so the same command works once the cause is fixed

    def test_definition_changes(self):
        """A slice whose directory name is no alias, a title with quotes, and edits the run cannot follow."""
        flow_dir = self.tmp / "def"
        shutil.copytree(EXAMPLE, flow_dir)
        world = flow_dir / "slices" / "世界.slice"
        (flow_dir / "slices" / "world.slice").rename(world)
        with (world / "slice.toml").open("a", encoding="utf-8") as meta:
            meta.write('alias = "lore"\n')
        toml = flow_dir / "flow.toml"
        text = toml.read_text(encoding="utf-8").replace("slices/world.slice", "slices/世界.slice")
        toml.write_text(text.replace('"第一幕：抵达"', "'第一幕：\"抵达\"'"), encoding="utf-8")

        self.ok("init", flow_dir, "-o", self.ws)
        self.assertEqual(self.aliases(), {"@overlay", "@lore", "@act1", "@flow"})
        self.assertIn('当前阶段：第一幕："抵达"', self.boot())
        self.assertEqual(json.loads(self.engine("validate", "--json"))["errors"], [])

        toml.write_text(text.replace('state = "story"', 'state = "saga"'), encoding="utf-8")
        code, _, err = self.flow("-w", self.ws, "next")
        self.assertEqual(code, 1)
        self.assertIn("a run keeps one state slice", err)
        self.assertEqual(self.state()["stage"], "act1")

        flow_dir.rename(self.tmp / "moved")
        code, _, err = self.flow("-w", self.ws, "status")
        self.assertEqual(code, 1)
        self.assertIn("set `flow` in", err)

    def test_workspace_errors(self):
        self.init()
        code, _, err = self.flow("init", EXAMPLE, "-o", self.ws)
        self.assertEqual(code, 1)
        self.assertIn("not empty", err)
        code, _, err = self.flow("-w", self.tmp, "status")
        self.assertEqual(code, 1)
        self.assertIn("not a flow workspace", err)


if __name__ == "__main__":
    unittest.main()
