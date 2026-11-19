"""One run of a flow in its own workspace: the state, the mounts and the ``@flow`` cards."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tomllib
from importlib import resources
from pathlib import Path

from . import FlowError, definition, engine
from .definition import FLOW_ALIAS, Flow, Stage

STATE = Path(".flow/state.json")
FLOW_SLICE = Path(".flow/stage.slice")
SCRIPT_PRECEDENCE = 40  # below the state slice (mounted at 50 by bake), so baked cards win on shared paths
FLOW_PRECEDENCE = 60
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
WIKILINK = re.compile(r"\[\[([^\[\]|\n]+?)(\|[^\[\]\n]*)?\]\]")  # the engine's own link syntax


# -- workspace and state ----------------------------------------------------

def locate(arg: Path | None) -> Path:
    """The workspace: ``-w``, else ``$MINDSLICE_WORKSPACE``, else the nearest directory with mounts.toml."""
    if arg is None and os.environ.get("MINDSLICE_WORKSPACE"):
        arg = Path(os.environ["MINDSLICE_WORKSPACE"])
    if arg is None:
        here = Path.cwd().resolve()
        arg = next((d for d in (here, *here.parents) if (d / "mounts.toml").is_file()), here)
    ws = arg.resolve()
    if not (ws / STATE).is_file():
        raise FlowError(f"{ws} is not a flow workspace (no {STATE}); create one with `mindslice-flow init FLOW_DIR`, "
                        f"or pass it with -w")
    return ws


def load(ws: Path) -> tuple[dict, Flow]:
    """The state of the run and its flow definition, which is read again by every command."""
    state = json.loads((ws / STATE).read_text(encoding="utf-8"))
    root = Path(state["flow"])
    if not (root / definition.FILE).is_file():
        raise FlowError(f"the flow definition {root} is gone; if it moved, set `flow` in {ws / STATE} to its new path")
    flow = definition.load(root)
    if state.get("state", flow.state) != flow.state:
        raise FlowError(f"{root / definition.FILE} now has state = \"{flow.state}\", but this run bakes into "
                        f"@{state['state']}; a run keeps one state slice, so change it back")
    engine.version()
    return state, flow


def save_state(ws: Path, state: dict) -> None:
    path = ws / STATE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def state_version(ws: Path, flow: Flow, status: dict | None = None) -> str | None:
    """The version of the mounted state slice, or None before the first bake."""
    for m in (status or engine.status(ws))["mounts"]:
        if m["alias"] == f"@{flow.state}":
            meta = (ws / m["path"]) / "slice.toml"
            return tomllib.loads(meta.read_text(encoding="utf-8")).get("version") if meta.is_file() else None
    return None


# -- the @flow cards ----------------------------------------------------------

def _template(name: str) -> str:
    return resources.files("mindslice_flow").joinpath("cards", name).read_text(encoding="utf-8")


def fill(text: str, values: dict[str, str]) -> str:
    """Fill the ``{{key}}`` placeholders of a card; in its TOML frontmatter the values are escaped."""
    _, head, body = text.split("+++\n", 2)
    head = PLACEHOLDER.sub(lambda m: json.dumps(values[m[1]], ensure_ascii=False)[1:-1], head)
    return f"+++\n{head}+++\n{PLACEHOLDER.sub(lambda m: values[m[1]], body)}"


def qualify_links(body: str, alias: str) -> str:
    """Prefix the unqualified links of a card from ``@alias`` with it, so they still resolve elsewhere.

    The engine resolves ``[[x]]`` in the card's own mount and ``[[@alias/x]]`` in ``@alias``, by path and
    then by card alias in both cases, so the rewrite keeps every link's target.
    """
    def one(m: re.Match) -> str:
        ref = m[1].strip()
        return m[0] if ref.startswith("@") else f"[[@{alias}/{ref}{m[2] or ''}]]"
    return WIKILINK.sub(one, body)


def cards(ws: Path, flow: Flow, stage: Stage | None, done: list[str],
          recap: tuple[str, str] | None = None) -> dict[str, str]:
    """The files of the @flow slice for ``stage`` (``None``: the flow is finished).

    ``recap`` is (stage id, summary body) of the stage played last; the stage card carries it in full.
    """
    values = {
        "flow_name": flow.name, "total": str(len(flow.stages)), "state": flow.state,
        "flow_cmd": f"mindslice-flow -w {shlex.quote(str(ws))}",
        "engine_cmd": f"mindslice -w {shlex.quote(str(ws))}",
    }
    files = {"slice.toml": f'name = "flow"\nversion = "0.1.0"\nalias = "@{FLOW_ALIAS}"\n'
                           f'description = "mindslice-flow stage cards; rewritten on every stage change"\n'}
    if stage is None:
        files["stage.md"] = fill(_template("finished.md"), values)
        return files
    number = flow.index(stage.id) + 1
    last = number == len(flow.stages)
    earlier = list(dict.fromkeys(s for s in done if s != stage.id))
    if recap:
        older = [s for s in earlier if s != recap[0]]
        previous = f"上一幕（{recap[0]}）的摘要附在本卡末尾，承接剧情以它为准，不必再读取。" + (
            f"更早各幕的摘要在 @{flow.state}/summaries/ 下（{', '.join(older)}），需要时读取。" if older else "")
    elif earlier:
        previous = f"前面各幕的摘要在 @{flow.state}/summaries/ 下（{', '.join(earlier)}），需要承接剧情时读取。"
    else:
        previous = "这是本次运行的第一幕，还没有前情。"
    values |= {
        "stage_id": stage.id, "stage_title": stage.title, "number": str(number), "previous": previous,
        "recap": (f"\n## 上一幕摘要（@{flow.state}/summaries/{recap[0]}）\n\n"
                  f"{qualify_links(recap[1].strip(), flow.state)}\n" if recap else ""),
        "after": ("这是最后一幕，next 会把流程标记为结束。" if last else
                  "建议用户开一个新会话进入下一幕；若在当前会话继续，先调用 manifest 重新加载常驻卡，"
                  "此前的导演卡和本卡随之作废。"),
    }
    files["stage.md"] = fill(_template("stage.md"), values)
    files["settlement.md"] = fill(_template("settlement.md"), values)
    return files


def last_summary(ws: Path, flow: Flow, stage: Stage | None, done: list[str]) -> tuple[str, str] | None:
    """(stage id, body) of the baked summary of the stage played last, if there is one."""
    if stage is None:
        return None
    played = [s for s in done if s != stage.id]
    if not played:
        return None
    card = engine.read(ws, f"@{flow.state}/summaries/{played[-1]}")
    return (played[-1], card["body"]) if card else None


def render(ws: Path, flow: Flow, stage: Stage | None, done: list[str]) -> None:
    """Rewrite the @flow slice directory; files it no longer has are removed."""
    root = ws / FLOW_SLICE
    root.mkdir(parents=True, exist_ok=True)
    files = cards(ws, flow, stage, done, last_summary(ws, flow, stage, done))
    for old in root.iterdir():
        if old.is_file() and old.name not in files:
            old.unlink()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")


# -- mounts -------------------------------------------------------------------

def sync(ws: Path, flow: Flow, stage: Stage | None) -> tuple[list[str], list[str]]:
    """Mount exactly the slices of ``stage``, plus @overlay, the state slice and @flow.

    Mounts are compared by directory, and mounted under the alias the definition was checked with.
    Returns (mounted, unmounted).
    """
    want = flow.mounts(stage)
    keep = {"@overlay", f"@{flow.state}", f"@{FLOW_ALIAS}"}
    mounts = engine.status(ws)["mounts"]
    have, removed = set(), []
    for m in mounts:
        if m["alias"] in keep:
            continue
        path = (ws / m["path"]).resolve()
        if path in want:
            have.add(path)
        else:
            engine.unmount(ws, m["alias"])
            removed.append(m["alias"])
    added = [engine.mount(ws, path, SCRIPT_PRECEDENCE, alias=definition.alias(path))
             for path in want if path not in have]
    if not any(m["alias"] == f"@{FLOW_ALIAS}" for m in mounts):
        added.append(engine.mount(ws, ws / FLOW_SLICE, FLOW_PRECEDENCE, alias=FLOW_ALIAS))
    return added, removed


def enter(ws: Path, flow: Flow, stage: Stage | None, done: list[str]) -> tuple[list[str], list[str]]:
    render(ws, flow, stage, done)
    return sync(ws, flow, stage)


# -- commands -----------------------------------------------------------------

def init(flow_dir: Path, ws: Path) -> None:
    flow = definition.load(flow_dir)
    engine.version()
    ws = ws.resolve()
    if ws.exists() and any(ws.iterdir()):
        raise FlowError(f"{ws} already exists and is not empty; every run needs its own workspace")
    for warning in definition.warnings(flow):
        print(f"warning: {warning}")
    first = flow.stages[0]
    try:
        engine.init(ws)
        added, _ = enter(ws, flow, first, [])
        save_state(ws, {"flow": str(flow.root), "state": flow.state, "stage": first.id, "done": []})
    except BaseException:
        shutil.rmtree(ws, ignore_errors=True)  # it was empty before, and a half-made workspace cannot be resumed
        raise
    print(f"created {ws} for 「{flow.name}」 ({len(flow.stages)} stages)")
    print(f"stage 1: {first.title} ({first.id}); mounted {' '.join(added)}")
    print("\nPoint your harness's MindSlice at this workspace, for example:")
    if os.name == "nt":  # `export` is POSIX shell only; PowerShell is the default shell on Windows
        quoted = "'" + str(ws).replace("'", "''") + "'"
        print(f"  $env:MINDSLICE_WORKSPACE = {quoted}   (PowerShell)")
        print(f"  set \"MINDSLICE_WORKSPACE={ws}\"   (cmd)")
    print(f"  export MINDSLICE_WORKSPACE={shlex.quote(str(ws))}" + ("   (Git Bash)" if os.name == "nt" else ""))
    print(f"  (MCP server command: mindslice -w {shlex.quote(str(ws))} mcp)")
    print("then start a new session and play.")


def overlay_cards(ws: Path) -> list[str]:
    """The paths of the @overlay cards, which the next bake moves into the state slice."""
    return sorted(p.relative_to(ws / "overlay").with_suffix("").as_posix() for p in (ws / "overlay").rglob("*.md"))


def check_settlement(ws: Path, stage: str | None) -> None:
    """What must hold before the overlay is baked: the stage summary exists, and no overlay card has an error."""
    if stage is not None and f"summaries/{stage}" not in overlay_cards(ws):
        raise FlowError(f"@overlay has no stage summary `summaries/{stage}`; write it before `next` "
                        f"(step 3 of @flow/settlement)")
    errors = [e for e in engine.validate(ws)["errors"] if str(e.get("address", "")).startswith("@overlay/")]
    if errors:
        lines = [f"  - {e['address']} [{e['code']}]: {e['message']}" for e in errors]
        raise FlowError("these @overlay cards have errors; fix them before they are baked:\n" + "\n".join(lines))


def status(ws: Path) -> None:
    state, flow = load(ws)
    st = engine.status(ws)
    versions = {d["stage"]: d["version"] for d in state["done"]}
    print(f"「{flow.name}」  {flow.root}")
    print(f"workspace: {ws}")
    for stage in flow.stages:
        current = stage.id == state["stage"]
        mark = "▶" if current else ("✓" if stage.id in versions else " ")
        baked = f"  → @{flow.state} {versions[stage.id]}" if versions.get(stage.id) and not current else ""
        print(f"  {mark} {stage.id}  {stage.title}{baked}")
    if state["stage"] is None:
        print("the flow is finished")
    print(f"mounted: {' '.join(m['alias'] for m in st['mounts'])}")
    overlay = overlay_cards(ws)
    print(f"overlay cards to settle ({len(overlay)}): {', '.join(overlay) or '-'}")
    pending = st["pending_review"]["files"]
    print(f"pending review ({len(pending)}): {', '.join(f['address'] for f in pending) or '-'}")
    if pending:
        print(f"  decide with `mindslice -w {shlex.quote(str(ws))} review` (or apply / discard) before `next`")


def advance(ws: Path, to: str | None = None) -> None:
    """Settle the current stage and enter the next one (or ``to``). Safe to run again after an interruption:
    the bake is skipped once the overlay is empty, and the state is written last. A non-empty overlay is
    baked only with the stage summary in it and no card errors."""
    state, flow = load(ws)
    current = state["stage"]
    if to is not None:
        target = flow.stages[flow.index(to)]
    elif current is None:
        raise FlowError("the flow is finished; use `next --to STAGE` to play a stage again")
    else:
        i = flow.index(current)
        target = flow.stages[i + 1] if i + 1 < len(flow.stages) else None

    st = engine.status(ws)
    pending = st["pending_review"]["files"]
    if pending:
        lines = [f"  - {f['address']}: {f.get('requires_approval') or f.get('change')}" for f in pending]
        raise FlowError("changes are waiting for review; the user decides on them first with "
                        f"`mindslice -w {shlex.quote(str(ws))} review` (or apply / discard):\n" + "\n".join(lines))
    baked = None
    if st["memory_state"]["saved_overlay_cards"]:
        check_settlement(ws, current)
        baked = engine.bake(ws, flow.state)

    done = [d["stage"] for d in state["done"]] + ([current] if current else [])
    added, removed = enter(ws, flow, target, done)
    version = baked["version"] if baked else state_version(ws, flow)
    if current:
        state["done"].append({"stage": current, "version": version})
    state["stage"] = target.id if target else None
    save_state(ws, state)

    if baked:
        print(f"baked {len(baked['cards'])} cards into @{flow.state} {baked['version']}")
    else:
        print("nothing in @overlay to bake")
    if removed:
        print(f"unmounted {' '.join(removed)}")
    if added:
        print(f"mounted {' '.join(added)}")
    if target is None:
        print(f"「{flow.name}」 is finished")
    else:
        print(f"now in stage {flow.index(target.id) + 1}/{len(flow.stages)}: {target.title} ({target.id})")
        print("start a new session for it (or call manifest again to continue in this one)")


def check(flow_dir: Path) -> None:
    flow = definition.load(flow_dir)
    for warning in definition.warnings(flow):
        print(f"warning: {warning}")
    print(f"「{flow.name}」: {len(flow.stages)} stages ({', '.join(flow.ids())}), state slice @{flow.state}")
