"""One run of a flow in its own workspace: the state, the route, the mounts and the ``@flow`` cards.

The state (``.flow/state.json``) holds the route so far (``done``: each stage played, in order, with the
version of the state slice it left), the current ``stage``, the stages it reuses as material (``reuse``),
and the side stages the agent wrote during the run (``generated``), each baked into a slice of its own.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import shutil
import tomllib
from importlib import resources
from pathlib import Path

from . import FlowError, definition, engine
from .definition import FLOW_ALIAS, NAME, RESERVED, Flow, Stage

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
    state.setdefault("reuse", [])
    state.setdefault("generated", {})
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


def mounted(status: dict, alias: str) -> Path | None:
    """The directory mounted as ``@alias`` (as the engine gives it, often relative to the workspace)."""
    return next((Path(m["path"]) for m in status["mounts"] if m["alias"] == f"@{alias}"), None)


def state_version(ws: Path, flow: Flow, status: dict | None = None) -> str | None:
    """The version of the mounted state slice, or None before the first bake."""
    path = mounted(status or engine.status(ws), flow.state)
    meta = ws / path / "slice.toml" if path else None
    return tomllib.loads(meta.read_text(encoding="utf-8")).get("version") if meta and meta.is_file() else None


# -- stages and the route -----------------------------------------------------

def stage_of(ws: Path, flow: Flow, state: dict, sid: str) -> Stage:
    """A stage of the flow, or one written in this run."""
    gen = state["generated"].get(sid)
    if gen:
        return Stage(sid, gen["title"], ((ws / gen["slice"]).resolve(),), side=True, generated=True)
    if sid not in flow.ids():
        raise FlowError(f"no stage {sid!r}; stages: {', '.join([*flow.ids(), *state['generated']])}")
    return flow.stages[flow.index(sid)]


def route(state: dict) -> list[str]:
    """The stages played so far, in order, then the current one."""
    return [d["stage"] for d in state["done"]] + ([state["stage"]] if state["stage"] else [])


def main_position(flow: Flow, played: list[str]) -> int:
    """The main-line index of the main-line stage played last (-1 if none was)."""
    return next((i for i in map(flow.main_index, reversed(played)) if i is not None), -1)


def next_main(flow: Flow, played: list[str]) -> Stage | None:
    """The main-line stage after the one played last on the main line, or None after the last one."""
    main, i = flow.main(), main_position(flow, played) + 1
    return main[i] if i < len(main) else None


def heading(flow: Flow, state: dict, stage: Stage | None) -> Stage | None:
    """Where the story is going on the main line when it is in ``stage``: the stage itself on the main line;
    from a side stage, the main-line stage a plain ``next`` enters after it."""
    if stage is None or not stage.side:
        return stage
    gen = state["generated"].get(stage.id)
    if gen is not None:
        return flow.stages[flow.index(gen["rejoin"])] if gen["rejoin"] else None
    return next_main(flow, route(state))


def default_next(ws: Path, flow: Flow, state: dict) -> Stage | None:
    """The stage a plain ``next`` enters from the current one; None ends the flow."""
    current = stage_of(ws, flow, state, state["stage"])
    return heading(flow, state, current) if current.side else next_main(flow, route(state))


def reusable(flow: Flow, played: list[str], goal: Stage | None) -> list[str]:
    """The stages a ``--reuse`` may name: those played already, and the main line up to ``goal``."""
    last = flow.main_index(goal.id) if goal else main_position(flow, played)
    return list(dict.fromkeys([*played, *(s.id for s in flow.main()[:last + 1])]))


def wanted(flow: Flow, stage: Stage | None, reuse: list[Stage]) -> list[Path]:
    """Every slice directory to mount in ``stage``, each once."""
    return list(dict.fromkeys([*flow.mounts(stage), *(p for s in reuse for p in s.mount)]))


def material(flow: Flow, stage: Stage, reuse: list[Stage]) -> list[Path]:
    """The reused slice directories that are not ``stage``'s own."""
    own = set(flow.mounts(stage))
    return [p for p in dict.fromkeys(p for s in reuse for p in s.mount) if p not in own]


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


def text(summary: str, body: str) -> str:
    """A card's summary and body, as one text."""
    return "\n\n".join(part.strip() for part in (summary, body) if part.strip())


def card_text(path: Path) -> str | None:
    """The summary and body of a card file (the summary may be in its frontmatter)."""
    if not path.is_file():
        return None
    parts = path.read_text(encoding="utf-8").split("+++\n", 2)
    if len(parts) != 3:
        return None
    try:
        summary = tomllib.loads(parts[1]).get("summary", "")
    except tomllib.TOMLDecodeError:
        summary = ""
    return text(summary if isinstance(summary, str) else "", parts[2])


def position(flow: Flow, stage: Stage) -> str:
    if stage.generated:
        return "支线（本局生成）"
    if stage.side:
        return "支线"
    return f"主线第 {flow.main_index(stage.id) + 1} 幕（共 {len(flow.main())} 幕）"


def label(stage: Stage) -> str:
    return f"支线「{stage.title}」" if stage.side else f"「{stage.title}」"


def premise(stage: Stage) -> str:
    return f"（入口条件：{stage.premise}）" if stage.premise else ""


def routes(ws: Path, flow: Flow, state: dict) -> str:
    """The settlement card's list of the ways on from the current stage (``next …`` stands for the full
    command, which the card gives once)."""
    played = route(state)
    default, main_next = default_next(ws, flow, state), next_main(flow, played)
    lines = [f"- 默认去向{label(default)}{premise(default)}：`next`" if default else "- 结束整个流程：`next`"]
    later = [s for s in flow.main()[main_position(flow, played) + 1:] if default is None or s.id != default.id]
    if later:
        lines.append("- 剧情已经满足主线后面某一幕的入口条件：`next --to <id>`，可选 "
                     + "；".join(f"`{s.id}`「{s.title}」{premise(s)}" for s in later))
    sides = [s for s in flow.stages if s.side]
    if sides:
        lines.append("- 进入剧本预设的支线：`next --to <id>`，可选 "
                     + "；".join(f"`{s.id}`「{s.title}」{premise(s)}" for s in sides))
    lines.append(
        "- 新编一幕支线（各幕的入口条件都不成立，或玩家走到了剧本之外）：先写导演方案 `stages/<新 id>/plan`"
        "（起因、目标、主要冲突、终止条件、要把剧情引回主线哪一幕）。新的一幕没有别的导演卡，所以当前导演卡里"
        "适用于全剧的主持规则（如回复篇幅、节奏、不提幕后操作）也要写进方案。本幕新增的人物、地点、伏笔、秘密写成 "
        "`stages/<新 id>/` 下的卡。卡片路径会保留这个文件夹，所以这些卡之间的链接也写全路径（如 "
        f"`[[stages/<新 id>/npcs/某人]]`），链接别处的卡写全地址（如 `[[@{flow.state}/player/status]]`）；"
        "然后运行 `next --new <新 id> --title \"<标题>\" [--rejoin <主线 id>]`"
        f"（不写 --rejoin 时回到{label(main_next) if main_next else '流程结束'}；标题不必带「支线」二字）。"
        f"这些卡会 bake 成只属于本局的 slice `@<新 id>`，其余的卡照常进 @{flow.state}。")
    lines.append(
        "- 任一走向都可以加 `--reuse <id>`（可重复），把剧情要用到的旧场景、旧人物所在的幕一并挂上作素材："
        f"已经走过的幕（{', '.join(dict.fromkeys(played))}），或主线上不晚于目标幕（新编支线则为回归的那一幕）的幕。")
    return "\n".join(f"   {line}" for line in lines)


def reused_card(flow: Flow, stage: Stage, reuse: list[Stage]) -> str | None:
    """The @flow card that stands in for the director cards of the reused slices; None if they have none.

    It supersedes them, so the boot set, ``read`` and ``search`` all give it in their place.
    """
    dirs = material(flow, stage, reuse)
    hidden = [f"@{definition.alias(p)}/{c}" for p in dirs for c in definition.always_loaded(p)]
    if not hidden:
        return None
    names = "、".join(f"@{definition.alias(p)}" for p in dirs)
    return ("+++\n"
            f'title = "复用的素材：{names}"\n'
            'summary = "这些 slice 在本幕只作素材，它们的导演卡在本幕不适用"\n'
            f"supersedes = {json.dumps(hidden, ensure_ascii=False)}\n"
            "+++\n\n"
            f"本幕复用了 {names} 作素材：其中的人物、地点、物品和秘密照常可用，但它们的导演卡在本幕不适用，"
            "照 [[@flow/stage]] 和本幕的导演卡（或方案）主持。\n")


def cards(ws: Path, flow: Flow, state: dict, recap: tuple[str, str] | None = None,
          plan: str | None = None) -> dict[str, str]:
    """The files of the @flow slice for the state's current stage (none: the flow is finished).

    ``recap`` is (stage id, summary body) of the stage played last, and ``plan`` the plan of a stage
    written in this run; the stage card carries both in full.
    """
    cmd = f"mindslice-flow -w {shlex.quote(str(ws))}"
    values = {
        "flow_name": flow.name, "total": str(len(flow.main())), "state": flow.state, "flow_cmd": cmd,
        "engine_cmd": f"mindslice -w {shlex.quote(str(ws))}",
    }
    files = {"slice.toml": f'name = "flow"\nversion = "0.1.0"\nalias = "@{FLOW_ALIAS}"\n'
                           f'description = "mindslice-flow stage cards; rewritten on every stage change"\n'}
    if state["stage"] is None:
        files["stage.md"] = fill(_template("finished.md"), values)
        return files
    stage = stage_of(ws, flow, state, state["stage"])
    reuse = [stage_of(ws, flow, state, r) for r in state["reuse"]]
    played = [d["stage"] for d in state["done"]]
    adapt = bool(played) and not stage.generated

    earlier = list(dict.fromkeys(s for s in played if s != stage.id))
    if recap:
        older = [s for s in earlier if s != recap[0]]
        previous = f"上一幕（{recap[0]}）的摘要附在本卡末尾，承接剧情以它为准，不必再读取。" + (
            f"更早各幕的摘要在 @{flow.state}/summaries/ 下（{', '.join(older)}），需要时读取。" if older else "")
    elif earlier:
        previous = f"前面各幕的摘要在 @{flow.state}/summaries/ 下（{', '.join(earlier)}），需要承接剧情时读取。"
    else:
        previous = "这是本次运行的第一幕，还没有前情。"

    notes = []
    if stage.premise:
        notes.append(f"剧本为本幕预设的入口条件是「{stage.premise}」；存档与它不符的地方，以存档为准。")
    if stage.side:
        goal = heading(flow, state, stage)
        notes.append(f"本支线结束后默认回到主线{label(goal)}{premise(goal)}，把剧情往那里引。" if goal else
                     "本支线结束后默认结束整个流程。")
    dirs = material(flow, stage, reuse)
    if dirs:
        notes.append(f"本幕还挂载了 {'、'.join(f'@{definition.alias(p)}' for p in dirs)} 作素材：其中的人物、地点、"
                     "物品照常可用，它们的导演卡在本幕不适用。")
    if adapt:
        notes.append(f"开场前先对照存档和本幕剧本，写改编卡 `stages/{stage.id}/adaptation`：剧本里哪些设定已被这一局"
                     "改写（人物处境、物品去向、关系等），本幕因此怎么改；没有改动也写明。结算时没有这张卡，next 会拒绝。")
    trail = [label(stage_of(ws, flow, state, s)) for s in played]

    values |= {
        "stage_id": stage.id, "stage_title": stage.title, "position": position(flow, stage),
        "guide": "导演方案" if stage.generated else "导演卡",
        "trail": f"已走过的路线：{' → '.join([*trail, '本幕'])}。" if trail else "",
        "director": (f"本幕不在预设剧本里，是上一幕结算时按玩家的走向新编的：导演方案附在本卡末尾，本幕新增的素材在 "
                     f"@{stage.id} 下；照方案主持本幕。" if stage.generated else
                     "本幕的导演卡同样是常驻卡，写着本幕的目标和终止条件；照它主持本幕。"),
        "notes": "".join(f"- {n}\n" for n in notes),
        "previous": previous,
        "plan": (f"\n## 本幕导演方案（@{stage.id}/stages/{stage.id}/plan）\n\n{qualify_links(plan, stage.id)}\n"
                 if plan else ""),
        "recap": (f"\n## 上一幕摘要（@{flow.state}/summaries/{recap[0]}）\n\n"
                  f"{qualify_links(recap[1].strip(), flow.state)}\n" if recap else ""),
        "routes": routes(ws, flow, state),
        "required": ("没有这张卡" + (f"、没有本幕的改编卡 `stages/{stage.id}/adaptation`" if adapt else "")
                     + "，或 @overlay 的卡片有错误时，next 会拒绝。"),
    }
    files["stage.md"] = fill(_template("stage.md"), values)
    files["settlement.md"] = fill(_template("settlement.md"), values)
    reused = reused_card(flow, stage, reuse)
    if reused:
        files["reused.md"] = reused
    return files


def last_summary(ws: Path, flow: Flow, state: dict) -> tuple[str, str] | None:
    """(stage id, body) of the baked summary of the stage played last, if there is one."""
    played = [d["stage"] for d in state["done"] if d["stage"] != state["stage"]]
    if state["stage"] is None or not played:
        return None
    card = engine.read(ws, f"@{flow.state}/summaries/{played[-1]}")
    return (played[-1], text(card.get("summary", ""), card.get("body", ""))) if card else None


def render(ws: Path, flow: Flow, state: dict) -> None:
    """Rewrite the @flow slice directory; files it no longer has are removed."""
    root = ws / FLOW_SLICE
    root.mkdir(parents=True, exist_ok=True)
    sid, plan = state["stage"], None
    if sid in state["generated"]:
        plan = card_text(ws / state["generated"][sid]["slice"] / "stages" / sid / "plan.md")
    files = cards(ws, flow, state, last_summary(ws, flow, state), plan)
    for old in root.iterdir():
        if old.is_file() and old.name not in files:
            old.unlink()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")


# -- mounts -------------------------------------------------------------------

def sync(ws: Path, flow: Flow, want: list[Path]) -> tuple[list[str], list[str]]:
    """Mount exactly the directories ``want``, plus @overlay, the state slice and @flow.

    Mounts are compared by directory, and mounted under the alias the definition was checked with.
    Returns (mounted, unmounted).
    """
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


def enter(ws: Path, flow: Flow, state: dict) -> tuple[list[str], list[str]]:
    """Render the @flow cards for the state's current stage and mount its slices."""
    stage = stage_of(ws, flow, state, state["stage"]) if state["stage"] else None
    reuse = [stage_of(ws, flow, state, r) for r in state["reuse"]]
    render(ws, flow, state)
    return sync(ws, flow, wanted(flow, stage, reuse))


# -- commands -----------------------------------------------------------------

def init(flow_dir: Path, ws: Path) -> None:
    flow = definition.load(flow_dir)
    engine.version()
    ws = ws.resolve()
    if ws.exists() and any(ws.iterdir()):
        raise FlowError(f"{ws} already exists and is not empty; every run needs its own workspace")
    for warning in definition.warnings(flow):
        print(f"warning: {warning}")
    first = flow.main()[0]
    state = {"flow": str(flow.root), "state": flow.state, "stage": first.id, "done": [], "reuse": [],
             "generated": {}}
    try:
        engine.init(ws)
        added, _ = enter(ws, flow, state)
        save_state(ws, state)
    except BaseException:
        shutil.rmtree(ws, ignore_errors=True)  # it was empty before, and a half-made workspace cannot be resumed
        raise
    print(f"created {ws} for 「{flow.name}」 ({len(flow.main())} main-line stages)")
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


def check_settlement(ws: Path, stage: str | None, adaptation: bool = False) -> None:
    """What must hold before the overlay is baked: the stage summary exists (and the stage's adaptation card,
    when it needs one), and no overlay card has an error."""
    have = overlay_cards(ws)
    if stage is not None and f"summaries/{stage}" not in have:
        raise FlowError(f"@overlay has no stage summary `summaries/{stage}`; write it before `next` "
                        f"(step 4 of @flow/settlement)")
    if adaptation and f"stages/{stage}/adaptation" not in have:
        raise FlowError(f"@overlay has no adaptation card `stages/{stage}/adaptation`; write it before `next`: "
                        f"what this run has changed in the stage's script (see @flow/stage)")
    errors = [e for e in engine.validate(ws)["errors"] if str(e.get("address", "")).startswith("@overlay/")]
    if errors:
        lines = [f"  - {e['address']} [{e['code']}]: {e['message']}" for e in errors]
        raise FlowError("these @overlay cards have errors; fix them before they are baked:\n" + "\n".join(lines))


def misplaced_links(ws: Path, sid: str) -> list[tuple[str, str]]:
    """(card, link) for each link in the overlay cards under ``stages/<sid>/`` that names one of those cards
    relative to the folder: card paths keep the folder when baked, so such a link would not resolve."""
    root, base = ws / "overlay", ws / "overlay" / "stages" / sid
    out = []
    for card in sorted(base.rglob("*.md")):
        for m in WIKILINK.finditer(card.read_text(encoding="utf-8")):
            ref = m[1].strip()
            if not ref.startswith("@") and (base / f"{ref}.md").is_file() and not (root / f"{ref}.md").is_file():
                out.append((card.relative_to(root).with_suffix("").as_posix(), ref))
    return out


def check_new(flow: Flow, state: dict, sid: str) -> None:
    """A new stage's id must be free: no stage of the flow or of this run, no alias already taken."""
    if not NAME.match(sid):
        raise FlowError(f"--new {sid!r}: an id is letters, digits, '-' or '_'")
    if sid in flow.ids():
        raise FlowError(f"--new {sid}: the script already has stage {sid}; enter it with `next --to {sid}`")
    if sid in state["generated"]:
        raise FlowError(f"--new {sid}: this run already wrote stage {sid}; play it again with `next --to {sid}`")
    taken = {definition.alias(p) for s in flow.stages for p in flow.mounts(s)} | RESERVED | {flow.state}
    if sid in taken:
        raise FlowError(f"--new {sid}: @{sid} is reserved or already a slice of the flow; choose another id")


def status(ws: Path) -> None:
    state, flow = load(ws)
    st = engine.status(ws)
    versions = {d["stage"]: d["version"] for d in state["done"]}

    def line(stage: Stage, note: str = "") -> str:
        current = stage.id == state["stage"]
        mark = "▶" if current else ("✓" if stage.id in versions else " ")
        baked = f"  → @{flow.state} {versions[stage.id]}" if versions.get(stage.id) and not current else ""
        return f"  {mark} {stage.id}  {stage.title}{note}{baked}"

    print(f"「{flow.name}」  {flow.root}")
    print(f"workspace: {ws}")
    print("main line:")
    for stage in flow.main():
        print(line(stage))
    sides = [s for s in flow.stages if s.side] + [stage_of(ws, flow, state, s) for s in state["generated"]]
    if sides:
        print("side stages:")
        for stage in sides:
            goal = heading(flow, state, stage) if stage.generated else None
            note = (f"  (written in this run, rejoins {goal.id if goal else 'nothing: the flow ends'})"
                    if stage.generated else "")
            print(line(stage, note))
    played = route(state)
    if played:
        print(f"route: {' → '.join(played)}")
    if state["stage"] is None:
        print("the flow is finished")
    if state["reuse"]:
        print(f"reusing as material: {', '.join(state['reuse'])}")
    print(f"mounted: {' '.join(m['alias'] for m in st['mounts'])}")
    overlay = overlay_cards(ws)
    print(f"overlay cards to settle ({len(overlay)}): {', '.join(overlay) or '-'}")
    pending = st["pending_review"]["files"]
    print(f"pending review ({len(pending)}): {', '.join(f['address'] for f in pending) or '-'}")
    if pending:
        print(f"  decide with `mindslice -w {shlex.quote(str(ws))} review` (or apply / discard) before `next`")


def advance(ws: Path, to: str | None = None, new: str | None = None, title: str | None = None,
            rejoin: str | None = None, reuse: list[str] | tuple[str, ...] = ()) -> None:
    """Settle the current stage and enter the next one: the default way on, ``to``, or a ``new`` stage the
    agent wrote under ``stages/<new>/``, which is baked into a slice of its own first.

    Every check runs before anything changes. Safe to run again after an interruption: each bake is
    skipped once its cards have left the overlay, and the state is written last.
    """
    state, flow = load(ws)
    current = state["stage"]
    played = route(state)
    if (title is not None or rejoin is not None) and new is None:
        raise FlowError("--title and --rejoin go with --new")
    if current is None and to is None:
        raise FlowError("the flow is finished; use `next --to STAGE` to play a stage again")

    if new is not None:
        check_new(flow, state, new)
        goal = stage_of(ws, flow, state, rejoin) if rejoin is not None else next_main(flow, played)
        if goal is not None and goal.side:
            raise FlowError(f"--rejoin {rejoin}: a new stage rejoins the main line, and {rejoin} is a side stage")
        target = Stage(new, (title or "").strip() or new, (), side=True, generated=True)
    else:
        target = stage_of(ws, flow, state, to) if to is not None else default_next(ws, flow, state)
        goal = heading(flow, state, target)
    allowed = reusable(flow, played, goal)
    for r in reuse:
        if r not in allowed:
            raise FlowError(f"--reuse {r}: only the stages already played and the main line up to "
                            f"{goal.id if goal else 'the stage played last'} can be reused here: {', '.join(allowed)}")
    reuse = [r for r in dict.fromkeys(reuse) if target is not None and r != target.id]

    st = engine.status(ws)
    pending = st["pending_review"]["files"]
    if pending:
        lines = [f"  - {f['address']}: {f.get('requires_approval') or f.get('change')}" for f in pending]
        raise FlowError("changes are waiting for review; the user decides on them first with "
                        f"`mindslice -w {shlex.quote(str(ws))} review` (or apply / discard):\n" + "\n".join(lines))
    overlay = overlay_cards(ws)
    prefix = f"stages/{new}/"
    if new is not None:
        before = mounted(st, new)  # baked already by an interrupted run of this command
        if f"{prefix}plan" not in overlay and not (before and (ws / before / prefix / "plan.md").is_file()):
            raise FlowError(f"@overlay has no plan `{prefix}plan` for the new stage; write it, and the stage's new "
                            f"material under {prefix}, before `next --new` (step 3 of @flow/settlement)")
        relative = misplaced_links(ws, new)
        if relative:
            lines = [f"  - @overlay/{card}: [[{ref}]] → [[{prefix}{ref}]]" for card, ref in relative]
            raise FlowError(f"these links between the new stage's cards leave out the folder {prefix}, which the "
                            f"card paths keep, so they would not resolve; write them in full:\n" + "\n".join(lines))
    if overlay:
        check_settlement(ws, current, adaptation=bool(state["done"]) and current not in state["generated"])

    side = None
    if new is not None:
        if any(c.startswith(prefix) for c in overlay):
            side = engine.bake(ws, new, prefix)
        path = mounted(engine.status(ws), new)
        target = dataclasses.replace(target, mount=((ws / path).resolve(),))
        state["generated"][new] = {"title": target.title, "slice": path.as_posix(),
                                   "rejoin": goal.id if goal else None}
    baked = engine.bake(ws, flow.state) if overlay_cards(ws) else None

    version = baked["version"] if baked else state_version(ws, flow)
    if current:
        state["done"].append({"stage": current, "version": version})
    state["stage"] = target.id if target else None
    state["reuse"] = reuse
    added, removed = enter(ws, flow, state)
    save_state(ws, state)

    if side:
        print(f"baked {len(side['cards'])} cards into @{new} {side['version']}, the new stage's own slice")
    if baked:
        print(f"baked {len(baked['cards'])} cards into @{flow.state} {baked['version']}")
    elif not side:
        print("nothing in @overlay to bake")
    if removed:
        print(f"unmounted {' '.join(removed)}")
    if added:
        print(f"mounted {' '.join(added)}")
    if target is None:
        print(f"「{flow.name}」 is finished")
        return
    if not target.side:
        print(f"now in main-line stage {flow.main_index(target.id) + 1}/{len(flow.main())}: {target.title} ({target.id})")
    else:
        kind = "side stage written in this run" if target.generated else "side stage"
        print(f"now in {kind}: {target.title} ({target.id}); a plain `next` from it goes to "
              f"{goal.id if goal else 'the end of the flow'}")
    print("start a new session for it (or call manifest again to continue in this one)")


def check(flow_dir: Path) -> None:
    flow = definition.load(flow_dir)
    for warning in definition.warnings(flow):
        print(f"warning: {warning}")
    sides = [s.id for s in flow.stages if s.side]
    print(f"「{flow.name}」: {len(flow.main())} main-line stages ({', '.join(s.id for s in flow.main())})"
          + (f", side stages ({', '.join(sides)})" if sides else "") + f", state slice @{flow.state}")
