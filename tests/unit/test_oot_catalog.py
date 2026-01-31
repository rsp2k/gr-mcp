"""Tests for the OOT module catalog and its data models."""

import json

import pytest

from gnuradio_mcp.oot_catalog import (
    CATALOG,
    OOTDirectoryIndex,
    OOTModuleDetail,
    OOTModuleEntry,
    OOTModuleSummary,
    build_install_example,
)


class TestCatalogIntegrity:
    def test_catalog_has_entries(self):
        assert len(CATALOG) >= 15

    def test_all_entries_have_git_url(self):
        for name, entry in CATALOG.items():
            assert entry.git_url.startswith("https://"), (
                f"{name}: git_url must start with https://"
            )

    def test_module_names_unique(self):
        names = [e.name for e in CATALOG.values()]
        assert len(names) == len(set(names))

    def test_all_categories_nonempty(self):
        for name, entry in CATALOG.items():
            assert entry.category, f"{name}: category must not be empty"

    def test_all_entries_have_description(self):
        for name, entry in CATALOG.items():
            assert entry.description, f"{name}: description must not be empty"

    def test_catalog_keys_match_entry_names(self):
        for key, entry in CATALOG.items():
            assert key == entry.name, (
                f"Key '{key}' does not match entry name '{entry.name}'"
            )

    def test_unknown_module_not_in_catalog(self):
        assert CATALOG.get("nonexistent") is None

    def test_has_preinstalled_modules(self):
        preinstalled = [e for e in CATALOG.values() if e.preinstalled]
        assert len(preinstalled) >= 5

    def test_has_installable_modules(self):
        installable = [e for e in CATALOG.values() if not e.preinstalled]
        assert len(installable) >= 5

    def test_known_preinstalled_modules(self):
        expected = {"osmosdr", "satellites", "gsm", "rds", "fosphor"}
        preinstalled_names = {
            e.name for e in CATALOG.values() if e.preinstalled
        }
        assert expected.issubset(preinstalled_names)

    def test_known_installable_modules(self):
        expected = {"lora_sdr", "ieee802_11", "adsb", "iridium"}
        installable_names = {
            e.name for e in CATALOG.values() if not e.preinstalled
        }
        assert expected.issubset(installable_names)


class TestModels:
    def test_summary_round_trip(self):
        summary = OOTModuleSummary(
            name="test_mod",
            description="A test module",
            category="Testing",
            preinstalled=True,
            installed=True,
        )
        data = json.loads(summary.model_dump_json())
        restored = OOTModuleSummary(**data)
        assert restored.name == "test_mod"
        assert restored.preinstalled is True
        assert restored.installed is True

    def test_summary_installed_default_none(self):
        summary = OOTModuleSummary(
            name="x", description="y", category="z"
        )
        assert summary.installed is None
        assert summary.preinstalled is False

    def test_detail_includes_install_fields(self):
        detail = OOTModuleDetail(
            name="lora_sdr",
            description="LoRa",
            category="IoT",
            git_url="https://github.com/tapparelj/gr-lora_sdr",
            branch="master",
            build_deps=[],
            cmake_args=[],
            homepage="",
            gr_versions="3.10+",
        )
        data = detail.model_dump()
        assert "git_url" in data
        assert "branch" in data
        assert "build_deps" in data
        assert "install_example" in data
        assert "preinstalled" in data

    def test_detail_preinstalled_flag(self):
        detail = OOTModuleDetail(
            name="osmosdr",
            description="HW",
            category="Hardware",
            git_url="https://example.com",
            branch="master",
            build_deps=[],
            cmake_args=[],
            homepage="",
            gr_versions="3.10+",
            preinstalled=True,
        )
        assert detail.preinstalled is True

    def test_directory_index_count(self):
        summaries = [
            OOTModuleSummary(name="a", description="A", category="X"),
            OOTModuleSummary(
                name="b", description="B", category="Y", preinstalled=True
            ),
        ]
        index = OOTDirectoryIndex(modules=summaries, count=2)
        assert index.count == 2
        assert len(index.modules) == 2
        assert index.modules[1].preinstalled is True


class TestBuildInstallExample:
    def test_simple_module(self):
        entry = OOTModuleEntry(
            name="adsb",
            description="ADS-B decoder",
            category="Aviation",
            git_url="https://github.com/mhostetter/gr-adsb",
            branch="main",
        )
        example = build_install_example(entry)
        assert "git_url=" in example
        assert "gr-adsb" in example
        # branch=main is the default, should not appear
        assert "branch=" not in example

    def test_non_default_branch(self):
        entry = OOTModuleEntry(
            name="lora_sdr",
            description="LoRa",
            category="IoT",
            git_url="https://github.com/tapparelj/gr-lora_sdr",
            branch="master",
        )
        example = build_install_example(entry)
        assert 'branch="master"' in example

    def test_with_build_deps(self):
        entry = OOTModuleEntry(
            name="osmosdr",
            description="HW source/sink",
            category="Hardware",
            git_url="https://github.com/osmocom/gr-osmosdr",
            branch="master",
            build_deps=["librtlsdr-dev", "libairspy-dev"],
        )
        example = build_install_example(entry)
        assert "build_deps=" in example
        assert "librtlsdr-dev" in example

    def test_all_catalog_entries_produce_example(self):
        for name, entry in CATALOG.items():
            example = build_install_example(entry)
            assert example.startswith("install_oot_module("), (
                f"{name}: bad install example"
            )
            assert example.endswith(")")
