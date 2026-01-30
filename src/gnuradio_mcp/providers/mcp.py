from fastmcp import FastMCP

from gnuradio_mcp.middlewares.platform import PlatformMiddleware
from gnuradio_mcp.providers.base import PlatformProvider


class McpPlatformProvider:
    def __init__(self, mcp_instance: FastMCP, platform_provider: PlatformProvider):
        self._mcp_instance = mcp_instance
        self._platform_provider = platform_provider
        self.__init_tools()

    def __init_tools(self):
        t = self._mcp_instance.tool
        p = self._platform_provider

        # ── Existing tools ─────────────────────
        t(p.get_blocks)
        t(p.make_block)
        t(p.remove_block)
        t(p.get_block_params)
        t(p.set_block_params)
        t(p.get_block_sources)
        t(p.get_block_sinks)
        t(p.get_connections)
        t(p.connect_blocks)
        t(p.disconnect_blocks)
        t(p.validate_block)
        t(p.validate_flowgraph)
        t(p.get_all_errors)
        t(p.save_flowgraph)
        t(p.get_all_available_blocks)

        # ── OOT Block Loading ──────────────────
        t(p.load_oot_blocks)

        # ── Gap 1: Code Generation ─────────────
        t(p.generate_code)

        # ── Gap 2: Load Flowgraph ──────────────
        t(p.load_flowgraph)

        # ── Gap 3: Flowgraph Options ───────────
        t(p.get_flowgraph_options)
        t(p.set_flowgraph_options)

        # ── Gap 4: Embedded Python Blocks ──────
        t(p.create_embedded_python_block)

        # ── Gap 5: Search / Categories ─────────
        t(p.search_blocks)
        t(p.get_block_categories)

        # ── Gap 6: Expression Evaluation ───────
        t(p.evaluate_expression)

        # ── Gap 7: Block Bypass ────────────────
        t(p.bypass_block)
        t(p.unbypass_block)

        # ── Gap 8: Export/Import Data ──────────
        t(p.export_flowgraph_data)
        t(p.import_flowgraph_data)

    @property
    def app(self) -> FastMCP:
        return self._mcp_instance

    @classmethod
    def from_platform_middleware(
        cls,
        mcp_instance: FastMCP,
        platform_middleware: PlatformMiddleware,
        flowgraph_path: str = "",
    ):
        platform_provider = PlatformProvider(platform_middleware, flowgraph_path)
        return cls(mcp_instance, platform_provider)
