"""Controlled, tenant-aware tool runtime for Longyun sub-agents.

Keep the heavyweight rice-tool imports lazy.  Contract and policy tests must
not need the complete scientific Python stack just to import the tool gateway,
and a worker that only validates a workflow should not eagerly import every
statistics package.
"""

from .core import AgentToolContext, ControlledToolRegistry, ToolExecutionError


def build_rice_tool_registry(*args, **kwargs):
    from .rice import build_rice_tool_registry as factory

    return factory(*args, **kwargs)

__all__ = [
    "AgentToolContext",
    "ControlledToolRegistry",
    "ToolExecutionError",
    "build_rice_tool_registry",
]
