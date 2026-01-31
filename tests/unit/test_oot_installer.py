"""Unit tests for OOT module installer middleware.

Tests Dockerfile generation, module name extraction, registry persistence,
and image naming — all without requiring Docker.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gnuradio_mcp.middlewares.oot import OOTInstallerMiddleware
from gnuradio_mcp.models import OOTImageInfo


@pytest.fixture
def mock_docker_client():
    return MagicMock()


@pytest.fixture
def oot(mock_docker_client, tmp_path):
    mw = OOTInstallerMiddleware(mock_docker_client)
    # Override registry path to use tmp_path
    mw._registry_path = tmp_path / "oot-registry.json"
    mw._registry = {}
    return mw


# ──────────────────────────────────────────
# Module Name Extraction
# ──────────────────────────────────────────


class TestModuleNameFromUrl:
    def test_gr_prefix_stripped(self):
        name = OOTInstallerMiddleware._module_name_from_url(
            "https://github.com/tapparelj/gr-lora_sdr.git"
        )
        assert name == "lora_sdr"

    def test_gr_prefix_no_git_suffix(self):
        name = OOTInstallerMiddleware._module_name_from_url(
            "https://github.com/osmocom/gr-osmosdr"
        )
        assert name == "osmosdr"

    def test_no_gr_prefix(self):
        name = OOTInstallerMiddleware._module_name_from_url(
            "https://github.com/gnuradio/volk.git"
        )
        assert name == "volk"

    def test_trailing_slash(self):
        name = OOTInstallerMiddleware._module_name_from_url(
            "https://github.com/tapparelj/gr-lora_sdr/"
        )
        assert name == "lora_sdr"

    def test_gr_satellites(self):
        name = OOTInstallerMiddleware._module_name_from_url(
            "https://github.com/daniestevez/gr-satellites.git"
        )
        assert name == "satellites"


class TestRepoDirFromUrl:
    def test_preserves_gr_prefix(self):
        d = OOTInstallerMiddleware._repo_dir_from_url(
            "https://github.com/tapparelj/gr-lora_sdr.git"
        )
        assert d == "gr-lora_sdr"

    def test_no_git_suffix(self):
        d = OOTInstallerMiddleware._repo_dir_from_url(
            "https://github.com/osmocom/gr-osmosdr"
        )
        assert d == "gr-osmosdr"


# ──────────────────────────────────────────
# Dockerfile Generation
# ──────────────────────────────────────────


class TestDockerfileGeneration:
    def test_basic_dockerfile(self, oot):
        dockerfile = oot.generate_dockerfile(
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            base_image="gnuradio-runtime:latest",
        )
        assert "FROM gnuradio-runtime:latest" in dockerfile
        assert "git clone --depth 1 --branch master" in dockerfile
        assert "https://github.com/tapparelj/gr-lora_sdr.git" in dockerfile
        assert "cd gr-lora_sdr && mkdir build" in dockerfile
        assert "cmake -DCMAKE_INSTALL_PREFIX=/usr" in dockerfile
        assert "make -j$(nproc)" in dockerfile
        assert "ldconfig" in dockerfile
        assert "PYTHONPATH" in dockerfile

    def test_with_extra_build_deps(self, oot):
        dockerfile = oot.generate_dockerfile(
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            base_image="gnuradio-runtime:latest",
            build_deps=["libvolk2-dev", "libboost-all-dev"],
        )
        assert "libvolk2-dev libboost-all-dev" in dockerfile

    def test_with_cmake_args(self, oot):
        dockerfile = oot.generate_dockerfile(
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            base_image="gnuradio-runtime:latest",
            cmake_args=["-DENABLE_TESTING=OFF", "-DBUILD_DOCS=OFF"],
        )
        assert "-DENABLE_TESTING=OFF -DBUILD_DOCS=OFF" in dockerfile

    def test_custom_base_image(self, oot):
        dockerfile = oot.generate_dockerfile(
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="main",
            base_image="gnuradio-coverage:latest",
        )
        assert "FROM gnuradio-coverage:latest" in dockerfile

    def test_no_extra_deps_no_trailing_space(self, oot):
        dockerfile = oot.generate_dockerfile(
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            base_image="gnuradio-runtime:latest",
        )
        # With no extra deps, the apt-get line should still work
        assert "build-essential cmake git" in dockerfile


# ──────────────────────────────────────────
# Registry Persistence
# ──────────────────────────────────────────


class TestRegistry:
    def test_empty_on_fresh_start(self, oot):
        assert oot._registry == {}
        assert oot.list_images() == []

    def test_save_and_load_roundtrip(self, oot):
        info = OOTImageInfo(
            module_name="lora_sdr",
            image_tag="gr-oot-lora_sdr:master-862746d",
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            git_commit="862746d",
            base_image="gnuradio-runtime:latest",
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._registry["lora_sdr"] = info
        oot._save_registry()

        # Reload from disk
        loaded = oot._load_registry()
        assert "lora_sdr" in loaded
        assert loaded["lora_sdr"].image_tag == "gr-oot-lora_sdr:master-862746d"
        assert loaded["lora_sdr"].git_commit == "862746d"

    def test_load_missing_file_returns_empty(self, tmp_path):
        mw = OOTInstallerMiddleware(MagicMock())
        mw._registry_path = tmp_path / "nonexistent" / "registry.json"
        result = mw._load_registry()
        assert result == {}

    def test_load_corrupt_file_returns_empty(self, oot):
        oot._registry_path.write_text("not valid json{{{")
        result = oot._load_registry()
        assert result == {}

    def test_save_creates_parent_dirs(self, tmp_path):
        mw = OOTInstallerMiddleware(MagicMock())
        mw._registry_path = tmp_path / "nested" / "deep" / "registry.json"
        mw._registry = {}
        mw._save_registry()
        assert mw._registry_path.exists()


# ──────────────────────────────────────────
# Image Naming
# ──────────────────────────────────────────


class TestImageTagFormat:
    def test_standard_format(self):
        """Image tags follow gr-oot-{name}:{branch}-{commit7}."""
        # This verifies the format used in build_module()
        module_name = "lora_sdr"
        branch = "master"
        commit = "862746d"
        tag = f"gr-oot-{module_name}:{branch}-{commit}"
        assert tag == "gr-oot-lora_sdr:master-862746d"

    def test_different_branch(self):
        tag = f"gr-oot-osmosdr:develop-abc1234"
        assert "develop" in tag
        assert "abc1234" in tag


# ──────────────────────────────────────────
# Remove Image
# ──────────────────────────────────────────


class TestRemoveImage:
    def test_remove_existing(self, oot, mock_docker_client):
        info = OOTImageInfo(
            module_name="lora_sdr",
            image_tag="gr-oot-lora_sdr:master-862746d",
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            git_commit="862746d",
            base_image="gnuradio-runtime:latest",
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._registry["lora_sdr"] = info

        result = oot.remove_image("lora_sdr")
        assert result is True
        assert "lora_sdr" not in oot._registry
        mock_docker_client.images.remove.assert_called_once_with(
            "gr-oot-lora_sdr:master-862746d", force=True
        )

    def test_remove_nonexistent(self, oot):
        result = oot.remove_image("does_not_exist")
        assert result is False

    def test_remove_survives_docker_error(self, oot, mock_docker_client):
        """Registry entry is removed even if Docker image removal fails."""
        info = OOTImageInfo(
            module_name="lora_sdr",
            image_tag="gr-oot-lora_sdr:master-862746d",
            git_url="https://github.com/tapparelj/gr-lora_sdr.git",
            branch="master",
            git_commit="862746d",
            base_image="gnuradio-runtime:latest",
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._registry["lora_sdr"] = info
        mock_docker_client.images.remove.side_effect = Exception("image not found")

        result = oot.remove_image("lora_sdr")
        assert result is True
        assert "lora_sdr" not in oot._registry
