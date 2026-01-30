from __future__ import annotations

import logging
import tempfile
from typing import TYPE_CHECKING, Any, Optional

from gnuradio.grc.core.blocks.block import Block
from gnuradio.grc.core.FlowGraph import FlowGraph

from gnuradio_mcp.middlewares.base import ElementMiddleware
from gnuradio_mcp.middlewares.block import BlockMiddleware
from gnuradio_mcp.models import (
    BlockModel,
    ConnectionModel,
    EmbeddedBlockIOModel,
    ErrorModel,
    FlowgraphOptionsModel,
    GeneratedCodeModel,
    GeneratedFileModel,
    PortModel,
)
from gnuradio_mcp.utils import format_error_message, get_port_from_port_model, get_unique_id

if TYPE_CHECKING:
    from gnuradio_mcp.middlewares.platform import PlatformMiddleware

logger = logging.getLogger(__name__)


def set_block_name(block: Block, name: str):
    block.params["id"].set_value(name)


class FlowGraphMiddleware(ElementMiddleware):
    def __init__(self, flowgraph: FlowGraph, platform: "PlatformMiddleware | None" = None):
        super().__init__(flowgraph)
        self._flowgraph = self._element
        self._platform_mw = platform

    @property
    def blocks(self) -> list[BlockModel]:
        return [BlockModel.from_block(block) for block in self._flowgraph.blocks]

    def add_block(
        self, block_type: str, block_name: Optional[str] = None
    ) -> BlockModel:
        block_name = block_name or get_unique_id(self._flowgraph.blocks, block_type)
        block = self._flowgraph.new_block(block_type)
        assert block is not None, f"Failed to create block: {block_type}"
        set_block_name(block, block_name)
        return BlockModel.from_block(block)

    def remove_block(self, block_name: str) -> None:
        block_middleware = self.get_block(block_name)
        self._flowgraph.remove_element(block_middleware._block)

    def get_block(self, block_name: str) -> BlockMiddleware:
        """Look up a block by name from the live flowgraph.

        Always queries the actual flowgraph state — no caching — so that
        block renames, removals, and re-creations are immediately visible.
        """
        block = next(
            (b for b in self._flowgraph.blocks if b.name == block_name), None
        )
        if block is None:
            raise KeyError(f"Block {block_name!r} not found in flowgraph")
        return BlockMiddleware(block)

    def connect_blocks(
        self, src_port_model: PortModel, dst_port_model: PortModel
    ) -> None:
        src_port = get_port_from_port_model(self._flowgraph, src_port_model)
        dst_port = get_port_from_port_model(self._flowgraph, dst_port_model)
        self._flowgraph.connect(src_port, dst_port)

    def disconnect_blocks(
        self, src_port_model: PortModel, dst_port_model: PortModel
    ) -> None:
        src_port = get_port_from_port_model(self._flowgraph, src_port_model)
        dst_port = get_port_from_port_model(self._flowgraph, dst_port_model)
        self._flowgraph.disconnect(src_port, dst_port)

    def get_connections(self) -> list[ConnectionModel]:
        return [
            ConnectionModel.from_connection(connection)
            for connection in self._flowgraph.connections
        ]

    # ──────────────────────────────────────────
    # Gap 1: Code Generation
    # ──────────────────────────────────────────

    def generate_code(self, output_dir: str = "") -> GeneratedCodeModel:
        """Generate Python/C++ code from the flowgraph.

        Unlike grcc, this does NOT block on validation errors — blocks with
        dynamically-resolved ports (e.g. gr-lora_sdr soft_decoding) can still
        produce valid runtime code even when GRC's static validator complains.
        Validation warnings are included in the response for reference.

        Args:
            output_dir: Directory for generated files. If empty, uses a
                        persistent temp directory (files survive the call).
        """
        import os

        fg = self._flowgraph
        fg.rewrite()

        # Collect validation state (non-blocking — never gate on this)
        fg.validate()
        warnings: list[ErrorModel] = [
            format_error_message(elem, msg)
            for elem, msg in fg.iter_error_messages()
        ]
        is_valid = fg.is_valid()

        generate_options = fg.get_option("generate_options") or "no_gui"
        flowgraph_id = fg.get_option("id") or "top_block"

        # Persistent output directory (NOT TemporaryDirectory context manager)
        if not output_dir:
            output_dir = tempfile.mkdtemp(prefix="gr_mcp_gen_")
        os.makedirs(output_dir, exist_ok=True)

        # Generate via Platform's Generator (bypasses grcc validation gate)
        if self._platform_mw:
            generator = self._platform_mw._platform.Generator(fg, output_dir)
        else:
            from gnuradio.grc.core.generator import Generator

            generator = Generator(fg, output_dir)
        generator.write()

        # Read back generated files as strings (preserves existing behavior)
        files: list[GeneratedFileModel] = []
        for root, _dirs, filenames in os.walk(output_dir):
            for fname in sorted(filenames):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                is_main = fname == f"{flowgraph_id}.py" or fname == f"{flowgraph_id}.cpp"
                files.append(
                    GeneratedFileModel(
                        filename=fname,
                        content=content,
                        is_main=is_main,
                    )
                )

        return GeneratedCodeModel(
            files=files,
            generate_options=generate_options,
            flowgraph_id=flowgraph_id,
            output_dir=output_dir,
            is_valid=is_valid,
            warnings=warnings,
        )

    # ──────────────────────────────────────────
    # Gap 3: Flowgraph Options
    # ──────────────────────────────────────────

    def get_flowgraph_options(self) -> FlowgraphOptionsModel:
        """Read the 'options' block parameters that control flowgraph behavior."""
        fg = self._flowgraph
        opts = fg.options_block

        all_params = {}
        for key, param in opts.params.items():
            all_params[key] = param.get_value()

        return FlowgraphOptionsModel(
            id=all_params.get("id", ""),
            title=all_params.get("title", ""),
            author=all_params.get("author", ""),
            description=all_params.get("description", ""),
            generate_options=all_params.get("generate_options", ""),
            run_options=all_params.get("run_options", ""),
            output_language=all_params.get("output_language", ""),
            catch_exceptions=all_params.get("catch_exceptions", ""),
            all_params=all_params,
        )

    def set_flowgraph_options(self, params: dict[str, Any]) -> bool:
        """Set parameters on the 'options' block."""
        fg = self._flowgraph
        opts = fg.options_block
        for key, value in params.items():
            if key in opts.params:
                opts.params[key].set_value(value)
            else:
                raise KeyError(f"Unknown options parameter: {key!r}")
        fg.rewrite()
        return True

    # ──────────────────────────────────────────
    # Gap 4: Embedded Python Blocks
    # ──────────────────────────────────────────

    def create_embedded_python_block(
        self, source_code: str, block_name: Optional[str] = None
    ) -> BlockModel:
        """Create an embedded Python block from source code.

        The source must define a class (typically named 'blk') that inherits
        from a GNU Radio block base class. All __init__ parameters must have
        default values. GRC auto-detects ports and parameters.
        """
        block_name = block_name or get_unique_id(self._flowgraph.blocks, "epy_block")
        block = self._flowgraph.new_block("epy_block")
        assert block is not None, "Failed to create epy_block"
        set_block_name(block, block_name)
        block.params["_source_code"].set_value(source_code)
        block.rewrite()
        return BlockModel.from_block(block)

    # ──────────────────────────────────────────
    # Gap 6: Expression Evaluation
    # ──────────────────────────────────────────

    def evaluate_expression(self, expr: str) -> Any:
        """Evaluate a Python expression in the flowgraph's namespace.

        The namespace includes all imports, variables, parameters, and
        modules defined in the flowgraph.
        """
        fg = self._flowgraph
        fg.rewrite()
        return fg.evaluate(expr)

    # ──────────────────────────────────────────
    # Gap 7: Block Bypass
    # ──────────────────────────────────────────

    def bypass_block(self, block_name: str) -> bool:
        """Bypass a block (pass signal through without processing).

        Only works for single-input, single-output blocks with matching types.
        """
        block_mw = self.get_block(block_name)
        block = block_mw._block
        if not block.can_bypass():
            raise ValueError(
                f"Block {block_name!r} cannot be bypassed "
                f"(requires 1 input and 1 output of the same type)"
            )
        return block.set_bypassed()

    def unbypass_block(self, block_name: str) -> bool:
        """Re-enable a bypassed block."""
        block_mw = self.get_block(block_name)
        block = block_mw._block
        if block.state == "bypassed":
            block.state = "enabled"
            return True
        return False

    # ──────────────────────────────────────────
    # Gap 8: Export Flowgraph Data
    # ──────────────────────────────────────────

    def export_data(self) -> dict:
        """Export the flowgraph as a nested dict (same format as .grc files)."""
        return self._flowgraph.export_data()

    def import_data(self, data: dict) -> bool:
        """Import flowgraph data from a nested dict, replacing current contents."""
        return self._flowgraph.import_data(data)

    @classmethod
    def from_file(
        cls, platform: "PlatformMiddleware", filepath: str = ""
    ) -> FlowGraphMiddleware:
        initial_state = platform._platform.parse_flow_graph(filepath)
        flowgraph = FlowGraph(platform._platform)
        flowgraph.import_data(initial_state)
        return cls(flowgraph, platform=platform)
