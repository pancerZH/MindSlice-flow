"""The MindSlice engine, used only through its ``mindslice`` command."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import FlowError

MINIMUM = (0, 2, 5)  # older engines are not supported (single user, no compatibility kept)


def command() -> str:
    """The full path of ``mindslice``. On Windows it may be a ``.cmd``/``.bat`` shim, which ``subprocess``
    finds only by full path (CreateProcess appends just ``.exe``); ``shutil.which`` honours PATHEXT."""
    found = shutil.which("mindslice")
    if found is None:
        raise FlowError("the `mindslice` command is not on PATH; install the MindSlice engine first")
    return found


def _engine(*args: str) -> subprocess.CompletedProcess:
    try:
        # The engine writes CJK text; without this its piped output uses the locale encoding on Windows.
        return subprocess.run([command(), *args], capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except FileNotFoundError:
        raise FlowError("the `mindslice` command is not on PATH; install the MindSlice engine first") from None


def version() -> str:
    """What ``mindslice --version`` prints. An engine older than ``MINIMUM`` is an error."""
    done = _engine("--version")
    line = done.stdout.strip()
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
    if done.returncode != 0 or not found:
        raise FlowError(f"`mindslice --version` did not give a version:\n{line or done.stderr.strip()}")
    if tuple(map(int, found.groups())) < MINIMUM:
        raise FlowError(f"this flow needs MindSlice {'.'.join(map(str, MINIMUM))} or newer, and `mindslice` on PATH "
                        f"is {found.group()}; upgrade the engine, for example with `pipx upgrade mindslice`")
    return line


def _error(args: tuple[str, ...], done: subprocess.CompletedProcess) -> FlowError:
    """The engine's own message, with the next steps it suggests (a bake conflict lists them)."""
    text = done.stderr.strip() or done.stdout.strip()
    try:
        data = json.loads(text)
        lines = [data.get("message", text)]
        steps = data.get("next_steps") or data.get("details", {}).get("next_steps") or []
        lines += [f"  - {s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)}" for s in steps]
        text = "\n".join(lines)
    except (json.JSONDecodeError, AttributeError):
        pass
    return FlowError(f"mindslice {' '.join(args)} failed:\n{text}")


def _call(*args: str) -> dict:
    done = _engine(*args, "--json")
    if done.returncode != 0:
        raise _error(args, done)
    return json.loads(done.stdout)


def init(ws: Path) -> None:
    _call("init", str(ws))


def status(ws: Path) -> dict:
    return _call("-w", str(ws), "status")


def mount(ws: Path, path: Path, precedence: int, alias: str | None = None) -> str:
    """Mount ``path`` read-only; returns its alias."""
    extra = ["--alias", alias] if alias else []
    return _call("-w", str(ws), "mount", str(path), "--precedence", str(precedence), *extra)["alias"]


def unmount(ws: Path, alias: str) -> None:
    _call("-w", str(ws), "unmount", alias)


def validate(ws: Path) -> dict:
    """The validation report. The engine exits 1 when it found errors, and still prints the report."""
    args = ("-w", str(ws), "validate")
    done = _engine(*args, "--json")
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise _error(args, done) from None
    if done.returncode not in (0, 1) or "errors" not in report:
        raise _error(args, done)
    return report


def read(ws: Path, address: str) -> dict | None:
    """The card at ``address``, or None when there is none (or its mount is not mounted)."""
    args = ("-w", str(ws), "read", address)
    done = _engine(*args, "--json")
    if done.returncode == 0:
        return json.loads(done.stdout)
    try:
        if json.loads(done.stderr).get("code") == "not_found":
            return None
    except (json.JSONDecodeError, AttributeError):
        pass
    raise _error(args, done)


def bake(ws: Path, alias: str) -> dict:
    """Move every @overlay card into the slice ``alias``: a new slice the first time, a new version after."""
    return _call("-w", str(ws), "bake", f"@{alias}", "--prefix", "")
