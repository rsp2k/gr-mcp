from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import Context, FastMCP
from pydantic import BaseModel

from gnuradio_mcp.middlewares.docker import DockerMiddleware
from gnuradio_mcp.middlewares.oot import OOTInstallerMiddleware
from gnuradio_mcp.providers.runtime import RuntimeProvider

logger = logging.getLogger(__name__)


class RuntimeModeStatus(BaseModel):
    """Status of runtime mode and available capabilities."""

    enabled: bool
    tools_registered: list[str]
    docker_available: bool
    oot_available: bool


class ClientCapabilities(BaseModel):
    """MCP client capability information from initialize handshake."""

    client_name: str | None = None
    client_version: str | None = None
    protocol_version: str | None = None
    capabilities: dict[str, Any] = {}
    roots_supported: bool = False
    sampling_supported: bool = False
    experimental: dict[str, Any] = {}


class ClientRoot(BaseModel):
    """A root directory advertised by the MCP client."""

    uri: str
    name: str | None = None


class McpRuntimeProvider:
    """Registers runtime control tools with FastMCP.

    Uses dynamic tool registration to minimize context usage:
    - At startup: only mode control tools are registered
    - When runtime mode is enabled: all runtime tools are registered
    - When disabled: runtime tools are removed

    This keeps the tool list small when only doing flowgraph design,
    and expands it when connecting to SDR hardware or running flowgraphs.
    """

    def __init__(self, mcp_instance: FastMCP, runtime_provider: RuntimeProvider):
        self._mcp = mcp_instance
        self._provider = runtime_provider
        self._runtime_tools: dict[str, Callable] = {}
        self._runtime_enabled = False
        self.__init_mode_tools()
        self.__init_resources()

    def __init_mode_tools(self):
        """Register only the mode control tools at startup."""

        @self._mcp.tool
        def get_runtime_mode() -> RuntimeModeStatus:
            """Check if runtime mode is enabled and what capabilities are available.

            Runtime mode provides tools for:
            - Connecting to running flowgraphs (XML-RPC, ControlPort)
            - Launching flowgraphs in Docker containers
            - Installing OOT modules
            - Controlling SDR hardware

            Call enable_runtime_mode() to register these tools.
            """
            return RuntimeModeStatus(
                enabled=self._runtime_enabled,
                tools_registered=list(self._runtime_tools.keys()),
                docker_available=self._provider._has_docker,
                oot_available=self._provider._has_oot,
            )

        @self._mcp.tool
        def enable_runtime_mode() -> RuntimeModeStatus:
            """Enable runtime mode, registering all runtime control tools.

            This adds tools for:
            - XML-RPC connection and variable control
            - ControlPort/Thrift for performance monitoring
            - Docker container lifecycle (if Docker available)
            - OOT module installation (if Docker available)

            Use this when you need to:
            - Connect to a running flowgraph
            - Launch flowgraphs in containers
            - Control SDR hardware
            - Monitor performance
            """
            if self._runtime_enabled:
                return RuntimeModeStatus(
                    enabled=True,
                    tools_registered=list(self._runtime_tools.keys()),
                    docker_available=self._provider._has_docker,
                    oot_available=self._provider._has_oot,
                )

            self._register_runtime_tools()
            self._runtime_enabled = True

            logger.info(
                "Runtime mode enabled: registered %d tools",
                len(self._runtime_tools),
            )

            return RuntimeModeStatus(
                enabled=True,
                tools_registered=list(self._runtime_tools.keys()),
                docker_available=self._provider._has_docker,
                oot_available=self._provider._has_oot,
            )

        @self._mcp.tool
        def disable_runtime_mode() -> RuntimeModeStatus:
            """Disable runtime mode, removing runtime tools to reduce context.

            Use this when you're done with runtime operations and want to
            reduce the tool list for flowgraph design work.
            """
            if not self._runtime_enabled:
                return RuntimeModeStatus(
                    enabled=False,
                    tools_registered=[],
                    docker_available=self._provider._has_docker,
                    oot_available=self._provider._has_oot,
                )

            self._unregister_runtime_tools()
            self._runtime_enabled = False

            logger.info("Runtime mode disabled: removed runtime tools")

            return RuntimeModeStatus(
                enabled=False,
                tools_registered=[],
                docker_available=self._provider._has_docker,
                oot_available=self._provider._has_oot,
            )

        # Debug tools for MCP client inspection
        @self._mcp.tool
        async def get_client_capabilities(ctx: Context) -> ClientCapabilities:
            """Get the connected MCP client's capabilities.

            Returns information about the client including:
            - Client name and version (e.g., "claude-code" v2.1.15)
            - MCP protocol version
            - Supported capabilities (roots, sampling, etc.)
            - Experimental features

            Useful for debugging MCP connections and understanding
            what features the client supports.
            """
            session = ctx.session
            client_params = session.client_params if session else None

            if client_params is None:
                return ClientCapabilities()

            client_info = getattr(client_params, "clientInfo", None)
            caps = getattr(client_params, "capabilities", None)

            result = ClientCapabilities(
                client_name=client_info.name if client_info else None,
                client_version=client_info.version if client_info else None,
                protocol_version=getattr(client_params, "protocolVersion", None),
            )

            if caps:
                if hasattr(caps, "roots") and caps.roots is not None:
                    result.roots_supported = True
                    result.capabilities["roots"] = {
                        "listChanged": getattr(caps.roots, "listChanged", None)
                    }

                if hasattr(caps, "sampling") and caps.sampling is not None:
                    result.sampling_supported = True
                    result.capabilities["sampling"] = {}

                if hasattr(caps, "experimental"):
                    result.experimental = caps.experimental or {}

            return result

        @self._mcp.tool
        async def list_client_roots(ctx: Context) -> list[ClientRoot]:
            """List the root directories advertised by the MCP client.

            Roots represent project directories or workspaces the client
            wants the server to be aware of. Typically includes the
            current working directory.

            Returns empty list if roots capability is not supported.
            """
            try:
                roots = await ctx.list_roots()
                return [
                    ClientRoot(uri=str(root.uri), name=root.name)
                    for root in roots
                ]
            except Exception as e:
                logger.warning("Failed to list client roots: %s", e)
                return []

        logger.info(
            "Registered 5 mode control tools (runtime mode disabled by default)"
        )

    def _register_runtime_tools(self):
        """Dynamically register all runtime tools."""
        p = self._provider

        # Connection management
        self._add_tool("connect", p.connect)
        self._add_tool("disconnect", p.disconnect)
        self._add_tool("get_status", p.get_status)

        # Variable control
        self._add_tool("list_variables", p.list_variables)
        self._add_tool("get_variable", p.get_variable)
        self._add_tool("set_variable", p.set_variable)

        # Flowgraph execution
        self._add_tool("start", p.start)
        self._add_tool("stop", p.stop)
        self._add_tool("lock", p.lock)
        self._add_tool("unlock", p.unlock)

        # ControlPort/Thrift tools
        self._add_tool("connect_controlport", p.connect_controlport)
        self._add_tool("disconnect_controlport", p.disconnect_controlport)
        self._add_tool("get_knobs", p.get_knobs)
        self._add_tool("set_knobs", p.set_knobs)
        self._add_tool("get_knob_properties", p.get_knob_properties)
        self._add_tool("get_performance_counters", p.get_performance_counters)
        self._add_tool("post_message", p.post_message)

        # Docker-dependent tools
        if p._has_docker:
            # Container lifecycle
            self._add_tool("launch_flowgraph", p.launch_flowgraph)
            self._add_tool("list_containers", p.list_containers)
            self._add_tool("stop_flowgraph", p.stop_flowgraph)
            self._add_tool("remove_flowgraph", p.remove_flowgraph)
            self._add_tool("connect_to_container", p.connect_to_container)
            self._add_tool(
                "connect_to_container_controlport", p.connect_to_container_controlport
            )

            # Visual feedback
            self._add_tool("capture_screenshot", p.capture_screenshot)
            self._add_tool("get_container_logs", p.get_container_logs)

            # Coverage collection
            self._add_tool("collect_coverage", p.collect_coverage)
            self._add_tool("generate_coverage_report", p.generate_coverage_report)
            self._add_tool("combine_coverage", p.combine_coverage)
            self._add_tool("delete_coverage", p.delete_coverage)

            # OOT module installation
            if p._has_oot:
                self._add_tool("detect_oot_modules", p.detect_oot_modules)
                self._add_tool("install_oot_module", p.install_oot_module)
                self._add_tool("list_oot_images", p.list_oot_images)
                self._add_tool("remove_oot_image", p.remove_oot_image)
                self._add_tool("build_multi_oot_image", p.build_multi_oot_image)
                self._add_tool("list_combo_images", p.list_combo_images)
                self._add_tool("remove_combo_image", p.remove_combo_image)

    def _unregister_runtime_tools(self):
        """Remove all dynamically registered runtime tools."""
        for name in list(self._runtime_tools.keys()):
            try:
                self._mcp.remove_tool(name)
            except Exception as e:
                logger.warning("Failed to remove tool %s: %s", name, e)
        self._runtime_tools.clear()

    def _add_tool(self, name: str, func: Callable):
        """Add a tool and track it for later removal."""
        self._mcp.add_tool(func)
        self._runtime_tools[name] = func

    def __init_resources(self):
        from gnuradio_mcp.oot_catalog import (
            CATALOG,
            OOTDirectoryIndex,
            OOTModuleDetail,
            OOTModuleSummary,
            build_install_example,
        )

        oot_mw = self._provider._oot  # None when Docker unavailable

        @self._mcp.resource(
            "oot://directory",
            name="oot_directory",
            description="Index of curated GNU Radio OOT modules available for installation",
            mime_type="application/json",
        )
        def list_oot_directory() -> str:
            summaries = []
            for entry in CATALOG.values():
                installed = None
                if oot_mw is not None:
                    installed = entry.name in oot_mw._registry
                summaries.append(
                    OOTModuleSummary(
                        name=entry.name,
                        description=entry.description,
                        category=entry.category,
                        preinstalled=entry.preinstalled,
                        installed=installed,
                    )
                )
            index = OOTDirectoryIndex(modules=summaries, count=len(summaries))
            return index.model_dump_json()

        @self._mcp.resource(
            "oot://directory/{module_name}",
            name="oot_module_detail",
            description="Full installation details for a specific OOT module",
            mime_type="application/json",
        )
        def get_oot_module(module_name: str) -> str:
            entry = CATALOG.get(module_name)
            if entry is None:
                known = ", ".join(sorted(CATALOG.keys()))
                raise ValueError(
                    f"Unknown module '{module_name}'. Available: {known}"
                )

            installed = None
            installed_image_tag = None
            if oot_mw is not None:
                info = oot_mw._registry.get(entry.name)
                installed = info is not None
                if info is not None:
                    installed_image_tag = info.image_tag

            detail = OOTModuleDetail(
                name=entry.name,
                description=entry.description,
                category=entry.category,
                git_url=entry.git_url,
                branch=entry.branch,
                build_deps=entry.build_deps,
                cmake_args=entry.cmake_args,
                homepage=entry.homepage,
                gr_versions=entry.gr_versions,
                preinstalled=entry.preinstalled,
                installed=installed,
                installed_image_tag=installed_image_tag,
                install_example=build_install_example(entry),
            )
            return detail.model_dump_json()

        logger.info(
            "Registered OOT directory resources (%d modules)", len(CATALOG)
        )

    @classmethod
    def create(cls, mcp_instance: FastMCP) -> McpRuntimeProvider:
        """Factory: create RuntimeProvider with optional Docker support."""
        docker_mw = DockerMiddleware.create()
        oot_mw = None
        if docker_mw is not None:
            oot_mw = OOTInstallerMiddleware(docker_mw._client)
        provider = RuntimeProvider(docker_mw=docker_mw, oot_mw=oot_mw)
        return cls(mcp_instance, provider)
