"""Flag-gated web setup wizard served from the MCP container.

``build_setup_wizard_mount`` composes the wizard's Starlette routes;
``kairix.agents.mcp.transport.build_mcp_app`` mounts them at ``/setup``
when the ``setup_wizard_web`` feature flag is ON. Templates + static
assets are vendored package data under ``templates/`` and ``static/``.
"""

from kairix.platform.setup.web.routes import (
    OPERATOR_TOKEN_HEADER,
    SETUP_PATH_PREFIX,
    OperatorTokenGuard,
    build_setup_wizard_mount,
)

__all__ = [
    "OPERATOR_TOKEN_HEADER",
    "SETUP_PATH_PREFIX",
    "OperatorTokenGuard",
    "build_setup_wizard_mount",
]
