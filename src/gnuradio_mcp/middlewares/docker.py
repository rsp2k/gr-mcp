from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from gnuradio_mcp.models import ContainerModel, ScreenshotModel

logger = logging.getLogger(__name__)

DEFAULT_XMLRPC_PORT = 8080
DEFAULT_VNC_PORT = 5900
DEFAULT_CONTROLPORT_PORT = 9090  # Phase 2: Thrift ControlPort
DEFAULT_STOP_TIMEOUT = 30  # Seconds to wait for graceful shutdown (coverage needs time)
RUNTIME_IMAGE = "gnuradio-runtime:latest"
COVERAGE_IMAGE = "gnuradio-coverage:latest"
CONTAINER_FLOWGRAPH_DIR = "/flowgraphs"
CONTAINER_COVERAGE_DIR = "/coverage"
HOST_COVERAGE_BASE = "/tmp/gr-coverage"


class DockerMiddleware:
    """Wraps the Docker SDK to manage GNU Radio runtime containers.

    Each container runs a flowgraph with Xvfb for headless QT rendering.
    XML-RPC is exposed for variable control; VNC is optional for visual debugging.
    """

    def __init__(self, docker_client: Any):
        self._client = docker_client

    @classmethod
    def create(cls) -> DockerMiddleware | None:
        """Create a DockerMiddleware. Returns None if Docker unavailable."""
        try:
            import docker

            client = docker.from_env()
            client.ping()
            return cls(client)
        except Exception as e:
            logger.warning("Docker unavailable: %s", e)
            return None

    def launch(
        self,
        flowgraph_path: str,
        name: str,
        xmlrpc_port: int = DEFAULT_XMLRPC_PORT,
        enable_vnc: bool = False,
        enable_coverage: bool = False,
        enable_controlport: bool = False,
        controlport_port: int = DEFAULT_CONTROLPORT_PORT,
        enable_perf_counters: bool = True,
        device_paths: list[str] | None = None,
    ) -> ContainerModel:
        """Launch a flowgraph in a Docker container with Xvfb.

        Args:
            flowgraph_path: Path to the .py flowgraph file
            name: Container name
            xmlrpc_port: Port for XML-RPC variable control
            enable_vnc: Enable VNC server for visual debugging
            enable_coverage: Use coverage image and collect Python coverage data
            enable_controlport: Enable ControlPort/Thrift for advanced control
            controlport_port: Port for ControlPort (default 9090)
            enable_perf_counters: Enable performance counters (requires controlport)
            device_paths: Host device paths to pass through (e.g., /dev/ttyUSB0)
        """
        fg_path = Path(flowgraph_path).resolve()
        if not fg_path.exists():
            raise FileNotFoundError(f"Flowgraph not found: {fg_path}")

        # Select image based on coverage mode
        image = COVERAGE_IMAGE if enable_coverage else RUNTIME_IMAGE

        env = {"DISPLAY": ":99", "XMLRPC_PORT": str(xmlrpc_port)}
        if enable_vnc:
            env["ENABLE_VNC"] = "1"
        if enable_coverage:
            env["ENABLE_COVERAGE"] = "1"
        if enable_controlport:
            env["ENABLE_CONTROLPORT"] = "1"
            env["CONTROLPORT_PORT"] = str(controlport_port)
            env["ENABLE_PERF_COUNTERS"] = "True" if enable_perf_counters else "False"

        ports: dict[str, int] = {f"{xmlrpc_port}/tcp": xmlrpc_port}
        vnc_port: int | None = None
        if enable_vnc:
            vnc_port = DEFAULT_VNC_PORT
            ports[f"{vnc_port}/tcp"] = vnc_port
        if enable_controlport:
            ports[f"{controlport_port}/tcp"] = controlport_port

        volumes = {
            str(fg_path.parent): {
                "bind": CONTAINER_FLOWGRAPH_DIR,
                "mode": "ro",
            }
        }

        # Mount coverage directory if coverage enabled
        if enable_coverage:
            coverage_dir = Path(HOST_COVERAGE_BASE) / name
            coverage_dir.mkdir(parents=True, exist_ok=True)
            volumes[str(coverage_dir)] = {
                "bind": CONTAINER_COVERAGE_DIR,
                "mode": "rw",
            }

        devices = [f"{d}:{d}:rwm" for d in (device_paths or [])]

        container_fg_path = f"{CONTAINER_FLOWGRAPH_DIR}/{fg_path.name}"

        container = self._client.containers.run(
            image,
            command=["python3", container_fg_path],
            name=name,
            detach=True,
            environment=env,
            ports=ports,
            volumes=volumes,
            devices=devices or None,
            labels={
                "gr-mcp": "true",
                "gr-mcp.flowgraph": str(fg_path),
                "gr-mcp.xmlrpc-port": str(xmlrpc_port),
                "gr-mcp.vnc-enabled": "1" if enable_vnc else "0",
                "gr-mcp.coverage-enabled": "1" if enable_coverage else "0",
                "gr-mcp.controlport-enabled": "1" if enable_controlport else "0",
                "gr-mcp.controlport-port": str(controlport_port),
            },
        )

        return ContainerModel(
            name=name,
            container_id=container.id[:12],
            status="running",
            flowgraph_path=str(fg_path),
            xmlrpc_port=xmlrpc_port,
            vnc_port=vnc_port,
            controlport_port=controlport_port if enable_controlport else None,
            device_paths=device_paths or [],
            coverage_enabled=enable_coverage,
            controlport_enabled=enable_controlport,
        )

    def list_containers(self) -> list[ContainerModel]:
        """List all gr-mcp managed containers."""
        containers = self._client.containers.list(
            all=True, filters={"label": "gr-mcp=true"}
        )
        result = []
        for c in containers:
            labels = c.labels
            controlport_enabled = labels.get("gr-mcp.controlport-enabled") == "1"
            result.append(
                ContainerModel(
                    name=c.name,
                    container_id=c.id[:12],
                    status=c.status,
                    flowgraph_path=labels.get("gr-mcp.flowgraph", ""),
                    xmlrpc_port=int(
                        labels.get("gr-mcp.xmlrpc-port", DEFAULT_XMLRPC_PORT)
                    ),
                    vnc_port=(
                        DEFAULT_VNC_PORT
                        if labels.get("gr-mcp.vnc-enabled") == "1"
                        and c.status == "running"
                        else None
                    ),
                    controlport_port=(
                        int(
                            labels.get(
                                "gr-mcp.controlport-port", DEFAULT_CONTROLPORT_PORT
                            )
                        )
                        if controlport_enabled and c.status == "running"
                        else None
                    ),
                    coverage_enabled=labels.get("gr-mcp.coverage-enabled") == "1",
                    controlport_enabled=controlport_enabled,
                )
            )
        return result

    def stop(self, name: str, timeout: int = DEFAULT_STOP_TIMEOUT) -> bool:
        """Stop a container gracefully with SIGTERM.

        Uses a longer timeout (30s) to allow coverage data to be flushed.
        Falls back to SIGKILL if container doesn't respond, but warns that
        coverage data may be lost.

        Args:
            name: Container name
            timeout: Seconds to wait for graceful shutdown before SIGKILL
        """
        container = self._client.containers.get(name)
        try:
            container.stop(timeout=timeout)
        except Exception as e:
            # Timeout reached, container will be killed - coverage may be lost
            logger.warning(
                "Container %s didn't stop gracefully within %ds, "
                "coverage data may be lost: %s",
                name,
                timeout,
                e,
            )
        return True

    def remove(self, name: str, force: bool = False) -> bool:
        """Remove a container by name."""
        container = self._client.containers.get(name)
        container.remove(force=force)
        return True

    def get_logs(self, name: str, tail: int = 100) -> str:
        """Get container logs."""
        container = self._client.containers.get(name)
        return container.logs(tail=tail).decode("utf-8", errors="replace")

    def capture_screenshot(self, name: str) -> ScreenshotModel:
        """Capture the Xvfb framebuffer via ImageMagick import."""
        container = self._client.containers.get(name)
        exit_code, output = container.exec_run(
            ["import", "-display", ":99", "-window", "root", "png:-"],
        )
        if exit_code != 0:
            raise RuntimeError(
                f"Screenshot failed (exit {exit_code}): "
                f"{output.decode('utf-8', errors='replace')[:200]}"
            )

        image_b64 = base64.b64encode(output).decode("ascii")
        return ScreenshotModel(
            container_name=name,
            image_base64=image_b64,
            format="png",
        )

    def get_xmlrpc_port(self, name: str) -> int:
        """Get the XML-RPC port for a container."""
        container = self._client.containers.get(name)
        return int(container.labels.get("gr-mcp.xmlrpc-port", DEFAULT_XMLRPC_PORT))

    def is_coverage_enabled(self, name: str) -> bool:
        """Check if coverage is enabled for a container."""
        container = self._client.containers.get(name)
        return container.labels.get("gr-mcp.coverage-enabled") == "1"

    def get_coverage_dir(self, name: str) -> Path:
        """Get the host-side coverage directory for a container."""
        return Path(HOST_COVERAGE_BASE) / name

    def is_controlport_enabled(self, name: str) -> bool:
        """Check if ControlPort is enabled for a container."""
        container = self._client.containers.get(name)
        return container.labels.get("gr-mcp.controlport-enabled") == "1"

    def get_controlport_port(self, name: str) -> int:
        """Get the ControlPort Thrift port for a container."""
        container = self._client.containers.get(name)
        return int(
            container.labels.get("gr-mcp.controlport-port", DEFAULT_CONTROLPORT_PORT)
        )
