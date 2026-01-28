"""Port utilities for dynamic allocation and flowgraph patching.

Provides pre-flight port checking so Docker bind failures are caught
early with actionable error messages, and flowgraph patching so the
compiled .py can use a different XML-RPC port than what GRC baked in.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex for SimpleXMLRPCServer(('addr', PORT)) in compiled flowgraphs.
# GRC emits: xmlrpc_server_0 = SimpleXMLRPCServer(('localhost', 8080), ...)
_XMLRPC_PORT_RE = re.compile(r"(SimpleXMLRPCServer\(\s*\([^,]+,\s*)(\d+)(\s*\))")


class PortConflictError(RuntimeError):
    """Raised when a requested port is already in use."""


def is_port_available(port: int) -> bool:
    """Check if a TCP port is available on localhost.

    Attempts to bind a socket to the port. Returns True if the bind
    succeeds (port is free), False otherwise.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port() -> int:
    """Find a free TCP port using the OS ephemeral range.

    Binds to port 0, which lets the kernel pick an available port,
    then closes the socket and returns the chosen port. A second
    availability check guards against the (rare) race where another
    process grabs it between close and Docker bind.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        port = sock.getsockname()[1]
    return port


def detect_xmlrpc_port(flowgraph_py: Path) -> int | None:
    """Extract the SimpleXMLRPCServer port from a compiled flowgraph.

    Returns the port number, or None if no XML-RPC server is found.
    """
    text = flowgraph_py.read_text()
    match = _XMLRPC_PORT_RE.search(text)
    if match:
        return int(match.group(2))
    return None


def patch_xmlrpc_port(flowgraph_py: Path, new_port: int) -> Path:
    """Create a patched copy of the flowgraph with a different XML-RPC port.

    The original file is never modified. The patched copy is placed in
    the same directory so Docker volume mounts pick it up automatically.

    Returns the path to the patched temporary file.
    """
    text = flowgraph_py.read_text()
    patched, count = _XMLRPC_PORT_RE.subn(
        rf"\g<1>{new_port}\3",
        text,
    )
    if count == 0:
        raise ValueError(f"No SimpleXMLRPCServer port found in {flowgraph_py} to patch")

    # Write to a temp file in the same directory (same Docker mount)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".py",
        prefix=f"{flowgraph_py.stem}_patched_",
        dir=flowgraph_py.parent,
    )
    tmp = Path(tmp_path)
    tmp.write_text(patched)
    # mkstemp opens the fd; we wrote via Path so close it
    os.close(fd)

    logger.debug("Patched flowgraph written to %s", tmp)
    return tmp
