from __future__ import annotations

import logging

from fastmcp import FastMCP

from gnuradio_mcp.middlewares.docker import DockerMiddleware
from gnuradio_mcp.middlewares.oot import OOTInstallerMiddleware
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
        self.__init_resources()

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

        # ControlPort/Thrift tools (always available - Phase 2)
        self._mcp.tool(p.connect_controlport)
        self._mcp.tool(p.disconnect_controlport)
        self._mcp.tool(p.get_knobs)
        self._mcp.tool(p.set_knobs)
        self._mcp.tool(p.get_knob_properties)
        self._mcp.tool(p.get_performance_counters)
        self._mcp.tool(p.post_message)

        # Docker-dependent tools
        if p._has_docker:
            # Container lifecycle
            self._mcp.tool(p.launch_flowgraph)
            self._mcp.tool(p.list_containers)
            self._mcp.tool(p.stop_flowgraph)
            self._mcp.tool(p.remove_flowgraph)
            self._mcp.tool(p.connect_to_container)
            self._mcp.tool(p.connect_to_container_controlport)  # Phase 2

            # Visual feedback
            self._mcp.tool(p.capture_screenshot)
            self._mcp.tool(p.get_container_logs)

            # Coverage collection
            self._mcp.tool(p.collect_coverage)
            self._mcp.tool(p.generate_coverage_report)
            self._mcp.tool(p.combine_coverage)
            self._mcp.tool(p.delete_coverage)

            # OOT module installation
            if p._has_oot:
                # Detection (new!)
                self._mcp.tool(p.detect_oot_modules)
                # Installation
                self._mcp.tool(p.install_oot_module)
                self._mcp.tool(p.list_oot_images)
                self._mcp.tool(p.remove_oot_image)
                # Multi-OOT combo images
                self._mcp.tool(p.build_multi_oot_image)
                self._mcp.tool(p.list_combo_images)
                self._mcp.tool(p.remove_combo_image)
                logger.info("Registered 36 runtime tools (Docker + OOT available)")
            else:
                logger.info("Registered 29 runtime tools (Docker available)")
        else:
            logger.info(
                "Registered 17 runtime tools (Docker unavailable, "
                "container tools skipped)"
            )

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
