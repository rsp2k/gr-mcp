from __future__ import annotations

import os
from pathlib import Path

from gnuradio.grc.core.platform import Platform

from gnuradio_mcp.middlewares.base import ElementMiddleware
from gnuradio_mcp.middlewares.flowgraph import FlowGraphMiddleware
from gnuradio_mcp.models import BlockTypeModel


class PlatformMiddleware(ElementMiddleware):
    def __init__(self, platform: Platform):
        super().__init__(platform)
        self._platform = self._element
        self._oot_paths: list[str] = []

    @property
    def blocks(self) -> list[BlockTypeModel]:
        return [
            BlockTypeModel.from_block_type(block)
            for block in self._platform.blocks.values()
        ]

    @property
    def default_block_paths(self) -> list[str]:
        """Get the default block paths from Platform.Config."""
        return list(self._platform.config.block_paths)

    @property
    def oot_paths(self) -> list[str]:
        """Get the currently loaded OOT paths."""
        return self._oot_paths.copy()

    def load_oot_paths(self, paths: list[str]) -> dict:
        """Load OOT (Out-of-Tree) block paths into the platform.

        Since Platform.build_library() does a full reset (clears all blocks),
        we must rebuild with default_paths + oot_paths combined.

        Args:
            paths: List of directory paths containing .block.yml files

        Returns:
            dict with:
                - added_paths: List of valid paths that were added
                - invalid_paths: List of paths that don't exist
                - blocks_before: Block count before reload
                - blocks_after: Block count after reload
        """
        blocks_before = len(self._platform.blocks)

        # Validate paths exist
        valid_paths = []
        invalid_paths = []
        for path in paths:
            expanded = os.path.expanduser(path)
            if Path(expanded).is_dir():
                valid_paths.append(expanded)
            else:
                invalid_paths.append(path)

        if not valid_paths:
            return {
                "added_paths": [],
                "invalid_paths": invalid_paths,
                "blocks_before": blocks_before,
                "blocks_after": blocks_before,
            }

        # Combine default paths with OOT paths
        combined_paths = self.default_block_paths + valid_paths

        # Rebuild the library with all paths
        self._platform.build_library(path=combined_paths)

        # Track the OOT paths we've loaded
        self._oot_paths = valid_paths

        blocks_after = len(self._platform.blocks)

        return {
            "added_paths": valid_paths,
            "invalid_paths": invalid_paths,
            "blocks_before": blocks_before,
            "blocks_after": blocks_after,
        }

    def make_flowgraph(self, filepath: str = "") -> FlowGraphMiddleware:
        return FlowGraphMiddleware.from_file(self, filepath)

    def save_flowgraph(self, filepath: str, flowgraph: FlowGraphMiddleware) -> None:
        self._platform.save_flow_graph(filepath, flowgraph._flowgraph)
