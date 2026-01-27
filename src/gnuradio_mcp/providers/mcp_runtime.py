from __future__ import annotations

import logging

from fastmcp import FastMCP

from gnuradio_mcp.middlewares.docker import DockerMiddleware
from gnuradio_mcp.providers.runtime import RuntimeProvider

logger = logging.getLogger(__name__)


class McpRuntimeProvider:
    """Registers runtime control tools with FastMCP.

    Docker is optional: if unavailable, container lifecycle and visual
    feedback tools are skipped, but XML-RPC connection/control tools
    are still registered (for connecting to externally-managed flowgraphs).
    """

    def __init__(self, mcp_instance: FastMCP, runtime_provider: RuntimeProvider):
        self._mcp = mcp_instance
        self._provider = runtime_provider
        self.__init_tools()

    def __init_tools(self):
        p = self._provider

        # Connection management (always available)
        self._mcp.tool(p.connect)
        self._mcp.tool(p.disconnect)
        self._mcp.tool(p.get_status)

        # Variable control (always available)
        self._mcp.tool(p.list_variables)
        self._mcp.tool(p.get_variable)
        self._mcp.tool(p.set_variable)

        # Flowgraph execution (always available)
        self._mcp.tool(p.start)
        self._mcp.tool(p.stop)
        self._mcp.tool(p.lock)
        self._mcp.tool(p.unlock)

        # Docker-dependent tools
        if p._has_docker:
            self._mcp.tool(p.launch_flowgraph)
            self._mcp.tool(p.list_containers)
            self._mcp.tool(p.stop_flowgraph)
            self._mcp.tool(p.remove_flowgraph)
            self._mcp.tool(p.connect_to_container)
            self._mcp.tool(p.capture_screenshot)
            self._mcp.tool(p.get_container_logs)
            logger.info("Registered 17 runtime tools (Docker available)")
        else:
            logger.info(
                "Registered 10 runtime tools (Docker unavailable, "
                "container tools skipped)"
            )

    @classmethod
    def create(cls, mcp_instance: FastMCP) -> McpRuntimeProvider:
        """Factory: create RuntimeProvider with optional Docker support."""
        docker_mw = DockerMiddleware.create()
        provider = RuntimeProvider(docker_mw=docker_mw)
        return cls(mcp_instance, provider)
