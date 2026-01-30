"""Tests for the capability gap fill features (Gaps 1-8).

These tests validate the new middleware and provider methods added to
close the gap between gr-mcp and grcc/GRC.
"""
from __future__ import annotations

import pytest

from gnuradio_mcp.middlewares.flowgraph import FlowGraphMiddleware
from gnuradio_mcp.middlewares.platform import PlatformMiddleware
from gnuradio_mcp.models import (
    BlockModel,
    BlockTypeDetailModel,
    FlowgraphOptionsModel,
    GeneratedCodeModel,
)


@pytest.fixture
def flowgraph_middleware(platform_middleware: PlatformMiddleware):
    return platform_middleware.make_flowgraph()


# ──────────────────────────────────────────────
# Gap 3: Flowgraph Options
# ──────────────────────────────────────────────


def test_get_flowgraph_options(flowgraph_middleware: FlowGraphMiddleware):
    opts = flowgraph_middleware.get_flowgraph_options()
    assert isinstance(opts, FlowgraphOptionsModel)
    assert opts.id  # Default flowgraph has an id
    assert opts.generate_options  # Should have a generate_options set


def test_set_flowgraph_options(flowgraph_middleware: FlowGraphMiddleware):
    flowgraph_middleware.set_flowgraph_options({
        "title": "Test Flowgraph",
        "author": "gr-mcp tests",
    })
    opts = flowgraph_middleware.get_flowgraph_options()
    assert opts.title == "Test Flowgraph"
    assert opts.author == "gr-mcp tests"


def test_set_invalid_option_raises(flowgraph_middleware: FlowGraphMiddleware):
    with pytest.raises(KeyError, match="nonexistent_key"):
        flowgraph_middleware.set_flowgraph_options({"nonexistent_key": "value"})


# ──────────────────────────────────────────────
# Gap 4: Embedded Python Blocks
# ──────────────────────────────────────────────

EPY_SOURCE = '''\
import numpy as np
from gnuradio import gr

class blk(gr.sync_block):
    """Test embedded block - multiply by constant"""
    def __init__(self, gain=1.0):
        gr.sync_block.__init__(
            self, name='Test Gain Block',
            in_sig=[np.float32], out_sig=[np.float32]
        )
        self.gain = gain

    def work(self, input_items, output_items):
        output_items[0][:] = input_items[0] * self.gain
        return len(output_items[0])
'''


def test_create_embedded_python_block(flowgraph_middleware: FlowGraphMiddleware):
    model = flowgraph_middleware.create_embedded_python_block(EPY_SOURCE, "my_gain")
    assert isinstance(model, BlockModel)
    assert model.name == "my_gain"

    # Verify the block is in the flowgraph
    assert any(b.name == "my_gain" for b in flowgraph_middleware.blocks)


def test_embedded_block_auto_names(flowgraph_middleware: FlowGraphMiddleware):
    model = flowgraph_middleware.create_embedded_python_block(EPY_SOURCE)
    assert "epy_block" in model.name


# ──────────────────────────────────────────────
# Gap 5: Search Blocks
# ──────────────────────────────────────────────


def test_search_blocks_by_query(platform_middleware: PlatformMiddleware):
    results = platform_middleware.search_blocks(query="throttle")
    assert len(results) > 0
    assert all(isinstance(r, BlockTypeDetailModel) for r in results)
    assert any("throttle" in r.key.lower() for r in results)


def test_search_blocks_by_category(platform_middleware: PlatformMiddleware):
    # GRC categories use names like "Core", "Waveform Generators", etc.
    results = platform_middleware.search_blocks(category="Waveform Generators")
    assert len(results) > 0
    for r in results:
        assert any("waveform generators" in c.lower() for c in r.category)


def test_search_blocks_empty_query(platform_middleware: PlatformMiddleware):
    # Empty query should return all blocks
    all_results = platform_middleware.search_blocks()
    all_blocks = platform_middleware.blocks
    assert len(all_results) == len(all_blocks)


def test_search_blocks_no_match(platform_middleware: PlatformMiddleware):
    results = platform_middleware.search_blocks(query="zzz_nonexistent_block_xyz")
    assert results == []


def test_get_block_categories(platform_middleware: PlatformMiddleware):
    cats = platform_middleware.get_block_categories()
    assert isinstance(cats, dict)
    assert len(cats) > 0
    # Each value should be a list of block keys
    for _cat, keys in cats.items():
        assert isinstance(keys, list)
        assert all(isinstance(k, str) for k in keys)


# ──────────────────────────────────────────────
# Gap 6: Expression Evaluation
# ──────────────────────────────────────────────


def test_evaluate_simple_expression(flowgraph_middleware: FlowGraphMiddleware):
    result = flowgraph_middleware.evaluate_expression("2 + 2")
    assert result == 4


def test_evaluate_variable_expression(flowgraph_middleware: FlowGraphMiddleware):
    # Default flowgraph has samp_rate variable
    result = flowgraph_middleware.evaluate_expression("samp_rate")
    assert result == 32000  # Default value


# ──────────────────────────────────────────────
# Gap 7: Block Bypass
# ──────────────────────────────────────────────


def test_bypass_single_io_block(flowgraph_middleware: FlowGraphMiddleware):
    # blocks_multiply_const_vxx has 1 input, 1 output of same type — bypassable
    model = flowgraph_middleware.add_block("blocks_multiply_const_vxx")
    result = flowgraph_middleware.bypass_block(model.name)
    assert result is True

    # Verify state
    block_mw = flowgraph_middleware.get_block(model.name)
    assert block_mw._block.state == "bypassed"


def test_unbypass_block(flowgraph_middleware: FlowGraphMiddleware):
    model = flowgraph_middleware.add_block("blocks_multiply_const_vxx")
    flowgraph_middleware.bypass_block(model.name)
    result = flowgraph_middleware.unbypass_block(model.name)
    assert result is True

    block_mw = flowgraph_middleware.get_block(model.name)
    assert block_mw._block.state == "enabled"


def test_bypass_multi_io_block_raises(flowgraph_middleware: FlowGraphMiddleware):
    # blocks_copy has a hidden message port — 2 sinks — cannot be bypassed
    model = flowgraph_middleware.add_block("blocks_copy")
    with pytest.raises(ValueError, match="cannot be bypassed"):
        flowgraph_middleware.bypass_block(model.name)


# ──────────────────────────────────────────────
# Gap 8: Export/Import Flowgraph Data
# ──────────────────────────────────────────────


def test_export_data(flowgraph_middleware: FlowGraphMiddleware):
    data = flowgraph_middleware.export_data()
    assert isinstance(data, dict)
    assert "blocks" in data or "options" in data


def test_roundtrip_export_import(
    flowgraph_middleware: FlowGraphMiddleware,
    platform_middleware: PlatformMiddleware,
):
    # Add a block, export, create fresh flowgraph, import
    flowgraph_middleware.add_block("analog_sig_source_x", "my_sig_source")
    exported = flowgraph_middleware.export_data()

    new_fg = platform_middleware.make_flowgraph()
    new_fg.import_data(exported)

    assert any(b.name == "my_sig_source" for b in new_fg.blocks)


# ──────────────────────────────────────────────
# Gap 1: Code Generation
# ──────────────────────────────────────────────


def test_generate_code_produces_output(flowgraph_middleware: FlowGraphMiddleware):
    result = flowgraph_middleware.generate_code()
    assert isinstance(result, GeneratedCodeModel)
    assert len(result.files) > 0
    assert result.flowgraph_id
    assert result.generate_options

    # Should have at least one main file
    main_files = [f for f in result.files if f.is_main]
    assert len(main_files) >= 1


def test_generate_code_contains_python(flowgraph_middleware: FlowGraphMiddleware):
    result = flowgraph_middleware.generate_code()
    main = next((f for f in result.files if f.is_main), None)
    assert main is not None
    # Generated Python code should contain typical markers
    assert "import" in main.content or "#include" in main.content


def test_generate_code_with_output_dir(flowgraph_middleware: FlowGraphMiddleware):
    """Files persist on disk when output_dir is specified."""
    import os
    import tempfile

    output_dir = tempfile.mkdtemp(prefix="gr_mcp_test_")
    result = flowgraph_middleware.generate_code(output_dir=output_dir)

    assert result.output_dir == output_dir
    # Files should exist on disk
    main = next((f for f in result.files if f.is_main), None)
    assert main is not None
    assert os.path.exists(os.path.join(output_dir, main.filename))


def test_generate_code_returns_validation_state(
    flowgraph_middleware: FlowGraphMiddleware,
):
    """generate_code includes is_valid and warnings in response."""
    result = flowgraph_middleware.generate_code()
    assert isinstance(result.is_valid, bool)
    assert isinstance(result.warnings, list)


def test_generate_code_default_output_persists(
    flowgraph_middleware: FlowGraphMiddleware,
):
    """Default temp dir persists files (not cleaned up after call)."""
    import os

    result = flowgraph_middleware.generate_code()
    assert result.output_dir  # Should have a temp path
    assert os.path.isdir(result.output_dir)  # Dir still exists
    main = next((f for f in result.files if f.is_main), None)
    if main:
        assert os.path.exists(os.path.join(result.output_dir, main.filename))
