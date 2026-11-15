# mindslice-flow

Mount, settle and bake [MindSlice](https://github.com/pancerZH/MindSlice) memory stage by stage.

A flow is a list of stages, each with its own set of slices. While a stage is played, only its slices
are mounted, and its director cards (cards with `load = "always"`) are the agent's standing
instructions. When the user ends the stage, the agent tidies what the stage left in `@overlay`, and
`mindslice-flow next` bakes it into one cumulative state slice (`@story` by default, a new version
per stage) and mounts the next stage. The first use is a multi-act tabletop / interactive story.

The flow is a small state machine on top of the `mindslice` command. It starts no agent and calls no
model: the player plays in their own harness, and the agent runs `next` for them when they ask.

Python 3.12+, standard library only. Needs `mindslice` 0.1.6 or newer on `PATH`.

## A flow definition

```text
my-script/
├── flow.toml
└── slices/
    ├── world.slice/      # mounted in every stage
    ├── act1.slice/       # its load = "always" cards are the director cards of act 1
    └── act2.slice/
```

```toml
name = "迷雾庄园"
state = "story"                      # alias of the cumulative state slice (default "story")
mount = ["slices/world.slice"]       # mounted in every stage, relative to flow.toml

[[stage]]
id = "act1"
title = "第一幕：抵达"
mount = ["slices/act1.slice"]        # this stage's own slices

[[stage]]
id = "act2"
title = "第二幕：晚宴"
mount = ["slices/act2.slice"]
```

A slice mounts under the `alias` of its `slice.toml`, else under its directory name without
`.slice`; either must be letters, digits, `_`, `.` or `-`, so a directory with another name (`世界.slice`)
needs an `alias`. `flow.toml` is read again by every command, so later stages can be edited during a run.
[`examples/manor`](examples/manor) is a complete three-act flow.

## Commands

| Command | Does |
| :-- | :-- |
| `mindslice-flow check FLOW_DIR` | check the definition; warn about stages without a director card |
| `mindslice-flow init FLOW_DIR [-o WORKSPACE]` | create a workspace for one run and enter the first stage |
| `mindslice-flow status` | stages, mounts, overlay cards left to settle, changes waiting for review |
| `mindslice-flow next [--to STAGE]` | settle the current stage and enter the next one (or `STAGE`) |

The workspace is found like `mindslice` finds it: `-w`, else `$MINDSLICE_WORKSPACE`, else the nearest
directory with `mounts.toml`. Each run gets its own workspace; the flow owns its mount table.

`next` refuses while changes wait for review (decide on them with `mindslice review`, `apply` or
`discard`), bakes every `@overlay` card into the state slice, mounts exactly the next stage's slices
plus `@overlay`, the state slice and `@flow`, and rewrites the `@flow` cards. It writes its state last,
so running it again after an interruption finishes the same switch. After the last stage it marks
the flow finished; `next --to STAGE` plays a stage again. To undo a stage's bake, use
`mindslice rollback @story --yes` and then `next --to`.

## Playing

1. `mindslice-flow init examples/manor -o manor.run`
2. Point your harness's MindSlice at the workspace (for the Claude Code plugin, set its workspace
   option; otherwise `MINDSLICE_WORKSPACE`, or the MCP server `mindslice -w manor.run mcp`).
3. Start a session. The standing memory holds the director card and the flow's stage card
   (`@flow/stage`). When the stage's end condition is met, the agent says so; you can keep exploring.
4. Ask the agent to end the stage. It follows `@flow/settlement`: tidies the overlay, writes the
   stage summary `summaries/<stage-id>`, shows you the result, and runs `mindslice-flow next` once
   you agree.
5. Start a new session for the next stage (or let the agent call `manifest` again to continue).

The stage cards are rendered from [`mindslice_flow/cards/`](mindslice_flow/cards) and carry the
workspace's absolute path, so the agent never has to guess it.

## Not in this version

- Turning a script document into slices, director cards and `flow.toml`.
- Branch conditions, automatic stage ends, or driving harness sessions.
- Hiding future stages: their files stay on disk, they are only not mounted or searched.

## Development

```sh
python -m unittest discover -s tests -t . -v
```

The functional tests run the example flow with the real `mindslice` command and no model. CI runs
them on Linux, Windows and macOS against both the newest `mindslice` on PyPI and the oldest supported
one (`MINIMUM` in `mindslice_flow/engine.py`), on every push and weekly.

## License

Apache-2.0
