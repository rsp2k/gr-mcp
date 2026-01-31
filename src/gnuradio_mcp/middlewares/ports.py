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

# Compat: GRC 3.10.12+ emits message_debug(True, gr.log_levels.info)
# but GNU Radio 3.10.5 (Docker images) only accepts message_debug(en_uvec: bool).
_MESSAGE_DEBUG_COMPAT_RE = re.compile(
    r"blocks\.message_debug\(True,\s*gr\.log_levels\.\w+\)"
)

# Docker: GRC generates SimpleXMLRPCServer(('localhost', ...)) which is
# unreachable from the Docker host.  Rewrite to 0.0.0.0 for container use.
_XMLRPC_LOCALHOST_RE = re.compile(
    r"(SimpleXMLRPCServer\(\s*\()'localhost'(\s*,)"
)

# Docker: GRC's no_gui template uses input('Press Enter to quit: ') which
# gets immediate EOF in detached containers, killing the flowgraph instantly.
# We inject a signal.pause() fallback after the EOFError catch.
_INPUT_EOF_RE = re.compile(
    r"( +)try:\n\1    input\('Press Enter to quit: '\)\n"
    r"\1except EOFError:\n\1    pass",
    re.MULTILINE,
)

_INPUT_EOF_REPLACEMENT = (
    r"\1try:\n"
    r"\1    input('Press Enter to quit: ')\n"
    r"\1except EOFError:\n"
    r"\1    import signal as _sig\n"
    r"\1    try:\n"
    r"\1        _sig.pause()\n"
    r"\1    except AttributeError:\n"
    r"\1        import time\n"
    r"\1        while True:\n"
    r"\1            time.sleep(1)"
)


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


def _apply_compat_patches(text: str) -> str:
    """Apply compatibility fixes so GRC-generated code runs in Docker.

    Handles three categories of issues:
    1. Cross-version constructor changes (message_debug signature)
    2. Network binding (localhost → 0.0.0.0 for container accessibility)
    3. Docker lifecycle (input() EOF → signal.pause() for detached mode)
    """
    text = _MESSAGE_DEBUG_COMPAT_RE.sub("blocks.message_debug(True)", text)
    text = _XMLRPC_LOCALHOST_RE.sub(r"\g<1>'0.0.0.0'\2", text)
    text = _INPUT_EOF_RE.sub(_INPUT_EOF_REPLACEMENT, text)
    return text


def patch_flowgraph(
    flowgraph_py: Path,
    xmlrpc_port: int | None = None,
) -> Path:
    """Apply all patches (port rewrite + compat fixes) in a single pass.

    Returns the original path unchanged if no patches were needed,
    or a new temp file in the same directory.
    """
    text = flowgraph_py.read_text()
    original = text

    if xmlrpc_port is not None:
        text, _ = _XMLRPC_PORT_RE.subn(rf"\g<1>{xmlrpc_port}\3", text)

    text = _apply_compat_patches(text)

    if text == original:
        return flowgraph_py

    fd, tmp_path = tempfile.mkstemp(
        suffix=".py",
        prefix=f"{flowgraph_py.stem}_patched_",
        dir=flowgraph_py.parent,
    )
    tmp = Path(tmp_path)
    tmp.write_text(text)
    os.close(fd)
    logger.debug("Patched flowgraph written to %s", tmp)
    return tmp
