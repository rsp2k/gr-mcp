from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from gnuradio.grc.core.platform import Platform

from gnuradio_mcp.middlewares.base import ElementMiddleware
from gnuradio_mcp.middlewares.flowgraph import FlowGraphMiddleware
from gnuradio_mcp.models import BlockPathsModel, BlockTypeDetailModel, BlockTypeModel


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

    def _rebuild_library(self) -> int:
        """Rebuild block library with default + OOT paths. Returns block count."""
        all_paths = self.default_block_paths + self._oot_paths
        self._platform.build_library(path=all_paths)
        return len(self._platform.blocks)

    def add_block_path(self, path: str) -> BlockPathsModel:
        """Add a directory of block YAMLs and rebuild the library."""
        path = os.path.expanduser(os.path.abspath(path))
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Block path not found: {path}")
        if path in self._oot_paths:
            return self.get_block_paths()

        before = len(self._platform.blocks)
        self._oot_paths.append(path)
        total = self._rebuild_library()
        return BlockPathsModel(
            paths=self._oot_paths.copy(),
            block_count=total,
            blocks_added=total - before,
        )

    def get_block_paths(self) -> BlockPathsModel:
        """Return current OOT paths and block count."""
        return BlockPathsModel(
            paths=self._oot_paths.copy(),
            block_count=len(self._platform.blocks),
        )

    def make_flowgraph(self, filepath: str = "") -> FlowGraphMiddleware:
        return FlowGraphMiddleware.from_file(self, filepath)

    def save_flowgraph(self, filepath: str, flowgraph: FlowGraphMiddleware) -> None:
        self._platform.save_flow_graph(filepath, flowgraph._flowgraph)

    # ──────────────────────────────────────────
    # Gap 2: Load Existing Flowgraph
    # ──────────────────────────────────────────

    def load_flowgraph(self, filepath: str) -> FlowGraphMiddleware:
        """Load an existing .grc file, replacing the current flowgraph."""
        return FlowGraphMiddleware.from_file(self, filepath)

    # ──────────────────────────────────────────
    # Gap 5: Search/Browse Blocks by Category
    # ──────────────────────────────────────────

    def search_blocks(
        self,
        query: str = "",
        category: Optional[str] = None,
    ) -> list[BlockTypeDetailModel]:
        """Search available blocks by keyword and/or category.

        Args:
            query: Substring match against key, label, or documentation.
            category: Filter to blocks in this category (case-insensitive).
                      Matches if any element in the block's category path
                      contains the string.
        """
        results = []
        query_lower = query.lower()
        category_lower = category.lower() if category else None

        for block_type in self._platform.blocks.values():
            # Category filter
            if category_lower:
                block_cats = [c.lower() for c in (block_type.category or [])]
                if not any(category_lower in c for c in block_cats):
                    continue

            # Query filter (empty query matches everything)
            if query_lower:
                searchable = (
                    block_type.key.lower()
                    + " "
                    + block_type.label.lower()
                )
                if hasattr(block_type, "documentation"):
                    doc = block_type.documentation
                    if isinstance(doc, dict):
                        searchable += " " + doc.get("", "").lower()
                    elif isinstance(doc, str):
                        searchable += " " + doc.lower()

                if query_lower not in searchable:
                    continue

            results.append(BlockTypeDetailModel.from_block_type(block_type))

        return results

    def get_block_categories(self) -> dict[str, list[str]]:
        """Get the full category tree with block keys per category.

        Returns a dict mapping category path (joined with '/') to
        list of block keys in that category.
        """
        categories: dict[str, list[str]] = {}
        for block_type in self._platform.blocks.values():
            cat_path = "/".join(block_type.category) if block_type.category else "(uncategorized)"
            categories.setdefault(cat_path, []).append(block_type.key)
        return dict(sorted(categories.items()))
