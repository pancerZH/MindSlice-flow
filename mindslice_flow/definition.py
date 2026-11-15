"""The flow definition: ``flow.toml`` and the slice directories it names."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import FlowError

FILE = "flow.toml"
FLOW_ALIAS = "flow"  # the slice the flow renders its own cards into
RESERVED = {"overlay", FLOW_ALIAS}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    mount: tuple[Path, ...]


@dataclass(frozen=True)
class Flow:
    root: Path
    name: str
    state: str  # alias of the slice every stage is baked into, without @
    mount: tuple[Path, ...]  # mounted in every stage
    stages: tuple[Stage, ...]

    def index(self, stage_id: str) -> int:
        for i, stage in enumerate(self.stages):
            if stage.id == stage_id:
                return i
        raise FlowError(f"no stage {stage_id!r} in {self.root / FILE}; stages: {', '.join(self.ids())}")

    def ids(self) -> list[str]:
        return [stage.id for stage in self.stages]

    def mounts(self, stage: Stage | None) -> list[Path]:
        """Every slice directory to mount in ``stage`` (only the common ones once the flow is finished)."""
        return [*self.mount, *(stage.mount if stage else ())]


def alias(slice_dir: Path) -> str:
    """The alias the engine mounts a directory under: ``alias`` in its slice.toml, else the directory name."""
    meta = slice_dir / "slice.toml"
    if meta.is_file():
        try:
            value = tomllib.loads(meta.read_text(encoding="utf-8")).get("alias")
        except tomllib.TOMLDecodeError as error:
            raise FlowError(f"{meta}: {error}") from None
        if isinstance(value, str) and value.strip("@"):
            return value.strip().lstrip("@")
    return slice_dir.name.removesuffix(".slice")


def _paths(root: Path, value: object, where: str) -> tuple[Path, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise FlowError(f"{where}: `mount` must be a list of paths")
    paths = tuple((root / v).resolve() for v in value)
    for path in paths:
        if not path.is_dir():
            raise FlowError(f"{where}: slice directory {path} does not exist")
    return paths


def load(flow_dir: Path) -> Flow:
    """Read and check ``flow_dir/flow.toml``; every problem that would break a run is an error."""
    root = flow_dir.resolve()
    file = root / FILE
    if not file.is_file():
        raise FlowError(f"{file} does not exist")
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise FlowError(f"{file}: {error}") from None

    name = data.get("name", root.name)
    state = data.get("state", "story")
    if not isinstance(name, str) or not name.strip():
        raise FlowError(f"{file}: `name` must be a non-empty string")
    if not isinstance(state, str) or not NAME.match(state.lstrip("@")) or state.lstrip("@") in RESERVED:
        raise FlowError(f"{file}: `state` must be a slice alias such as \"story\", other than "
                        f"{', '.join(sorted(RESERVED))}")
    state = state.lstrip("@")
    common = _paths(root, data.get("mount", []), f"{file}")

    raw = data.get("stage", [])
    if not isinstance(raw, list) or not raw:
        raise FlowError(f"{file}: define at least one [[stage]]")
    stages, seen = [], set()
    for n, item in enumerate(raw, 1):
        where = f"{file}, stage {n}"
        sid = item.get("id")
        if not isinstance(sid, str) or not NAME.match(sid):
            raise FlowError(f"{where}: `id` must be letters, digits, '-' or '_'")
        if sid in seen:
            raise FlowError(f"{where}: stage id {sid!r} is used twice")
        seen.add(sid)
        title = item.get("title", sid)
        if not isinstance(title, str):
            raise FlowError(f"{where}: `title` must be a string")
        stages.append(Stage(sid, title, _paths(root, item.get("mount", []), where)))

    flow = Flow(root, name.strip(), state, common, tuple(stages))
    for stage in flow.stages:
        taken: dict[str, Path] = {}
        for path in flow.mounts(stage):
            a = alias(path)
            if a in RESERVED or a == state:
                raise FlowError(f"stage {stage.id}: {path} would mount as @{a}, which the flow reserves; "
                                f"set another `alias` in its slice.toml")
            if a in taken and taken[a] != path:
                raise FlowError(f"stage {stage.id}: {taken[a]} and {path} would both mount as @{a}")
            taken[a] = path
    return flow


def always_loaded(slice_dir: Path) -> list[str]:
    """The cards in a slice directory whose frontmatter says ``load = "always"``."""
    found = []
    for card in sorted(slice_dir.rglob("*.md")):
        lines = card.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines or lines[0] != "+++" or "+++" not in lines[1:]:
            continue
        try:
            meta = tomllib.loads("\n".join(lines[1:lines.index("+++", 1)]))
        except tomllib.TOMLDecodeError:
            continue
        if meta.get("load") == "always":
            found.append(card.relative_to(slice_dir).with_suffix("").as_posix())
    return found


def warnings(flow: Flow) -> list[str]:
    """Problems that do not stop a run: a stage without a director card."""
    out = []
    for stage in flow.stages:
        if not any(always_loaded(path) for path in stage.mount):
            out.append(f"stage {stage.id}: no always-loaded card in its own slices, so the agent gets no "
                       f"director card for it")
    return out
