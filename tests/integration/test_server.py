import pytest
from fastmcp import Client

from main import app as mcp_app


@pytest.fixture
async def main_mcp_client():
    async with Client(mcp_app) as mcp_client:
        yield mcp_client


async def test_get_blocks(main_mcp_client: Client):
    """Test retrieving available blocks."""
    blocks = await main_mcp_client.call_tool(name="get_blocks")
    assert blocks.data is not None
    # We don't know exactly what blocks are there, but should get a list
    assert isinstance(blocks.data, list)


async def test_make_and_remove_block(main_mcp_client: Client):
    """Test making a block and then removing it."""
    block_type = "analog_sig_source_x"

    # helper to check if block exists in the flowgraph
    async def get_block_names():
        current_blocks = await main_mcp_client.call_tool(name="get_blocks")
        # FastMCP 3.0 returns Pydantic models in .data, use attribute access
        return [b.name for b in current_blocks.data]

    # 1. Create a block
    result = await main_mcp_client.call_tool(
        name="make_block", arguments={"block_name": block_type}
    )
    assert result.data is not None
    # The output is the new block name, likely something like "analog_sig_source_x_0"
    new_block_name = str(result.data)
    assert block_type in new_block_name

    # Verify it exists
    block_names = await get_block_names()
    assert new_block_name in block_names

    # 2. Remove the block
    remove_result = await main_mcp_client.call_tool(
        name="remove_block", arguments={"block_name": new_block_name}
    )
    assert remove_result.data is True

    # Verify it's gone
    block_names = await get_block_names()
    assert new_block_name not in block_names
