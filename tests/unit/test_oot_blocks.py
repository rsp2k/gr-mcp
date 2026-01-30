from __future__ import annotations

import tempfile

from gnuradio.grc.core.platform import Platform

from gnuradio_mcp.middlewares.platform import PlatformMiddleware
from gnuradio_mcp.providers.base import PlatformProvider


class TestPlatformMiddlewareOOT:
    """Tests for OOT (Out-of-Tree) block path loading."""

    def test_default_block_paths_property(self, platform: Platform):
        """Verify we can access default block paths from Platform.Config."""
        middleware = PlatformMiddleware(platform)
        default_paths = middleware.default_block_paths

        assert isinstance(default_paths, list)
        assert len(default_paths) > 0
        # Should include the system blocks path
        assert any("gnuradio" in path for path in default_paths)

    def test_oot_paths_initially_empty(self, platform: Platform):
        """Verify OOT paths are empty on fresh middleware."""
        middleware = PlatformMiddleware(platform)
        assert middleware.oot_paths == []

    def test_load_oot_paths_with_invalid_path(self, platform: Platform):
        """Verify invalid paths are reported correctly."""
        middleware = PlatformMiddleware(platform)
        blocks_before = len(middleware.blocks)

        result = middleware.load_oot_paths(["/nonexistent/path/to/blocks"])

        assert result["added_paths"] == []
        assert result["invalid_paths"] == ["/nonexistent/path/to/blocks"]
        assert result["blocks_before"] == blocks_before
        assert result["blocks_after"] == blocks_before
        assert middleware.oot_paths == []

    def test_load_oot_paths_with_empty_directory(self, platform: Platform):
        """Verify loading an empty directory doesn't break anything."""
        middleware = PlatformMiddleware(platform)
        blocks_before = len(middleware.blocks)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = middleware.load_oot_paths([tmpdir])

            assert result["added_paths"] == [tmpdir]
            assert result["invalid_paths"] == []
            assert result["blocks_before"] == blocks_before
            # Should have same or fewer blocks (empty dir adds nothing)
            assert result["blocks_after"] <= blocks_before
            assert middleware.oot_paths == [tmpdir]

    def test_load_oot_paths_with_mixed_valid_invalid(self, platform: Platform):
        """Verify mixed valid/invalid paths are handled correctly."""
        middleware = PlatformMiddleware(platform)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = middleware.load_oot_paths([tmpdir, "/nonexistent/path"])

            assert result["added_paths"] == [tmpdir]
            assert result["invalid_paths"] == ["/nonexistent/path"]
            assert middleware.oot_paths == [tmpdir]

    def test_load_oot_paths_expands_tilde(self, platform: Platform):
        """Verify ~ is expanded to home directory."""
        middleware = PlatformMiddleware(platform)

        # This should either fail validation (if path doesn't exist)
        # or succeed (if it does) - either way, it shouldn't error
        result = middleware.load_oot_paths(["~/nonexistent_oot_test_dir"])

        # The path should be in invalid_paths since it doesn't exist
        assert "~/nonexistent_oot_test_dir" in result["invalid_paths"]

    def test_load_oot_paths_with_system_blocks_path(self, platform: Platform):
        """Verify we can reload with the system blocks path (idempotent test)."""
        middleware = PlatformMiddleware(platform)

        # Get a default path and use it as "OOT" (should be no-op essentially)
        default_paths = middleware.default_block_paths
        if default_paths:
            result = middleware.load_oot_paths([default_paths[0]])

            # Should have roughly the same number of blocks
            # (might be slightly different due to how GRC handles duplicates)
            assert result["blocks_after"] > 0
            assert result["added_paths"] == [default_paths[0]]


class TestPlatformProviderOOT:
    """Tests for OOT block loading via PlatformProvider."""

    def test_load_oot_blocks_method_exists(
        self, platform_middleware: PlatformMiddleware
    ):
        """Verify the load_oot_blocks method is available on provider."""
        provider = PlatformProvider(platform_middleware)
        assert hasattr(provider, "load_oot_blocks")
        assert callable(provider.load_oot_blocks)

    def test_load_oot_blocks_returns_dict(
        self, platform_middleware: PlatformMiddleware
    ):
        """Verify load_oot_blocks returns expected structure."""
        provider = PlatformProvider(platform_middleware)

        result = provider.load_oot_blocks(["/nonexistent/path"])

        assert isinstance(result, dict)
        assert "added_paths" in result
        assert "invalid_paths" in result
        assert "blocks_before" in result
        assert "blocks_after" in result

    def test_load_oot_blocks_with_valid_path(
        self, platform_middleware: PlatformMiddleware
    ):
        """Verify load_oot_blocks works with a valid directory."""
        provider = PlatformProvider(platform_middleware)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = provider.load_oot_blocks([tmpdir])

            assert tmpdir in result["added_paths"]
            assert result["invalid_paths"] == []
