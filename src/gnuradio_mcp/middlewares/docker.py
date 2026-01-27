from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from gnuradio_mcp.models import ContainerModel, ScreenshotModel

logger = logging.getLogger(__name__)

DEFAULT_XMLRPC_PORT = 8080
DEFAULT_VNC_PORT = 5900
RUNTIME_IMAGE = "gnuradio-runtime:latest"
CONTAINER_FLOWGRAPH_DIR = "/flowgraphs"


class DockerMiddleware:
    """Wraps the Docker SDK to manage GNU Radio runtime containers.

    Each container runs a flowgraph with Xvfb for headless QT rendering.
    XML-RPC is exposed for variable control; VNC is optional for visual debugging.
    """

    def __init__(self, docker_client: Any):
        self._client = docker_client

    @classmethod
    def create(cls) -> DockerMiddleware | None:
        """Attempt to create a DockerMiddleware. Returns None if Docker is unavailable."""
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
        device_paths: list[str] | None = None,
    ) -> ContainerModel:
        """Launch a flowgraph in a Docker container with Xvfb."""
        fg_path = Path(flowgraph_path).resolve()
        if not fg_path.exists():
            raise FileNotFoundError(f"Flowgraph not found: {fg_path}")

        env = {"DISPLAY": ":99", "XMLRPC_PORT": str(xmlrpc_port)}
        if enable_vnc:
            env["ENABLE_VNC"] = "1"

        ports: dict[str, int] = {f"{xmlrpc_port}/tcp": xmlrpc_port}
        vnc_port: int | None = None
        if enable_vnc:
            vnc_port = DEFAULT_VNC_PORT
            ports[f"{vnc_port}/tcp"] = vnc_port

        volumes = {
            str(fg_path.parent): {
                "bind": CONTAINER_FLOWGRAPH_DIR,
                "mode": "ro",
            }
        }

        devices = [f"{d}:{d}:rwm" for d in (device_paths or [])]

        container_fg_path = f"{CONTAINER_FLOWGRAPH_DIR}/{fg_path.name}"

        container = self._client.containers.run(
            RUNTIME_IMAGE,
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
            },
        )

        return ContainerModel(
            name=name,
            container_id=container.id[:12],
            status="running",
            flowgraph_path=str(fg_path),
            xmlrpc_port=xmlrpc_port,
            vnc_port=vnc_port,
            device_paths=device_paths or [],
        )

    def list_containers(self) -> list[ContainerModel]:
        """List all gr-mcp managed containers."""
        containers = self._client.containers.list(
            all=True, filters={"label": "gr-mcp=true"}
        )
        result = []
        for c in containers:
            labels = c.labels
            result.append(
                ContainerModel(
                    name=c.name,
                    container_id=c.id[:12],
                    status=c.status,
                    flowgraph_path=labels.get("gr-mcp.flowgraph", ""),
                    xmlrpc_port=int(labels.get("gr-mcp.xmlrpc-port", DEFAULT_XMLRPC_PORT)),
                    vnc_port=DEFAULT_VNC_PORT
                    if labels.get("gr-mcp.vnc-enabled") == "1" and c.status == "running"
                    else None,
                )
            )
        return result

    def stop(self, name: str) -> bool:
        """Stop a container by name."""
        container = self._client.containers.get(name)
        container.stop(timeout=10)
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
        return int(
            container.labels.get("gr-mcp.xmlrpc-port", DEFAULT_XMLRPC_PORT)
        )
