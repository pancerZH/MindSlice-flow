"""Command line: ``mindslice-flow [-w WORKSPACE] {check,init,status,next}``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import FlowError, __version__, run


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mindslice-flow",
        description="Mount, settle and bake MindSlice memory stage by stage. The player plays in their own "
                    "harness; this command only switches stages.")
    p.add_argument("-w", "--workspace", type=Path, metavar="WORKSPACE",
                   help="flow workspace (default: $MINDSLICE_WORKSPACE or the nearest dir with mounts.toml)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="check a flow definition")
    c.add_argument("flow_dir", type=Path, metavar="FLOW_DIR")

    i = sub.add_parser("init", help="create a workspace for one run and enter the first stage")
    i.add_argument("flow_dir", type=Path, metavar="FLOW_DIR")
    i.add_argument("-o", "--output", type=Path, metavar="WORKSPACE",
                   help="workspace to create (default: ./<flow dir name>.workspace)")

    sub.add_parser("status", help="show the stages, the mounts and what the current stage leaves to settle")

    n = sub.add_parser("next", help="bake the current stage into the state slice and enter the next stage")
    n.add_argument("--to", metavar="STAGE", help="enter this stage instead of the next one")
    return p


def dispatch(args: argparse.Namespace) -> None:
    if args.command == "check":
        run.check(args.flow_dir)
    elif args.command == "init":
        out = args.output or args.workspace or Path(f"{args.flow_dir.resolve().name}.workspace")
        run.init(args.flow_dir, out)
    elif args.command == "status":
        run.status(run.locate(args.workspace))
    else:
        run.advance(run.locate(args.workspace), args.to)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        dispatch(args)
    except FlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to continue", file=sys.stderr)
        return 130
    return 0
