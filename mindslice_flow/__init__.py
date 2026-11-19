"""mindslice-flow: mount, settle and bake MindSlice memory stage by stage.

The flow only orchestrates. It reads a flow definition (``flow.toml``), keeps the run state in the
workspace and drives the ``mindslice`` command; the agent in the user's own harness plays each stage
and tidies its memory, following the cards the flow renders into the ``@flow`` slice.
"""

__version__ = "0.2.1"


class FlowError(Exception):
    """A failure the user can act on; the CLI prints it without a traceback."""
