"""Curated catalog of GNU Radio OOT modules.

Provides browsable metadata so MCP clients can discover available
modules and get the exact parameters needed for install_oot_module()
without guessing URLs or build dependencies.

Modules marked ``preinstalled=True`` ship with the gnuradio-runtime
base Docker image via Debian packages.  They can still be rebuilt
from source (e.g., to get a newer version) via install_oot_module().
"""

from __future__ import annotations

from pydantic import BaseModel


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────


class OOTModuleEntry(BaseModel):
    """A curated OOT module in the directory."""

    name: str
    description: str
    category: str
    git_url: str
    branch: str = "main"
    build_deps: list[str] = []
    cmake_args: list[str] = []
    homepage: str = ""
    gr_versions: str = "3.10+"
    preinstalled: bool = False


class OOTModuleSummary(BaseModel):
    """Compact entry for the directory index."""

    name: str
    description: str
    category: str
    preinstalled: bool = False
    installed: bool | None = None


class OOTDirectoryIndex(BaseModel):
    """Response shape for oot://directory."""

    modules: list[OOTModuleSummary]
    count: int


class OOTModuleDetail(BaseModel):
    """Response shape for oot://directory/{name}."""

    name: str
    description: str
    category: str
    git_url: str
    branch: str
    build_deps: list[str]
    cmake_args: list[str]
    homepage: str
    gr_versions: str
    preinstalled: bool = False
    installed: bool | None = None
    installed_image_tag: str | None = None
    install_example: str = ""


# ──────────────────────────────────────────────
# Catalog Entries
# ──────────────────────────────────────────────


def _entry(
    name: str,
    description: str,
    category: str,
    git_url: str,
    branch: str = "main",
    build_deps: list[str] | None = None,
    cmake_args: list[str] | None = None,
    homepage: str = "",
    gr_versions: str = "3.10+",
    preinstalled: bool = False,
) -> OOTModuleEntry:
    return OOTModuleEntry(
        name=name,
        description=description,
        category=category,
        git_url=git_url,
        branch=branch,
        build_deps=build_deps or [],
        cmake_args=cmake_args or [],
        homepage=homepage,
        gr_versions=gr_versions,
        preinstalled=preinstalled,
    )


CATALOG: dict[str, OOTModuleEntry] = {
    e.name: e
    for e in [
        # ── Pre-installed in gnuradio-runtime base image ──
        _entry(
            name="osmosdr",
            description="Hardware source/sink for RTL-SDR, Airspy, HackRF, and more",
            category="Hardware",
            git_url="https://github.com/osmocom/gr-osmosdr",
            branch="master",
            build_deps=["librtlsdr-dev", "libairspy-dev", "libhackrf-dev"],
            homepage="https://osmocom.org/projects/gr-osmosdr/wiki",
            preinstalled=True,
        ),
        _entry(
            name="satellites",
            description="Satellite telemetry decoders (AX.25, CCSDS, AO-73, etc.)",
            category="Satellite",
            git_url="https://github.com/daniestevez/gr-satellites",
            branch="main",
            build_deps=["python3-construct", "python3-requests"],
            homepage="https://gr-satellites.readthedocs.io/",
            preinstalled=True,
        ),
        _entry(
            name="gsm",
            description="GSM/GPRS burst receiver and channel decoder",
            category="Cellular",
            git_url="https://github.com/ptrkrysik/gr-gsm",
            branch="master",
            build_deps=["libosmocore-dev"],
            homepage="https://github.com/ptrkrysik/gr-gsm",
            preinstalled=True,
        ),
        _entry(
            name="rds",
            description="FM RDS/RBDS (Radio Data System) decoder",
            category="Broadcast",
            git_url="https://github.com/bastibl/gr-rds",
            branch="main",
            homepage="https://github.com/bastibl/gr-rds",
            preinstalled=True,
        ),
        _entry(
            name="fosphor",
            description="GPU-accelerated real-time spectrum display (OpenCL)",
            category="Visualization",
            git_url="https://github.com/osmocom/gr-fosphor",
            branch="master",
            build_deps=["libfreetype6-dev", "ocl-icd-opencl-dev"],
            homepage="https://osmocom.org/projects/sdr/wiki/Fosphor",
            preinstalled=True,
        ),
        _entry(
            name="air_modes",
            description="Mode-S/ADS-B aircraft transponder decoder (1090 MHz)",
            category="Aviation",
            git_url="https://github.com/bistromath/gr-air-modes",
            branch="master",
            homepage="https://github.com/bistromath/gr-air-modes",
            preinstalled=True,
        ),
        _entry(
            name="funcube",
            description="Funcube Dongle Pro/Pro+ controller and source block",
            category="Hardware",
            git_url="https://github.com/dl1ksv/gr-funcube",
            branch="master",
            homepage="https://github.com/dl1ksv/gr-funcube",
            preinstalled=True,
        ),
        _entry(
            name="hpsdr",
            description="OpenHPSDR Protocol 1 interface for HPSDR hardware",
            category="Hardware",
            git_url="https://github.com/Tom-McDermott/gr-hpsdr",
            branch="master",
            homepage="https://github.com/Tom-McDermott/gr-hpsdr",
            preinstalled=True,
        ),
        _entry(
            name="iqbal",
            description="Blind IQ imbalance estimator and correction",
            category="Analysis",
            git_url="https://github.com/osmocom/gr-iqbal",
            branch="master",
            homepage="https://git.osmocom.org/gr-iqbal",
            preinstalled=True,
        ),
        _entry(
            name="limesdr",
            description="LimeSDR source/sink blocks (LMS7002M)",
            category="Hardware",
            git_url="https://github.com/myriadrf/gr-limesdr",
            branch="master",
            homepage="https://wiki.myriadrf.org/Gr-limesdr_Plugin_for_GNURadio",
            preinstalled=True,
        ),
        _entry(
            name="radar",
            description="Radar signal processing toolbox (FMCW, OFDM radar)",
            category="Analysis",
            git_url="https://github.com/kit-cel/gr-radar",
            branch="master",
            homepage="https://github.com/kit-cel/gr-radar",
            preinstalled=True,
        ),
        _entry(
            name="satnogs",
            description="SatNOGS satellite ground station decoders and deframers",
            category="Satellite",
            git_url="https://gitlab.com/librespacefoundation/satnogs/gr-satnogs",
            branch="master",
            homepage="https://gitlab.com/librespacefoundation/satnogs/gr-satnogs",
            preinstalled=True,
        ),
        # ── Installable via install_oot_module ──
        _entry(
            name="lora_sdr",
            description="LoRa PHY transceiver (CSS modulation/demodulation)",
            category="IoT",
            git_url="https://github.com/tapparelj/gr-lora_sdr",
            branch="master",
            homepage="https://github.com/tapparelj/gr-lora_sdr",
        ),
        _entry(
            name="ieee802_11",
            description="IEEE 802.11a/g/p OFDM transceiver",
            category="WiFi",
            git_url="https://github.com/bastibl/gr-ieee802-11",
            branch="maint-3.10",
            build_deps=["castxml"],
            homepage="https://github.com/bastibl/gr-ieee802-11",
        ),
        _entry(
            name="ieee802_15_4",
            description="IEEE 802.15.4 (Zigbee) O-QPSK transceiver",
            category="IoT",
            git_url="https://github.com/bastibl/gr-ieee802-15-4",
            branch="maint-3.10",
            build_deps=["castxml"],
            homepage="https://github.com/bastibl/gr-ieee802-15-4",
        ),
        _entry(
            name="adsb",
            description="ADS-B (1090 MHz) aircraft transponder decoder",
            category="Aviation",
            git_url="https://github.com/mhostetter/gr-adsb",
            branch="maint-3.10",
            homepage="https://github.com/mhostetter/gr-adsb",
        ),
        _entry(
            name="iridium",
            description="Iridium satellite burst detector and demodulator",
            category="Satellite",
            git_url="https://github.com/muccc/gr-iridium",
            branch="master",
            homepage="https://github.com/muccc/gr-iridium",
        ),
        _entry(
            name="inspector",
            description="Signal analysis toolbox (energy detection, OFDM estimation)",
            category="Analysis",
            git_url="https://github.com/gnuradio/gr-inspector",
            branch="master",
            build_deps=["qtbase5-dev", "libqwt-qt5-dev"],
            homepage="https://github.com/gnuradio/gr-inspector",
            gr_versions="3.9 (master branch has API compat issues with 3.10)",
        ),
        _entry(
            name="nrsc5",
            description="HD Radio (NRSC-5) digital broadcast decoder",
            category="Broadcast",
            git_url="https://github.com/argilo/gr-nrsc5",
            branch="master",
            build_deps=["autoconf", "automake", "libtool"],
            homepage="https://github.com/argilo/gr-nrsc5",
        ),
    ]
}


def build_install_example(entry: OOTModuleEntry) -> str:
    """Format a copy-paste install_oot_module() call for this module."""
    parts = [f'install_oot_module(git_url="{entry.git_url}"']
    if entry.branch != "main":
        parts.append(f', branch="{entry.branch}"')
    if entry.build_deps:
        deps = ", ".join(f'"{d}"' for d in entry.build_deps)
        parts.append(f", build_deps=[{deps}]")
    if entry.cmake_args:
        args = ", ".join(f'"{a}"' for a in entry.cmake_args)
        parts.append(f", cmake_args=[{args}]")
    parts.append(")")
    return "".join(parts)
