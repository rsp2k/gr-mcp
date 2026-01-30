from typing import Any, Dict, List, Optional

from gnuradio_mcp.middlewares.platform import PlatformMiddleware
from gnuradio_mcp.models import (
    SINK,
    SOURCE,
    BlockModel,
    BlockTypeDetailModel,
    BlockTypeModel,
    ConnectionModel,
    ErrorModel,
    FlowgraphOptionsModel,
    GeneratedCodeModel,
    ParamModel,
    PortModel,
)
from gnuradio_mcp.utils import get_port_by_key


class PlatformProvider:
    def __init__(self, platform_mw: PlatformMiddleware, flowgraph_path: str = ""):
        self._platform_mw = platform_mw
        self._flowgraph_mw = platform_mw.make_flowgraph(flowgraph_path)

    ##############################################
    # Flowgraph Management
    ##############################################

    def get_blocks(self) -> list[BlockModel]:
        return self._flowgraph_mw.blocks

    def make_block(self, block_name: str) -> str:
        block_mw = self._flowgraph_mw.add_block(block_name)
        return block_mw.name

    def remove_block(self, block_name: str) -> bool:
        self._flowgraph_mw.remove_block(block_name)
        return True

    ##############################################
    # Block Management
    ##############################################

    def get_block_params(self, block_name: str) -> List[ParamModel]:
        return self._flowgraph_mw.get_block(block_name).params

    def set_block_params(self, block_name: str, params: Dict[str, Any]) -> bool:
        self._flowgraph_mw.get_block(block_name).set_params(params)
        return True

    def get_block_sources(self, block_name: str) -> list[PortModel]:
        return self._flowgraph_mw.get_block(block_name).sources

    def get_block_sinks(self, block_name: str) -> list[PortModel]:
        return self._flowgraph_mw.get_block(block_name).sinks

    ##############################################
    # Connection Management
    ##############################################

    def get_connections(self) -> list[ConnectionModel]:
        return self._flowgraph_mw.get_connections()

    def connect_blocks(
        self,
        source_block_name: str,
        sink_block_name: str,
        source_port_name: str,
        sink_port_name: str,
    ) -> bool:
        source_port = get_port_by_key(
            self._flowgraph_mw, source_block_name, source_port_name, SOURCE
        )
        sink_port = get_port_by_key(
            self._flowgraph_mw, sink_block_name, sink_port_name, SINK
        )
        self._flowgraph_mw.connect_blocks(source_port, sink_port)
        return True

    def disconnect_blocks(self, source_port: PortModel, sink_port: PortModel) -> bool:
        self._flowgraph_mw.disconnect_blocks(source_port, sink_port)
        return True

    ##############################################
    # Flowgraph Validation
    ##############################################

    def validate_block(self, block_name: str) -> bool:
        return self._flowgraph_mw.get_block(block_name).validate()

    def validate_flowgraph(self) -> bool:
        return self._flowgraph_mw.validate()

    def get_all_errors(self) -> list[ErrorModel]:
        return self._flowgraph_mw.get_all_errors()

    ##############################################
    # Platform Management
    ##############################################

    def get_all_available_blocks(self) -> list[BlockTypeModel]:
        return self._platform_mw.blocks

    def save_flowgraph(self, filepath: str) -> bool:
        self._platform_mw.save_flowgraph(filepath, self._flowgraph_mw)
        return True

    def load_oot_blocks(self, paths: List[str]) -> Dict[str, Any]:
        """Load OOT (Out-of-Tree) block paths into the platform.

        OOT modules are third-party GNU Radio blocks installed separately.
        They may be installed to:
        - /usr/share/gnuradio/grc/blocks (system-wide via package manager)
        - /usr/local/share/gnuradio/grc/blocks (locally-built)
        - Custom paths specified by the user

        Since Platform.build_library() does a full reset, this method
        combines the default block paths with the OOT paths and rebuilds.

        Args:
            paths: List of directory paths containing .block.yml files

        Returns:
            dict with:
                - added_paths: List of valid paths that were added
                - invalid_paths: List of paths that don't exist
                - blocks_before: Block count before reload
                - blocks_after: Block count after reload
        """
        return self._platform_mw.load_oot_paths(paths)

    ##############################################
    # Gap 1: Code Generation
    ##############################################

    def generate_code(self, output_dir: str = "") -> GeneratedCodeModel:
        """Generate Python/C++ code from the current flowgraph.

        Unlike grcc, this does NOT block on validation errors.
        Validation warnings are included in the response for reference.
        """
        return self._flowgraph_mw.generate_code(output_dir)

    ##############################################
    # Gap 2: Load Existing Flowgraph
    ##############################################

    def load_flowgraph(self, filepath: str) -> list[BlockModel]:
        """Load a .grc file, replacing the current flowgraph.

        Returns the blocks in the newly loaded flowgraph.
        """
        self._flowgraph_mw = self._platform_mw.load_flowgraph(filepath)
        return self._flowgraph_mw.blocks

    ##############################################
    # Gap 3: Flowgraph Options
    ##############################################

    def get_flowgraph_options(self) -> FlowgraphOptionsModel:
        """Get the flowgraph-level options (title, author, generate_options, etc.)."""
        return self._flowgraph_mw.get_flowgraph_options()

    def set_flowgraph_options(self, params: Dict[str, Any]) -> bool:
        """Set flowgraph-level options on the 'options' block."""
        return self._flowgraph_mw.set_flowgraph_options(params)

    ##############################################
    # Gap 4: Embedded Python Blocks
    ##############################################

    def create_embedded_python_block(
        self, source_code: str, block_name: Optional[str] = None
    ) -> str:
        """Create an embedded Python block from source code.

        Returns the block name.
        """
        block_model = self._flowgraph_mw.create_embedded_python_block(
            source_code, block_name
        )
        return block_model.name

    ##############################################
    # Gap 5: Search Blocks
    ##############################################

    def search_blocks(
        self, query: str = "", category: Optional[str] = None
    ) -> list[BlockTypeDetailModel]:
        """Search available blocks by keyword and/or category."""
        return self._platform_mw.search_blocks(query, category)

    def get_block_categories(self) -> dict[str, list[str]]:
        """Get all block categories with their block keys."""
        return self._platform_mw.get_block_categories()

    ##############################################
    # Gap 6: Expression Evaluation
    ##############################################

    def evaluate_expression(self, expr: str) -> Any:
        """Evaluate a Python expression in the flowgraph's namespace."""
        return self._flowgraph_mw.evaluate_expression(expr)

    ##############################################
    # Gap 7: Block Bypass
    ##############################################

    def bypass_block(self, block_name: str) -> bool:
        """Bypass a block (pass signal through without processing)."""
        return self._flowgraph_mw.bypass_block(block_name)

    def unbypass_block(self, block_name: str) -> bool:
        """Re-enable a bypassed block."""
        return self._flowgraph_mw.unbypass_block(block_name)

    ##############################################
    # Gap 8: Export/Import Flowgraph Data
    ##############################################

    def export_flowgraph_data(self) -> dict:
        """Export the flowgraph as a nested dict (same format as .grc files)."""
        return self._flowgraph_mw.export_data()

    def import_flowgraph_data(self, data: dict) -> bool:
        """Import flowgraph data from a dict, replacing current contents."""
        return self._flowgraph_mw.import_data(data)
