from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from gnuradio_mcp.middlewares.platform import PlatformMiddleware
from gnuradio_mcp.providers.mcp import McpPlatformProvider
from gnuradio_mcp.providers.mcp_runtime import McpRuntimeProvider

logger = logging.getLogger(__name__)

try:
    from gnuradio import gr
    from gnuradio.grc.core.platform import Platform
except ImportError:
    raise Exception("Cannot find GNU Radio!") from None

platform = Platform(
    version=gr.version(),
    version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
    prefs=gr.prefs(),
)
platform.build_library()

app: FastMCP = FastMCP("GNU Radio MCP", instructions="Create GNU Radio flowgraphs")

pmw = PlatformMiddleware(platform)

# Auto-discover OOT modules from common install locations
oot_candidates = [
    "/usr/local/share/gnuradio/grc/blocks",
    os.path.expanduser("~/.local/share/gnuradio/grc/blocks"),
]
for path in oot_candidates:
    if os.path.isdir(path):
        try:
            result = pmw.add_block_path(path)
            if result.blocks_added > 0:
                logger.info(f"OOT: +{result.blocks_added} blocks from {path}")
        except Exception:
            pass

McpPlatformProvider.from_platform_middleware(app, pmw)
McpRuntimeProvider.create(app)

if __name__ == "__main__":
    app.run()
