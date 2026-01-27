from __future__ import annotations

import logging
import xmlrpc.client
from typing import Any

from gnuradio_mcp.models import ConnectionInfoModel, VariableModel

logger = logging.getLogger(__name__)

XMLRPC_TIMEOUT = 5


class XmlRpcMiddleware:
    """Wraps xmlrpc.client.ServerProxy for GNU Radio XML-RPC control.

    GNU Radio flowgraphs expose an XML-RPC server when they contain an
    xmlrpc_server block. Methods follow the pattern:
    - get_<variable>() to read
    - set_<variable>(value) to write
    - start() / stop() / lock() / unlock() for execution control
    """

    def __init__(self, proxy: xmlrpc.client.ServerProxy, url: str):
        self._proxy = proxy
        self._url = url

    @classmethod
    def connect(cls, url: str) -> XmlRpcMiddleware:
        """Create a connection to a GNU Radio XML-RPC server."""
        transport = xmlrpc.client.Transport()
        transport.timeout = XMLRPC_TIMEOUT
        proxy = xmlrpc.client.ServerProxy(url, transport=transport)
        # Verify connectivity
        proxy.system.listMethods()
        logger.info("Connected to XML-RPC at %s", url)
        return cls(proxy, url)

    def get_connection_info(
        self, container_name: str | None = None, xmlrpc_port: int = 8080
    ) -> ConnectionInfoModel:
        """Return connection metadata including available methods."""
        methods = self._list_methods()
        return ConnectionInfoModel(
            url=self._url,
            container_name=container_name,
            xmlrpc_port=xmlrpc_port,
            methods=methods,
        )

    def _list_methods(self) -> list[str]:
        """List XML-RPC methods, filtering out system internals."""
        try:
            all_methods = self._proxy.system.listMethods()
            return [m for m in all_methods if not m.startswith("system.")]
        except Exception:
            return []

    def list_variables(self) -> list[VariableModel]:
        """Discover variables by introspecting get_* methods."""
        methods = self._list_methods()
        variables = []
        for method in methods:
            if method.startswith("get_"):
                var_name = method[4:]
                # Only include if there's a matching setter
                if f"set_{var_name}" in methods:
                    try:
                        value = getattr(self._proxy, method)()
                        variables.append(VariableModel(name=var_name, value=value))
                    except Exception as e:
                        logger.warning("Failed to read %s: %s", var_name, e)
                        variables.append(VariableModel(name=var_name, value=None))
        return variables

    def get_variable(self, name: str) -> Any:
        """Get a variable value via XML-RPC."""
        getter = getattr(self._proxy, f"get_{name}")
        return getter()

    def set_variable(self, name: str, value: Any) -> bool:
        """Set a variable value via XML-RPC."""
        setter = getattr(self._proxy, f"set_{name}")
        setter(value)
        return True

    def start(self) -> bool:
        """Start the flowgraph."""
        self._proxy.start()
        return True

    def stop(self) -> bool:
        """Stop the flowgraph."""
        self._proxy.stop()
        return True

    def lock(self) -> bool:
        """Lock the flowgraph for thread-safe parameter updates."""
        self._proxy.lock()
        return True

    def unlock(self) -> bool:
        """Unlock the flowgraph after parameter updates."""
        self._proxy.unlock()
        return True

    def close(self) -> None:
        """Close the XML-RPC connection (clears reference, GC handles socket)."""
        self._proxy = None
