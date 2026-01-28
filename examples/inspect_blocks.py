#!/usr/bin/env python3
"""Inspect block parameters for FM receiver blocks."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastmcp import Client
from main import app as mcp_app


async def inspect_block(client: Client, block_key: str):
    """Create a block and show its parameters."""
    print(f"\n{'='*60}")
    print(f"Block: {block_key}")
    print(f"{'='*60}")

    # Create the block
    result = await client.call_tool(
        name="make_block", arguments={"block_name": block_key}
    )
    block_name = str(result.data)
    print(f"Created: {block_name}")

    # Get parameters
    params = await client.call_tool(
        name="get_block_params", arguments={"block_name": block_name}
    )

    print("\nParameters (key -> display name):")
    for param in params.data:
        print(f"  {param.key}: {param.value!r}")
        print(f"    name: {param.name}")
        print(f"    type: {param.dtype}")

    # Get sources (outputs)
    sources = await client.call_tool(
        name="get_block_sources", arguments={"block_name": block_name}
    )
    print("\nSources (outputs):")
    for port in sources.data:
        print(f"  [{port.key}] {port.name} ({port.dtype})")

    # Get sinks (inputs)
    sinks = await client.call_tool(
        name="get_block_sinks", arguments={"block_name": block_name}
    )
    print("\nSinks (inputs):")
    for port in sinks.data:
        print(f"  [{port.key}] {port.name} ({port.dtype})")

    return block_name


async def main():
    blocks_to_inspect = [
        "osmosdr_source",
        "low_pass_filter",
        "analog_wfm_rcv",
        "audio_sink",
        "analog_sig_source_x",
    ]

    async with Client(mcp_app) as client:
        for block_key in blocks_to_inspect:
            try:
                await inspect_block(client, block_key)
            except Exception as e:
                print(f"\nError inspecting {block_key}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
