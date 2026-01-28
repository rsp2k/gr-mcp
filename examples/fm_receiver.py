#!/usr/bin/env python3
"""
FM Receiver Flowgraph Builder using gr-mcp

This script uses gr-mcp's MCP tools to programmatically build a Wideband FM
receiver flowgraph that:
- Receives RF from an RTL-SDR dongle (or simulated source)
- Demodulates FM audio
- Outputs to speakers

Signal Chain:
    RTL-SDR Source (2.4 MHz) → Low Pass Filter → WBFM Demod → Audio Sink
         ↓                          ↓                ↓              ↓
      88-108 MHz              Anti-alias         Demodulate      Speakers
      complex IQ              200 kHz BW          to audio
"""

import asyncio
import sys
from pathlib import Path

from fastmcp import Client

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app as mcp_app


async def find_blocks_matching(client: Client, patterns: list[str]) -> dict[str, str]:
    """Search available blocks for ones matching the given patterns."""
    result = await client.call_tool(name="get_all_available_blocks")
    available = result.data

    matches = {}
    for pattern in patterns:
        for block in available:
            if pattern.lower() in block.key.lower():
                if pattern not in matches:
                    matches[pattern] = block.key
                    break
    return matches


async def build_fm_receiver(
    client: Client,
    freq_mhz: float = 99.5,
    output_path: str = "/tmp/fm_receiver.grc",
    use_simulation: bool = False,
):
    """
    Build an FM receiver flowgraph.

    Args:
        client: FastMCP client connected to gr-mcp
        freq_mhz: FM station frequency in MHz (default 99.5)
        output_path: Where to save the .grc file
        use_simulation: If True, use signal source instead of RTL-SDR
    """
    print(f"\n{'='*60}")
    print(f"Building FM Receiver for {freq_mhz} MHz")
    print(f"{'='*60}\n")

    # Step 1: Find available blocks
    print("Step 1: Checking available blocks...")
    result = await client.call_tool(name="get_all_available_blocks")
    available_blocks = {b.key: b for b in result.data}

    # Check for SDR source options
    sdr_sources = ["osmosdr_source", "soapy_source", "rtlsdr_source"]
    found_sdr = None
    for src in sdr_sources:
        if src in available_blocks:
            found_sdr = src
            print(f"  ✓ Found SDR source: {src}")
            break

    if not found_sdr and not use_simulation:
        print("  ⚠ No SDR source found (osmosdr, soapy, rtlsdr)")
        print("    Using simulation mode with analog_sig_source_x")
        use_simulation = True

    # Check for required blocks
    required = ["low_pass_filter", "analog_wfm_rcv", "audio_sink"]
    for block_key in required:
        if block_key in available_blocks:
            print(f"  ✓ Found: {block_key}")
        else:
            # Try partial match
            matches = [k for k in available_blocks if block_key in k]
            if matches:
                print(f"  ✓ Found (partial): {matches[0]}")
            else:
                print(f"  ✗ Missing: {block_key}")

    # Step 2: Create the blocks
    print("\nStep 2: Creating blocks...")
    blocks = {}

    # Source block
    if use_simulation:
        result = await client.call_tool(
            name="make_block", arguments={"block_name": "analog_sig_source_x"}
        )
        blocks["source"] = str(result.data)
        print(f"  Created simulation source: {blocks['source']}")
    else:
        result = await client.call_tool(
            name="make_block", arguments={"block_name": found_sdr}
        )
        blocks["source"] = str(result.data)
        print(f"  Created SDR source: {blocks['source']}")

    # Low pass filter
    result = await client.call_tool(
        name="make_block", arguments={"block_name": "low_pass_filter"}
    )
    blocks["lpf"] = str(result.data)
    print(f"  Created low pass filter: {blocks['lpf']}")

    # WFM (Wideband FM) demodulator
    result = await client.call_tool(
        name="make_block", arguments={"block_name": "analog_wfm_rcv"}
    )
    blocks["wfm"] = str(result.data)
    print(f"  Created WFM demod: {blocks['wfm']}")

    # Audio sink
    result = await client.call_tool(
        name="make_block", arguments={"block_name": "audio_sink"}
    )
    blocks["audio"] = str(result.data)
    print(f"  Created audio sink: {blocks['audio']}")

    # Step 3: Configure block parameters
    print("\nStep 3: Configuring block parameters...")

    freq_hz = freq_mhz * 1e6
    samp_rate = 2.4e6  # 2.4 MHz sample rate
    audio_rate = 48000  # 48 kHz audio

    if use_simulation:
        # Configure simulation source (complex sine wave at FM frequency)
        # Using GRC parameter keys (not display names) from inspect_blocks.py
        await client.call_tool(
            name="set_block_params",
            arguments={
                "block_name": blocks["source"],
                "params": {
                    "type": "complex",
                    "samp_rate": str(samp_rate),
                    "freq": "1000",  # 1 kHz tone offset
                    "amp": "1",
                    "offset": "0",
                    "waveform": "analog.GR_COS_WAVE",
                },
            },
        )
        print(f"  Configured simulation source (complex, {samp_rate/1e6} MHz)")
    else:
        # Configure RTL-SDR/OsmoSDR source
        # Using GRC parameter keys (not display names) from inspect_blocks.py
        await client.call_tool(
            name="set_block_params",
            arguments={
                "block_name": blocks["source"],
                "params": {
                    "type": "fc32",
                    "args": '"rtl=0"',
                    "sample_rate": str(samp_rate),
                    "freq0": str(freq_hz),
                    "gain0": "40",
                    "if_gain0": "20",
                    "bb_gain0": "20",
                },
            },
        )
        print(f"  Configured SDR source: {freq_mhz} MHz, {samp_rate/1e6} MS/s")

    # Configure low pass filter
    # Decimation: 2.4M → 480k (factor of 5)
    # Using GRC parameter keys (not display names) from inspect_blocks.py
    await client.call_tool(
        name="set_block_params",
        arguments={
            "block_name": blocks["lpf"],
            "params": {
                "type": "fir_filter_ccf",
                "decim": "5",
                "gain": "1",
                "samp_rate": str(samp_rate),
                "cutoff_freq": "100e3",  # 100 kHz cutoff
                "width": "10e3",  # 10 kHz transition width
                "win": "window.WIN_HAMMING",
            },
        },
    )
    print("  Configured LPF: 100 kHz cutoff, 5x decimation → 480 kHz")

    # Configure WFM demodulator
    # Input rate: 480 kHz, audio decimation: 10 → 48 kHz audio
    # Using GRC parameter keys (not display names) from inspect_blocks.py
    await client.call_tool(
        name="set_block_params",
        arguments={
            "block_name": blocks["wfm"],
            "params": {
                "quad_rate": "480e3",  # 480 kHz input rate
                "audio_decimation": "10",  # → 48 kHz output
            },
        },
    )
    print("  Configured WFM: quad_rate=480k, audio_dec=10 → 48 kHz")

    # Configure audio sink
    # Using GRC parameter keys (not display names) from inspect_blocks.py
    await client.call_tool(
        name="set_block_params",
        arguments={
            "block_name": blocks["audio"],
            "params": {
                "samp_rate": str(audio_rate),
                "device_name": "",  # Default audio device
                "ok_to_block": "True",
                "num_inputs": "1",
            },
        },
    )
    print(f"  Configured audio sink: {audio_rate} Hz")

    # Step 4: Check block ports before connecting
    print("\nStep 4: Checking block ports...")
    for name, block_name in blocks.items():
        sources = await client.call_tool(
            name="get_block_sources", arguments={"block_name": block_name}
        )
        sinks = await client.call_tool(
            name="get_block_sinks", arguments={"block_name": block_name}
        )
        src_count = len(sources.data) if sources.data else 0
        sink_count = len(sinks.data) if sinks.data else 0
        print(f"  {name} ({block_name}): {src_count} source(s), {sink_count} sink(s)")

    # Step 5: Connect the signal chain
    print("\nStep 5: Connecting signal chain...")

    # Source → Low Pass Filter
    await client.call_tool(
        name="connect_blocks",
        arguments={
            "source_block_name": blocks["source"],
            "sink_block_name": blocks["lpf"],
            "source_port_name": "0",
            "sink_port_name": "0",
        },
    )
    print(f"  {blocks['source']}:0 → {blocks['lpf']}:0")

    # Low Pass Filter → WBFM Demod
    await client.call_tool(
        name="connect_blocks",
        arguments={
            "source_block_name": blocks["lpf"],
            "sink_block_name": blocks["wfm"],
            "source_port_name": "0",
            "sink_port_name": "0",
        },
    )
    print(f"  {blocks['lpf']}:0 → {blocks['wfm']}:0")

    # WBFM Demod → Audio Sink
    await client.call_tool(
        name="connect_blocks",
        arguments={
            "source_block_name": blocks["wfm"],
            "sink_block_name": blocks["audio"],
            "source_port_name": "0",
            "sink_port_name": "0",
        },
    )
    print(f"  {blocks['wfm']}:0 → {blocks['audio']}:0")

    # Step 6: Validate the flowgraph
    print("\nStep 6: Validating flowgraph...")
    valid = await client.call_tool(name="validate_flowgraph")
    if valid.data:
        print("  ✓ Flowgraph is valid")
    else:
        print("  ✗ Flowgraph has errors:")
        errors = await client.call_tool(name="get_all_errors")
        for err in errors.data:
            print(f"    - {err}")

    # Step 7: Get all connections for verification
    print("\nStep 7: Verifying connections...")
    conns = await client.call_tool(name="get_connections")
    for conn in conns.data:
        # ConnectionModel has source/sink PortModels with parent (block) and key (port)
        print(
            f"  {conn.source.parent}:{conn.source.key} → "
            f"{conn.sink.parent}:{conn.sink.key}"
        )

    # Step 8: Save the flowgraph
    print(f"\nStep 8: Saving flowgraph to {output_path}...")
    await client.call_tool(
        name="save_flowgraph", arguments={"filepath": output_path}
    )
    print(f"  ✓ Saved to {output_path}")

    # Summary
    print(f"\n{'='*60}")
    print("FM Receiver Flowgraph Complete!")
    print(f"{'='*60}")
    print(f"  Frequency: {freq_mhz} MHz")
    print(f"  Sample Rate: {samp_rate/1e6} MS/s")
    print(f"  Audio Rate: {audio_rate} Hz")
    print(f"  Output: {output_path}")
    if use_simulation:
        print("  Mode: SIMULATION (no RTL-SDR)")
    else:
        print("  Mode: RTL-SDR")
    print()

    return blocks


async def list_all_blocks(client: Client, filter_pattern: str = None):
    """List all available GNU Radio blocks, optionally filtered."""
    result = await client.call_tool(name="get_all_available_blocks")
    blocks = sorted(result.data, key=lambda b: b.key)

    if filter_pattern:
        blocks = [b for b in blocks if filter_pattern.lower() in b.key.lower()]

    print(f"\nAvailable blocks ({len(blocks)} total):")
    for block in blocks:
        print(f"  {block.key}")

    return blocks


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Build FM Receiver with gr-mcp")
    parser.add_argument(
        "--freq", type=float, default=99.5, help="FM frequency in MHz (default: 99.5)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="/tmp/fm_receiver.grc",
        help="Output .grc file path",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use simulated source instead of RTL-SDR",
    )
    parser.add_argument(
        "--list-blocks",
        type=str,
        nargs="?",
        const="",
        help="List available blocks (optionally filter by pattern)",
    )

    args = parser.parse_args()

    async with Client(mcp_app) as client:
        if args.list_blocks is not None:
            await list_all_blocks(
                client, args.list_blocks if args.list_blocks else None
            )
        else:
            await build_fm_receiver(
                client,
                freq_mhz=args.freq,
                output_path=args.output,
                use_simulation=args.simulate,
            )


if __name__ == "__main__":
    asyncio.run(main())
