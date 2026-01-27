from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from gnuradio_mcp.middlewares.docker import DockerMiddleware, HOST_COVERAGE_BASE
from gnuradio_mcp.middlewares.xmlrpc import XmlRpcMiddleware
from gnuradio_mcp.models import (
    ConnectionInfoModel,
    ContainerModel,
    CoverageDataModel,
    CoverageReportModel,
    RuntimeStatusModel,
    ScreenshotModel,
    VariableModel,
)

logger = logging.getLogger(__name__)


class RuntimeProvider:
    """Business logic for runtime flowgraph control.

    Coordinates Docker (container lifecycle) and XML-RPC (variable control).
    Tracks the active connection so convenience methods like get_variable()
    work without repeating the URL each call.
    """

    def __init__(
        self,
        docker_mw: DockerMiddleware | None = None,
    ):
        self._docker = docker_mw
        self._xmlrpc: XmlRpcMiddleware | None = None
        self._active_container: str | None = None

    @property
    def _has_docker(self) -> bool:
        return self._docker is not None

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

    # ──────────────────────────────────────────
    # Container Lifecycle
    # ──────────────────────────────────────────

    def launch_flowgraph(
        self,
        flowgraph_path: str,
        name: str | None = None,
        xmlrpc_port: int = 8080,
        enable_vnc: bool = False,
        enable_coverage: bool = False,
        device_paths: list[str] | None = None,
    ) -> ContainerModel:
        """Launch a flowgraph in a Docker container with Xvfb.

        Args:
            flowgraph_path: Path to the .py flowgraph file
            name: Container name (defaults to 'gr-{stem}')
            xmlrpc_port: Port for XML-RPC variable control
            enable_vnc: Enable VNC server for visual debugging
            enable_coverage: Enable Python code coverage collection
            device_paths: Host device paths to pass through
        """
        docker = self._require_docker()
        if name is None:
            name = f"gr-{Path(flowgraph_path).stem}"
        return docker.launch(
            flowgraph_path=flowgraph_path,
            name=name,
            xmlrpc_port=xmlrpc_port,
            enable_vnc=enable_vnc,
            enable_coverage=enable_coverage,
            device_paths=device_paths,
        )

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
        return self._xmlrpc.get_connection_info(
            container_name=name, xmlrpc_port=port
        )

    def disconnect(self) -> bool:
        """Disconnect from the current XML-RPC endpoint."""
        if self._xmlrpc is not None:
            self._xmlrpc.close()
            self._xmlrpc = None
            self._active_container = None
        return True

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
                "No container specified. Provide a name or connect to a container first."
            )
        return docker.capture_screenshot(container_name)

    def get_container_logs(self, name: str | None = None, tail: int = 100) -> str:
        """Get logs from a flowgraph container."""
        docker = self._require_docker()
        container_name = name or self._active_container
        if container_name is None:
            raise RuntimeError(
                "No container specified. Provide a name or connect to a container first."
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
                    "coverage", "html",
                    "--data-file", str(coverage_file),
                    "-d", str(coverage_dir / "htmlcov"),
                ],
                capture_output=True,
                check=True,
            )
        elif format == "xml":
            report_path = coverage_dir / "coverage.xml"
            subprocess.run(
                [
                    "coverage", "xml",
                    "--data-file", str(coverage_file),
                    "-o", str(report_path),
                ],
                capture_output=True,
                check=True,
            )
        elif format == "json":
            report_path = coverage_dir / "coverage.json"
            subprocess.run(
                [
                    "coverage", "json",
                    "--data-file", str(coverage_file),
                    "-o", str(report_path),
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
