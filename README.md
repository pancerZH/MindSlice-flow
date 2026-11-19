# mindslice-flow

Mount, settle and bake [MindSlice](https://github.com/pancerZH/MindSlice) memory stage by stage.

A flow is a list of stages, each with its own set of slices. While a stage is played, only its slices
are mounted, and its director cards (cards with `load = "always"`) are the agent's standing
instructions. When the user ends the stage, the agent tidies what the stage left in `@overlay`, and
`mindslice-flow next` bakes it into one cumulative state slice (`@story` by default, a new version
per stage) and mounts the next stage. The first use is a multi-act tabletop / interactive story.

The script sets the main line; the player moves the world. Each stage can state its premise, what the
story should hold when it starts. At the end of a stage the agent compares the state slice with the
premises and picks the way on with the user: the next main-line stage, a later one, a side stage the
script provides, or a new side stage that it writes itself, which is baked into a slice of its own for
this run. Any stage can also mount earlier stages' slices again as material, so a side stage is the
script's own slices plus one new slice, not a new script.

The flow is a small state machine on top of the `mindslice` command. It starts no agent and calls no
model: the player plays in their own harness, and the agent runs `next` for them when they ask.

Python 3.12+, standard library only. Needs `mindslice` 0.2.5 or newer on `PATH`.

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
premise = "玩家留在庄园过夜，打算查一查封闭的东翼"   # what the story should hold when it starts
mount = ["slices/act2.slice"]

[[stage]]
id = "cellar"
title = "酒窖"
premise = "玩家发现了通往酒窖的暗门"
side = true                          # off the main line: entered only with `next --to cellar`
mount = ["slices/cellar.slice"]
```

The stages without `side = true` are the main line, in file order. A premise is optional and should
not give anything away: the agent reads the premises of the stages ahead when it settles a stage.

A slice mounts under the `alias` of its `slice.toml`, else under its directory name without
`.slice`; either must be letters, digits, `_`, `.` or `-`, so a directory with another name (`世界.slice`)
needs an `alias`. `flow.toml` is read again by every command, so later stages can be edited during a run.
[`examples/manor`](https://github.com/pancerZH/MindSlice-flow/tree/main/examples/manor) is a complete three-act flow with one side stage.

## Commands

| Command | Does |
| :-- | :-- |
| `mindslice-flow check FLOW_DIR` | check the definition; warn about stages without a director card |
| `mindslice-flow init FLOW_DIR [-o WORKSPACE]` | create a workspace for one run and enter the first stage |
| `mindslice-flow status` | stages, mounts, overlay cards left to settle, changes waiting for review |
| `mindslice-flow next` | settle the current stage and enter the default next one |
| `mindslice-flow next --to STAGE` | … and enter `STAGE`: a stage of the script, or one written in this run |
| `mindslice-flow next --new ID --title T [--rejoin STAGE]` | … and enter a new side stage the agent wrote under `stages/ID/` |
| `… --reuse STAGE` (with any of the three, repeatable) | also mount `STAGE`'s slices as material |

The workspace is found like `mindslice` finds it: `-w`, else `$MINDSLICE_WORKSPACE`, else the nearest
directory with `mounts.toml`. Each run gets its own workspace; the flow owns its mount table.

Where `next` goes:

- From a main-line stage, the next main-line stage; after the last one the flow is finished.
- From a side stage of the script, the main-line stage after the one played last.
- From a stage written in this run, the main-line stage it rejoins: `--rejoin`, by default the next
  main-line stage.
- `--to STAGE` enters any stage of the script, or plays a stage written in this run again.
- `--new ID` enters a stage the agent wrote: its plan card `stages/ID/plan` (required) and its new
  material under `stages/ID/`. `next` bakes those cards into a slice `@ID` of the run's own (in the
  workspace's `slices/`), mounts it as the stage's slice, and attaches the plan to the stage card.
- `--reuse STAGE` mounts another stage's slices too: a stage already played, or a main-line stage no
  later than the one the story heads for (the target, or the stage a side stage rejoins). A card in
  `@flow` supersedes their director cards, so the boot set, `read` and `search` give it instead.

`next` refuses, and changes nothing, while changes wait for review (decide on them with
`mindslice review`, `apply` or `discard`); when the overlay has cards, while the stage summary
`summaries/<stage-id>` is missing, a stage after the first has no adaptation card
`stages/<stage-id>/adaptation` (what this run has changed in the stage's script; stages written in
the run need none), or `mindslice validate` reports an error on an `@overlay` card; and for `--new`,
while the plan card is missing, the id is taken, or `--rejoin` names a side stage. A `--reuse` beyond
the stages above is refused too. Then it bakes the `--new` stage's cards, then every other `@overlay`
card into the state slice, mounts exactly the new stage's slices (and the reused ones) plus `@overlay`,
the state slice and `@flow`, and rewrites the `@flow` cards. The new stage card shows the route so far
and carries the plan of a stage written in the run and the baked summary of the stage just played, so
the next session starts from them without reading them. `next` writes its state last, so running it
again after an interruption finishes the same switch. To undo a stage's bake, use
`mindslice rollback @story --yes` and then `next --to`.

## Playing

1. `mindslice-flow init examples/manor -o manor.run`
2. Point your harness's MindSlice at the workspace (for the Claude Code plugin, set its workspace
   option; otherwise `MINDSLICE_WORKSPACE`, or the MCP server `mindslice -w manor.run mcp`).
3. Start a session. The standing memory holds the director card and the flow's stage card
   (`@flow/stage`). From the second stage on, the agent first writes the stage's adaptation card.
   When the stage's end condition is met, the agent says so; you can keep exploring.
4. Ask the agent to end the stage. It follows `@flow/settlement`: tidies the overlay, picks the way
   on against the premises (writing a new side stage when none fits), writes the stage summary
   `summaries/<stage-id>`, shows you both, and runs the `mindslice-flow next` it chose once you agree.
5. Start a new session for the next stage (or let the agent call `manifest` again to continue).

The stage cards are rendered from [`mindslice_flow/cards/`](https://github.com/pancerZH/MindSlice-flow/tree/main/mindslice_flow/cards) and carry the
workspace's absolute path, so the agent never has to guess it.

## Not in this version

- Turning a script document into slices, director cards and `flow.toml`.
- Checking premises by program: the agent judges them; the flow checks only what it can (ids, plans,
  summaries, the reuse range).
- Automatic stage ends, or driving harness sessions.
- Hiding future stages: their files stay on disk, they are only not mounted or searched.

## Development

```sh
python -m unittest discover -s tests -t . -v
```

The functional tests run the example flow with the real `mindslice` command and no model. CI runs
them on Linux, Windows and macOS against the newest `mindslice` on PyPI, on every push and weekly.

## License

Apache-2.0
