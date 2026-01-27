from __future__ import annotations

import logging
from typing import Any

from gnuradio_mcp.middlewares.docker import DockerMiddleware
from gnuradio_mcp.middlewares.xmlrpc import XmlRpcMiddleware
from gnuradio_mcp.models import (
    ConnectionInfoModel,
    ContainerModel,
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
        device_paths: list[str] | None = None,
    ) -> ContainerModel:
        """Launch a flowgraph in a Docker container with Xvfb."""
        docker = self._require_docker()
        if name is None:
            from pathlib import Path

            name = f"gr-{Path(flowgraph_path).stem}"
        return docker.launch(
            flowgraph_path=flowgraph_path,
            name=name,
            xmlrpc_port=xmlrpc_port,
            enable_vnc=enable_vnc,
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
