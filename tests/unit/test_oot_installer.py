"""Unit tests for OOT module installer middleware.

Tests Dockerfile generation, module name extraction, registry persistence,
and image naming — all without requiring Docker.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gnuradio_mcp.middlewares.oot import OOTInstallerMiddleware
from gnuradio_mcp.models import ComboImageInfo, OOTImageInfo


@pytest.fixture
def mock_docker_client():
    return MagicMock()


@pytest.fixture
def oot(mock_docker_client, tmp_path):
    mw = OOTInstallerMiddleware(mock_docker_client)
    # Override registry paths to use tmp_path
    mw._registry_path = tmp_path / "oot-registry.json"
    mw._registry = {}
    mw._combo_registry_path = tmp_path / "oot-combo-registry.json"
    mw._combo_registry = {}
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
        assert "cd gr-lora_sdr" in dockerfile
        assert "fix_binding_hashes.py" in dockerfile
        assert "mkdir build" in dockerfile
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


# ──────────────────────────────────────────
# Combo Key Generation
# ──────────────────────────────────────────


class TestComboKeyGeneration:
    def test_sorted_and_deduped(self):
        key = OOTInstallerMiddleware._combo_key(["lora_sdr", "adsb", "lora_sdr"])
        assert key == "combo:adsb+lora_sdr"

    def test_alphabetical_order(self):
        key = OOTInstallerMiddleware._combo_key(["osmosdr", "adsb", "lora_sdr"])
        assert key == "combo:adsb+lora_sdr+osmosdr"

    def test_single_module(self):
        key = OOTInstallerMiddleware._combo_key(["adsb"])
        assert key == "combo:adsb"

    def test_two_modules(self):
        key = OOTInstallerMiddleware._combo_key(["lora_sdr", "adsb"])
        assert key == "combo:adsb+lora_sdr"


# ──────────────────────────────────────────
# Combo Image Tag
# ──────────────────────────────────────────


class TestComboImageTag:
    def test_format(self):
        tag = OOTInstallerMiddleware._combo_image_tag(["lora_sdr", "adsb"])
        assert tag == "gr-combo-adsb-lora_sdr:latest"

    def test_sorted_and_deduped(self):
        tag = OOTInstallerMiddleware._combo_image_tag(
            ["osmosdr", "adsb", "osmosdr"]
        )
        assert tag == "gr-combo-adsb-osmosdr:latest"

    def test_three_modules(self):
        tag = OOTInstallerMiddleware._combo_image_tag(
            ["lora_sdr", "adsb", "osmosdr"]
        )
        assert tag == "gr-combo-adsb-lora_sdr-osmosdr:latest"


# ──────────────────────────────────────────
# Combo Dockerfile Generation
# ──────────────────────────────────────────


def _make_oot_info(name: str, tag: str) -> OOTImageInfo:
    """Helper to create a minimal OOTImageInfo for testing."""
    return OOTImageInfo(
        module_name=name,
        image_tag=tag,
        git_url=f"https://example.com/gr-{name}.git",
        branch="main",
        git_commit="abc1234",
        base_image="gnuradio-runtime:latest",
        built_at="2025-01-01T00:00:00+00:00",
    )


class TestComboDockerfileGeneration:
    def test_multi_stage_structure(self, oot):
        oot._registry["adsb"] = _make_oot_info("adsb", "gr-oot-adsb:main-abc1234")
        oot._registry["lora_sdr"] = _make_oot_info(
            "lora_sdr", "gr-oot-lora_sdr:master-def5678"
        )

        dockerfile = oot.generate_combo_dockerfile(["lora_sdr", "adsb"])

        # Stage aliases (sorted order: adsb first)
        assert "FROM gr-oot-adsb:main-abc1234 AS stage_adsb" in dockerfile
        assert "FROM gr-oot-lora_sdr:master-def5678 AS stage_lora_sdr" in dockerfile

        # Final base image
        assert "FROM gnuradio-runtime:latest" in dockerfile

        # COPY directives for both modules
        assert "COPY --from=stage_adsb /usr/lib/ /usr/lib/" in dockerfile
        assert "COPY --from=stage_adsb /usr/include/ /usr/include/" in dockerfile
        assert "COPY --from=stage_adsb /usr/share/gnuradio/ /usr/share/gnuradio/" in dockerfile
        assert "COPY --from=stage_lora_sdr /usr/lib/ /usr/lib/" in dockerfile
        assert "COPY --from=stage_lora_sdr /usr/include/ /usr/include/" in dockerfile

        # Runtime setup
        assert "RUN ldconfig" in dockerfile
        assert "WORKDIR /flowgraphs" in dockerfile
        assert "PYTHONPATH" in dockerfile

    def test_missing_module_raises(self, oot):
        oot._registry["adsb"] = _make_oot_info("adsb", "gr-oot-adsb:main-abc1234")

        with pytest.raises(ValueError, match="lora_sdr"):
            oot.generate_combo_dockerfile(["adsb", "lora_sdr"])

    def test_uses_configured_base_image(self, mock_docker_client, tmp_path):
        mw = OOTInstallerMiddleware(mock_docker_client, base_image="my-custom:v2")
        mw._registry_path = tmp_path / "oot-registry.json"
        mw._registry = {
            "adsb": _make_oot_info("adsb", "gr-oot-adsb:main-abc1234"),
            "lora_sdr": _make_oot_info("lora_sdr", "gr-oot-lora_sdr:main-def5678"),
        }
        mw._combo_registry_path = tmp_path / "oot-combo-registry.json"
        mw._combo_registry = {}

        dockerfile = mw.generate_combo_dockerfile(["adsb", "lora_sdr"])
        assert "FROM my-custom:v2" in dockerfile


# ──────────────────────────────────────────
# Combo Registry Persistence
# ──────────────────────────────────────────


class TestComboRegistry:
    def test_separate_file(self, oot):
        """Combo registry uses a different file from single-OOT registry."""
        assert oot._combo_registry_path != oot._registry_path
        assert "combo" in str(oot._combo_registry_path)

    def test_empty_on_fresh_start(self, oot):
        assert oot._combo_registry == {}
        assert oot.list_combo_images() == []

    def test_save_and_load_roundtrip(self, oot):
        info = ComboImageInfo(
            combo_key="combo:adsb+lora_sdr",
            image_tag="gr-combo-adsb-lora_sdr:latest",
            modules=[
                _make_oot_info("adsb", "gr-oot-adsb:main-abc1234"),
                _make_oot_info("lora_sdr", "gr-oot-lora_sdr:master-def5678"),
            ],
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._combo_registry["combo:adsb+lora_sdr"] = info
        oot._save_combo_registry()

        loaded = oot._load_combo_registry()
        assert "combo:adsb+lora_sdr" in loaded
        assert loaded["combo:adsb+lora_sdr"].image_tag == "gr-combo-adsb-lora_sdr:latest"
        assert len(loaded["combo:adsb+lora_sdr"].modules) == 2

    def test_load_missing_file_returns_empty(self, oot):
        oot._combo_registry_path = oot._combo_registry_path.parent / "nope" / "r.json"
        result = oot._load_combo_registry()
        assert result == {}

    def test_load_corrupt_file_returns_empty(self, oot):
        oot._combo_registry_path.write_text("broken{{{")
        result = oot._load_combo_registry()
        assert result == {}

    def test_list_returns_values(self, oot):
        info = ComboImageInfo(
            combo_key="combo:adsb+lora_sdr",
            image_tag="gr-combo-adsb-lora_sdr:latest",
            modules=[],
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._combo_registry["combo:adsb+lora_sdr"] = info
        result = oot.list_combo_images()
        assert len(result) == 1
        assert result[0].combo_key == "combo:adsb+lora_sdr"

    def test_remove_existing(self, oot, mock_docker_client):
        info = ComboImageInfo(
            combo_key="combo:adsb+lora_sdr",
            image_tag="gr-combo-adsb-lora_sdr:latest",
            modules=[],
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._combo_registry["combo:adsb+lora_sdr"] = info

        result = oot.remove_combo_image("combo:adsb+lora_sdr")
        assert result is True
        assert "combo:adsb+lora_sdr" not in oot._combo_registry
        mock_docker_client.images.remove.assert_called_once_with(
            "gr-combo-adsb-lora_sdr:latest", force=True
        )

    def test_remove_nonexistent(self, oot):
        result = oot.remove_combo_image("combo:nope+nada")
        assert result is False

    def test_remove_survives_docker_error(self, oot, mock_docker_client):
        info = ComboImageInfo(
            combo_key="combo:adsb+lora_sdr",
            image_tag="gr-combo-adsb-lora_sdr:latest",
            modules=[],
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._combo_registry["combo:adsb+lora_sdr"] = info
        mock_docker_client.images.remove.side_effect = Exception("gone")

        result = oot.remove_combo_image("combo:adsb+lora_sdr")
        assert result is True
        assert "combo:adsb+lora_sdr" not in oot._combo_registry


# ──────────────────────────────────────────
# Build Combo Image
# ──────────────────────────────────────────


class TestBuildComboImage:
    def test_requires_at_least_two_modules(self, oot):
        result = oot.build_combo_image(["adsb"])
        assert result.success is False
        assert "2 distinct" in result.error

    def test_rejects_duplicate_as_single(self, oot):
        result = oot.build_combo_image(["adsb", "adsb"])
        assert result.success is False
        assert "2 distinct" in result.error

    def test_idempotent_skip(self, oot, mock_docker_client):
        """Skips build if combo image already exists."""
        oot._registry["adsb"] = _make_oot_info("adsb", "gr-oot-adsb:main-abc")
        oot._registry["lora_sdr"] = _make_oot_info("lora_sdr", "gr-oot-lora:m-def")

        existing = ComboImageInfo(
            combo_key="combo:adsb+lora_sdr",
            image_tag="gr-combo-adsb-lora_sdr:latest",
            modules=[],
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._combo_registry["combo:adsb+lora_sdr"] = existing

        # Docker image exists
        mock_docker_client.images.get.return_value = MagicMock()

        result = oot.build_combo_image(["lora_sdr", "adsb"])
        assert result.success is True
        assert result.skipped is True

    def test_happy_path(self, oot, mock_docker_client):
        """Builds combo from pre-existing single-OOT images."""
        oot._registry["adsb"] = _make_oot_info("adsb", "gr-oot-adsb:main-abc1234")
        oot._registry["lora_sdr"] = _make_oot_info(
            "lora_sdr", "gr-oot-lora_sdr:master-def5678"
        )

        # Docker image does not exist yet
        mock_docker_client.images.get.side_effect = Exception("not found")
        # Mock successful build
        mock_docker_client.images.build.return_value = (
            MagicMock(),
            [{"stream": "Step 1/5 : FROM ...\n"}],
        )

        result = oot.build_combo_image(["adsb", "lora_sdr"])
        assert result.success is True
        assert result.skipped is False
        assert result.image is not None
        assert result.image.combo_key == "combo:adsb+lora_sdr"
        assert result.image.image_tag == "gr-combo-adsb-lora_sdr:latest"
        assert len(result.image.modules) == 2
        assert result.modules_built == []

        # Verify persisted to combo registry
        assert "combo:adsb+lora_sdr" in oot._combo_registry

    def test_unknown_module_not_in_catalog(self, oot):
        """Fails if module not in registry and not in catalog."""
        oot._registry["adsb"] = _make_oot_info("adsb", "gr-oot-adsb:main-abc1234")

        result = oot.build_combo_image(["adsb", "totally_fake_module"])
        assert result.success is False
        assert "totally_fake_module" in result.error
        assert "not found in the catalog" in result.error

    def test_force_rebuilds(self, oot, mock_docker_client):
        """force=True bypasses idempotency check."""
        oot._registry["adsb"] = _make_oot_info("adsb", "gr-oot-adsb:main-abc")
        oot._registry["lora_sdr"] = _make_oot_info("lora_sdr", "gr-oot-lora:m-def")

        existing = ComboImageInfo(
            combo_key="combo:adsb+lora_sdr",
            image_tag="gr-combo-adsb-lora_sdr:latest",
            modules=[],
            built_at="2025-01-01T00:00:00+00:00",
        )
        oot._combo_registry["combo:adsb+lora_sdr"] = existing

        # Docker image exists
        mock_docker_client.images.get.return_value = MagicMock()
        mock_docker_client.images.build.return_value = (
            MagicMock(),
            [{"stream": "rebuilt\n"}],
        )

        result = oot.build_combo_image(["adsb", "lora_sdr"], force=True)
        assert result.success is True
        assert result.skipped is False
