from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from gnuradio_mcp.middlewares.docker import HOST_COVERAGE_BASE, DockerMiddleware
from gnuradio_mcp.middlewares.oot import OOTInstallerMiddleware
from gnuradio_mcp.middlewares.thrift import ThriftMiddleware
from gnuradio_mcp.middlewares.xmlrpc import XmlRpcMiddleware
from gnuradio_mcp.models import (
    ComboImageInfo,
    ComboImageResult,
    ConnectionInfoModel,
    ContainerModel,
    CoverageDataModel,
    CoverageReportModel,
    KnobModel,
    KnobPropertiesModel,
    OOTDetectionResult,
    OOTImageInfo,
    OOTInstallResult,
    PerfCounterModel,
    RuntimeStatusModel,
    ScreenshotModel,
    ThriftConnectionInfoModel,
    VariableModel,
)

logger = logging.getLogger(__name__)


class RuntimeProvider:
    """Business logic for runtime flowgraph control.

    Coordinates Docker (container lifecycle), XML-RPC (variable control),
    and ControlPort/Thrift (advanced control with perf counters).

    Tracks the active connection so convenience methods like get_variable()
    work without repeating the URL each call.
    """

    def __init__(
        self,
        docker_mw: DockerMiddleware | None = None,
        oot_mw: OOTInstallerMiddleware | None = None,
    ):
        self._docker = docker_mw
        self._oot = oot_mw
        self._xmlrpc: XmlRpcMiddleware | None = None
        self._thrift: ThriftMiddleware | None = None
        self._active_container: str | None = None

    @property
    def _has_docker(self) -> bool:
        return self._docker is not None

    @property
    def _has_oot(self) -> bool:
        return self._oot is not None

    def _require_docker(self) -> DockerMiddleware:
        if self._docker is None:
            raise RuntimeError(
                "Docker is not available. Install the 'docker' package "
                "and ensure the Docker daemon is running."
            )
        return self._docker

    def _require_xmlrpc(self) -> XmlRpcMiddleware:
        if self._xmlrpc is None:
            raise RuntimeError(
                "Not connected to a flowgraph. Use connect() or "
                "connect_to_container() first."
            )
        return self._xmlrpc

    def _require_thrift(self) -> ThriftMiddleware:
        if self._thrift is None:
            raise RuntimeError(
                "Not connected via ControlPort. Use connect_controlport() or "
                "connect_to_container_controlport() first."
            )
        return self._thrift

    def _require_oot(self) -> OOTInstallerMiddleware:
        if self._oot is None:
            raise RuntimeError(
                "OOT installer requires Docker. Install the 'docker' package "
                "and ensure the Docker daemon is running."
            )
        return self._oot

    # ──────────────────────────────────────────
    # Container Lifecycle
    # ──────────────────────────────────────────

    def launch_flowgraph(
        self,
        flowgraph_path: str,
        name: str | None = None,
        xmlrpc_port: int = 0,
        enable_vnc: bool = False,
        enable_coverage: bool = False,
        enable_controlport: bool = False,
        controlport_port: int = 9090,
        enable_perf_counters: bool = True,
        device_paths: list[str] | None = None,
        image: str | None = None,
        auto_image: bool = False,
    ) -> ContainerModel:
        """Launch a flowgraph in a Docker container with Xvfb.

        Args:
            flowgraph_path: Path to the .py flowgraph file
            name: Container name (defaults to 'gr-{stem}')
            xmlrpc_port: Port for XML-RPC variable control (0 = auto-allocate)
            enable_vnc: Enable VNC server for visual debugging
            enable_coverage: Enable Python code coverage collection
            enable_controlport: Enable ControlPort/Thrift for advanced control
            controlport_port: Port for ControlPort (default 9090)
            enable_perf_counters: Enable performance counters (requires controlport)
            device_paths: Host device paths to pass through
            image: Docker image to use (e.g., 'gnuradio-lora-runtime:latest')
            auto_image: Automatically detect required OOT modules and build
                appropriate Docker image. If True and image is not specified,
                analyzes the flowgraph to determine OOT dependencies and
                builds a single-OOT or combo image as needed.
        """
        docker = self._require_docker()

        # Auto-detect and build image if requested
        if auto_image and image is None and self._has_oot:
            image = self._auto_select_image(flowgraph_path)

        if name is None:
            name = f"gr-{Path(flowgraph_path).stem}"
        return docker.launch(
            flowgraph_path=flowgraph_path,
            name=name,
            xmlrpc_port=xmlrpc_port,
            enable_vnc=enable_vnc,
            enable_coverage=enable_coverage,
            enable_controlport=enable_controlport,
            controlport_port=controlport_port,
            enable_perf_counters=enable_perf_counters,
            device_paths=device_paths,
            image=image,
        )

    def _auto_select_image(self, flowgraph_path: str) -> str | None:
        """Detect OOT modules and build/select appropriate image.

        Auto-builds missing modules from catalog when needed.
        """
        from gnuradio_mcp.oot_catalog import CATALOG

        oot = self._require_oot()
        detection = oot.detect_required_modules(flowgraph_path)

        if not detection.detected_modules:
            logger.info("No OOT modules detected, using base runtime image")
            return detection.recommended_image

        modules = detection.detected_modules
        logger.info("Detected OOT modules: %s", modules)

        if len(modules) == 1:
            # Single module - ensure it's built
            module = modules[0]
            if module not in oot._registry:
                entry = CATALOG.get(module)
                if entry:
                    logger.info("Auto-building module '%s' from catalog", module)
                    result = oot.build_module(
                        git_url=entry.git_url,
                        branch=entry.branch,
                        build_deps=entry.build_deps or None,
                        cmake_args=entry.cmake_args or None,
                    )
                    if not result.success:
                        logger.error("Auto-build of '%s' failed: %s", module, result.error)
                        return None
            info = oot._registry.get(module)
            return info.image_tag if info else None
        else:
            # Multiple modules - build combo
            logger.info("Building combo image for modules: %s", modules)
            result = oot.build_combo_image(modules)
            if result.success and result.image:
                return result.image.image_tag
            logger.error("Combo image build failed: %s", result.error)
            return None

    def list_containers(self) -> list[ContainerModel]:
        """List all gr-mcp managed containers."""
        docker = self._require_docker()
        return docker.list_containers()

    def stop_flowgraph(self, name: str) -> bool:
        """Stop a running flowgraph container."""
        docker = self._require_docker()
        return docker.stop(name)

    def remove_flowgraph(self, name: str, force: bool = False) -> bool:
        """Remove a flowgraph container."""
        docker = self._require_docker()
        return docker.remove(name, force=force)

    # ──────────────────────────────────────────
    # Connection Management
    # ──────────────────────────────────────────

    def connect(self, url: str) -> ConnectionInfoModel:
        """Connect to a GNU Radio XML-RPC endpoint."""
        self._xmlrpc = XmlRpcMiddleware.connect(url)
        self._active_container = None
        # Parse port from URL
        from urllib.parse import urlparse

        parsed = urlparse(url)
        port = parsed.port or 8080
        return self._xmlrpc.get_connection_info(xmlrpc_port=port)

    def connect_to_container(self, name: str) -> ConnectionInfoModel:
        """Connect to a flowgraph by container name (resolves port automatically)."""
        docker = self._require_docker()
        port = docker.get_xmlrpc_port(name)
        url = f"http://localhost:{port}"
        self._xmlrpc = XmlRpcMiddleware.connect(url)
        self._active_container = name
        return self._xmlrpc.get_connection_info(container_name=name, xmlrpc_port=port)

    def disconnect(self) -> bool:
        """Disconnect from the current XML-RPC endpoint."""
        if self._xmlrpc is not None:
            self._xmlrpc.close()
            self._xmlrpc = None
        if self._thrift is not None:
            self._thrift.close()
            self._thrift = None
        self._active_container = None
        return True

    # ──────────────────────────────────────────
    # ControlPort/Thrift Connection (Phase 2)
    # ──────────────────────────────────────────

    def connect_controlport(
        self,
        host: str = "127.0.0.1",
        port: int = 9090,
    ) -> ThriftConnectionInfoModel:
        """Connect to a GNU Radio ControlPort/Thrift endpoint.

        ControlPort provides richer functionality than XML-RPC:
        - Native type support (complex numbers, vectors)
        - Performance counters (throughput, timing, buffer utilization)
        - Knob metadata (units, min/max, descriptions)
        - PMT message injection
        - Regex-based knob queries

        Args:
            host: Hostname or IP address
            port: ControlPort Thrift port (default 9090)
        """
        self._thrift = ThriftMiddleware.connect(host, port)
        self._active_container = None
        return self._thrift.get_connection_info()

    def connect_to_container_controlport(self, name: str) -> ThriftConnectionInfoModel:
        """Connect to a flowgraph's ControlPort by container name.

        Resolves the ControlPort port from container labels automatically.

        Args:
            name: Container name
        """
        docker = self._require_docker()
        if not docker.is_controlport_enabled(name):
            raise RuntimeError(
                f"Container '{name}' was not launched with ControlPort enabled. "
                f"Use launch_flowgraph(..., enable_controlport=True)"
            )
        port = docker.get_controlport_port(name)
        self._thrift = ThriftMiddleware.connect("127.0.0.1", port)
        self._active_container = name
        return self._thrift.get_connection_info(container_name=name)

    def disconnect_controlport(self) -> bool:
        """Disconnect from the current ControlPort endpoint."""
        if self._thrift is not None:
            self._thrift.close()
            self._thrift = None
        return True

    # ──────────────────────────────────────────
    # ControlPort Knob Operations (Phase 2)
    # ──────────────────────────────────────────

    def get_knobs(self, pattern: str = "") -> list[KnobModel]:
        """Get ControlPort knobs, optionally filtered by regex pattern.

        Knobs are named using the pattern: block_alias::varname
        (e.g., "sig_source0::frequency")

        Args:
            pattern: Regex pattern for filtering knob names.
                     Empty string returns all knobs.

        Examples:
            get_knobs("")  # All knobs
            get_knobs(".*frequency.*")  # All frequency-related knobs
            get_knobs("sig_source0::.*")  # All knobs for sig_source0
        """
        thrift = self._require_thrift()
        return thrift.get_knobs(pattern)

    def set_knobs(self, knobs: dict[str, Any]) -> bool:
        """Set multiple ControlPort knobs atomically.

        Args:
            knobs: Dict mapping knob names to new values.
                   Types are inferred from existing knobs.

        Example:
            set_knobs({
                "sig_source0::frequency": 1000000.0,
                "sig_source0::amplitude": 0.5,
            })
        """
        thrift = self._require_thrift()
        return thrift.set_knobs(knobs)

    def get_knob_properties(self, names: list[str]) -> list[KnobPropertiesModel]:
        """Get metadata (units, min/max, description) for specified knobs.

        Args:
            names: List of knob names to query. Empty list returns all properties.

        Returns:
            List of KnobPropertiesModel with rich metadata.
        """
        thrift = self._require_thrift()
        return thrift.get_knob_properties(names)

    def get_performance_counters(
        self, block: str | None = None
    ) -> list[PerfCounterModel]:
        """Get performance metrics for blocks via ControlPort.

        Requires the flowgraph to be launched with enable_controlport=True
        and enable_perf_counters=True (default).

        Args:
            block: Optional block alias to filter (e.g., "sig_source0").
                   If None, returns metrics for all blocks.

        Returns:
            List of PerfCounterModel with throughput, timing, and buffer stats.
        """
        thrift = self._require_thrift()
        return thrift.get_performance_counters(block)

    def post_message(self, block: str, port: str, message: Any) -> bool:
        """Send a PMT message to a block's message port via ControlPort.

        Args:
            block: Block alias (e.g., "msg_sink0")
            port: Message port name (e.g., "in")
            message: Message to send (will be converted to PMT if needed)

        Example:
            # Send a simple string message
            post_message("pdu_sink0", "pdus", "hello")

            # Send a dict (converted to PMT dict)
            post_message("block0", "command", {"freq": 1e6})
        """
        thrift = self._require_thrift()
        return thrift.post_message(block, port, message)

    def get_status(self) -> RuntimeStatusModel:
        """Get runtime status including connection and container info."""
        connection = None
        if self._xmlrpc is not None:
            from urllib.parse import urlparse

            parsed = urlparse(self._xmlrpc._url)
            port = parsed.port or 8080
            connection = self._xmlrpc.get_connection_info(
                container_name=self._active_container, xmlrpc_port=port
            )

        containers = []
        if self._has_docker:
            try:
                containers = self._docker.list_containers()  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("Failed to list containers: %s", e)

        return RuntimeStatusModel(
            connected=self._xmlrpc is not None,
            connection=connection,
            containers=containers,
        )

    # ──────────────────────────────────────────
    # Variable Control
    # ──────────────────────────────────────────

    def list_variables(self) -> list[VariableModel]:
        """List all XML-RPC-exposed variables."""
        xmlrpc = self._require_xmlrpc()
        return xmlrpc.list_variables()

    def get_variable(self, name: str) -> Any:
        """Get a variable value."""
        xmlrpc = self._require_xmlrpc()
        return xmlrpc.get_variable(name)

    def set_variable(self, name: str, value: Any) -> bool:
        """Set a variable value."""
        xmlrpc = self._require_xmlrpc()
        return xmlrpc.set_variable(name, value)

    # ──────────────────────────────────────────
    # Flowgraph Execution Control
    # ──────────────────────────────────────────

    def start(self) -> bool:
        """Start the connected flowgraph."""
        return self._require_xmlrpc().start()

    def stop(self) -> bool:
        """Stop the connected flowgraph."""
        return self._require_xmlrpc().stop()

    def lock(self) -> bool:
        """Lock the flowgraph for thread-safe parameter updates."""
        return self._require_xmlrpc().lock()

    def unlock(self) -> bool:
        """Unlock the flowgraph after parameter updates."""
        return self._require_xmlrpc().unlock()

    # ──────────────────────────────────────────
    # Visual Feedback
    # ──────────────────────────────────────────

    def capture_screenshot(self, name: str | None = None) -> ScreenshotModel:
        """Capture a screenshot of the flowgraph's QT GUI."""
        docker = self._require_docker()
        container_name = name or self._active_container
        if container_name is None:
            raise RuntimeError(
                "No container specified. Provide a name or connect first."
            )
        return docker.capture_screenshot(container_name)

    def get_container_logs(self, name: str | None = None, tail: int = 100) -> str:
        """Get logs from a flowgraph container."""
        docker = self._require_docker()
        container_name = name or self._active_container
        if container_name is None:
            raise RuntimeError(
                "No container specified. Provide a name or connect first."
            )
        return docker.get_logs(container_name, tail=tail)

    # ──────────────────────────────────────────
    # Coverage Collection
    # ──────────────────────────────────────────

    def _get_coverage_dir(self, name: str) -> Path:
        """Get coverage directory for a container, ensure it exists."""
        coverage_dir = Path(HOST_COVERAGE_BASE) / name
        if not coverage_dir.exists():
            raise FileNotFoundError(
                f"No coverage data for container '{name}'. "
                f"Was it launched with enable_coverage=True?"
            )
        return coverage_dir

    def _parse_coverage_summary(self, output: str) -> dict[str, int | float | None]:
        """Parse coverage report output for metrics.

        Example output:
        Name                    Stmts   Miss Branch BrPart  Cover
        ---------------------------------------------------------
        gnuradio/__init__.py       10      2      4      1    75%
        ...
        TOTAL                     100     25     40     10    70%
        """
        result: dict[str, int | float | None] = {
            "lines_covered": None,
            "lines_total": None,
            "coverage_percent": None,
        }
        # Look for TOTAL line
        match = re.search(
            r"^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%",
            output,
            re.MULTILINE,
        )
        if match:
            total_stmts = int(match.group(1))
            miss_stmts = int(match.group(2))
            result["lines_total"] = total_stmts
            result["lines_covered"] = total_stmts - miss_stmts
            result["coverage_percent"] = float(match.group(5))
        return result

    def collect_coverage(self, name: str) -> CoverageDataModel:
        """Collect coverage data from a stopped container.

        Combines any parallel coverage files and returns a summary.
        Container must have been stopped (not removed) for coverage
        data to be available.

        Args:
            name: Container name
        """
        coverage_dir = self._get_coverage_dir(name)

        # First, combine any parallel files (idempotent if already combined)
        # This handles both single-run and multi-run scenarios
        subprocess.run(
            ["coverage", "combine"],
            cwd=coverage_dir,
            capture_output=True,
        )

        coverage_file = coverage_dir / ".coverage"
        if not coverage_file.exists():
            # Check for parallel files that weren't combined
            parallel_files = list(coverage_dir.glob(".coverage.*"))
            if parallel_files:
                raise RuntimeError(
                    f"Coverage combine failed. Found {len(parallel_files)} "
                    f"parallel files but no combined .coverage file."
                )
            raise FileNotFoundError(
                f"No coverage data found in {coverage_dir}. "
                f"Container may not have generated coverage data."
            )

        # Generate summary report
        result = subprocess.run(
            ["coverage", "report", "--data-file", str(coverage_file)],
            capture_output=True,
            text=True,
        )

        summary = result.stdout if result.returncode == 0 else result.stderr
        metrics = self._parse_coverage_summary(summary)

        return CoverageDataModel(
            container_name=name,
            coverage_file=str(coverage_file),
            summary=summary,
            lines_covered=metrics["lines_covered"],
            lines_total=metrics["lines_total"],
            coverage_percent=metrics["coverage_percent"],
        )

    def generate_coverage_report(
        self,
        name: str,
        format: Literal["html", "xml", "json"] = "html",
    ) -> CoverageReportModel:
        """Generate a coverage report in the specified format.

        Args:
            name: Container name
            format: Report format (html, xml, json)
        """
        coverage_dir = self._get_coverage_dir(name)
        coverage_file = coverage_dir / ".coverage"

        if not coverage_file.exists():
            raise FileNotFoundError(
                f"No combined coverage file for '{name}'. "
                f"Call collect_coverage() first."
            )

        if format == "html":
            report_path = coverage_dir / "htmlcov" / "index.html"
            subprocess.run(
                [
                    "coverage",
                    "html",
                    "--data-file",
                    str(coverage_file),
                    "-d",
                    str(coverage_dir / "htmlcov"),
                ],
                capture_output=True,
                check=True,
            )
        elif format == "xml":
            report_path = coverage_dir / "coverage.xml"
            subprocess.run(
                [
                    "coverage",
                    "xml",
                    "--data-file",
                    str(coverage_file),
                    "-o",
                    str(report_path),
                ],
                capture_output=True,
                check=True,
            )
        elif format == "json":
            report_path = coverage_dir / "coverage.json"
            subprocess.run(
                [
                    "coverage",
                    "json",
                    "--data-file",
                    str(coverage_file),
                    "-o",
                    str(report_path),
                ],
                capture_output=True,
                check=True,
            )
        else:
            raise ValueError(f"Unsupported format: {format}")

        return CoverageReportModel(
            container_name=name,
            format=format,
            report_path=str(report_path),
        )

    def combine_coverage(self, names: list[str]) -> CoverageDataModel:
        """Combine coverage data from multiple containers.

        Useful for aggregating coverage across a test suite.

        Args:
            names: List of container names to combine
        """
        if not names:
            raise ValueError("At least one container name required")

        combined_dir = Path(HOST_COVERAGE_BASE) / "combined"
        combined_dir.mkdir(parents=True, exist_ok=True)

        # Clear any existing combined data
        for f in combined_dir.glob(".coverage*"):
            f.unlink()

        # Copy all coverage files to combined directory
        for name in names:
            coverage_dir = self._get_coverage_dir(name)
            for cov_file in coverage_dir.glob(".coverage*"):
                # Ensure unique names when copying
                dest_name = f".coverage.{name}.{cov_file.name}"
                shutil.copy(cov_file, combined_dir / dest_name)

        # Run coverage combine
        subprocess.run(
            ["coverage", "combine"],
            cwd=combined_dir,
            capture_output=True,
            check=True,
        )

        # Generate summary
        coverage_file = combined_dir / ".coverage"
        result = subprocess.run(
            ["coverage", "report", "--data-file", str(coverage_file)],
            capture_output=True,
            text=True,
        )

        summary = result.stdout if result.returncode == 0 else result.stderr
        metrics = self._parse_coverage_summary(summary)

        return CoverageDataModel(
            container_name="combined",
            coverage_file=str(coverage_file),
            summary=summary,
            lines_covered=metrics["lines_covered"],
            lines_total=metrics["lines_total"],
            coverage_percent=metrics["coverage_percent"],
        )

    def delete_coverage(
        self,
        name: str | None = None,
        older_than_days: int | None = None,
    ) -> int:
        """Delete coverage data.

        Args:
            name: Delete specific container's coverage
            older_than_days: Delete all coverage older than N days

        Returns:
            Number of coverage directories deleted
        """
        import time

        deleted = 0
        coverage_base = Path(HOST_COVERAGE_BASE)

        if not coverage_base.exists():
            return 0

        if name is not None:
            # Delete specific container's coverage
            coverage_dir = coverage_base / name
            if coverage_dir.exists():
                shutil.rmtree(coverage_dir)
                deleted += 1
        elif older_than_days is not None:
            # Delete coverage older than N days
            cutoff = time.time() - (older_than_days * 86400)
            for coverage_dir in coverage_base.iterdir():
                if coverage_dir.is_dir():
                    if coverage_dir.stat().st_mtime < cutoff:
                        shutil.rmtree(coverage_dir)
                        deleted += 1
        else:
            # No filter - delete all
            for coverage_dir in coverage_base.iterdir():
                if coverage_dir.is_dir():
                    shutil.rmtree(coverage_dir)
                    deleted += 1

        return deleted

    # ──────────────────────────────────────────
    # OOT Module Detection & Installation
    # ──────────────────────────────────────────

    def detect_oot_modules(self, flowgraph_path: str) -> OOTDetectionResult:
        """Detect which OOT modules a flowgraph requires.

        Analyzes .py or .grc files to find OOT module dependencies.
        Returns recommended Docker image to use with launch_flowgraph().

        For .py files: parses Python imports (most accurate)
        For .grc files: uses heuristic prefix matching against the
            OOT catalog (fast, no Docker required)

        Args:
            flowgraph_path: Path to a .py or .grc flowgraph file

        Returns:
            OOTDetectionResult with detected modules, unknown blocks,
            and recommended image tag.

        Example:
            result = detect_oot_modules("lora_rx.grc")
            # -> detected_modules=["lora_sdr", "osmosdr"]
            # -> recommended_image="gr-combo-lora_sdr-osmosdr:latest"

            # Then launch with auto-built image:
            launch_flowgraph("lora_rx.py", auto_image=True)
        """
        oot = self._require_oot()
        return oot.detect_required_modules(flowgraph_path)

    def install_oot_module(
        self,
        git_url: str,
        branch: str = "main",
        build_deps: list[str] | None = None,
        cmake_args: list[str] | None = None,
        base_image: str | None = None,
        force: bool = False,
    ) -> OOTInstallResult:
        """Install an OOT module into a Docker image.

        Clones the git repo, compiles with cmake, and creates a reusable
        Docker image. Use the returned image_tag with launch_flowgraph().

        Args:
            git_url: Git repository URL (e.g., "https://github.com/tapparelj/gr-lora_sdr")
            branch: Git branch to build from
            build_deps: Extra apt packages needed for compilation
            cmake_args: Extra cmake flags (e.g., ["-DENABLE_TESTING=OFF"])
            base_image: Base image (default: gnuradio-runtime:latest)
            force: Rebuild even if image exists
        """
        oot = self._require_oot()
        return oot.build_module(git_url, branch, build_deps, cmake_args, base_image, force)

    def list_oot_images(self) -> list[OOTImageInfo]:
        """List all installed OOT module images."""
        oot = self._require_oot()
        return oot.list_images()

    def remove_oot_image(self, module_name: str) -> bool:
        """Remove an OOT module image and its registry entry."""
        oot = self._require_oot()
        return oot.remove_image(module_name)

    # ──────────────────────────────────────────
    # Multi-OOT Combo Images
    # ──────────────────────────────────────────

    def build_multi_oot_image(
        self,
        module_names: list[str],
        force: bool = False,
    ) -> ComboImageResult:
        """Combine multiple OOT modules into a single Docker image.

        Modules are merged using multi-stage Docker builds from existing
        single-OOT images. Missing modules that exist in the catalog
        are auto-built first.

        Use the returned image_tag with launch_flowgraph().
        """
        oot = self._require_oot()
        return oot.build_combo_image(module_names, force)

    def list_combo_images(self) -> list[ComboImageInfo]:
        """List all combined multi-OOT images."""
        oot = self._require_oot()
        return oot.list_combo_images()

    def remove_combo_image(self, combo_key: str) -> bool:
        """Remove a combined image by its combo key (e.g., 'combo:adsb+lora_sdr')."""
        oot = self._require_oot()
        return oot.remove_combo_image(combo_key)
