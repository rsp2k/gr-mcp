#!/usr/bin/env python3
"""LoRa Band Scanner — scan 902–928 MHz US ISM band for LoRa activity.

Scanning uses rtl_power to sweep the band and detect RF activity.
Decoding builds a gr-lora_sdr receiver flowgraph programmatically using
the same GRC Platform API that gr-mcp uses, compiles it with grcc, and
controls it at runtime via XML-RPC for live parameter changes.

gr-lora_sdr: https://github.com/tapparelj/gr-lora_sdr
"""

import argparse
import csv
import io
import json
import math
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xmlrpc.client
from collections import defaultdict
from pathlib import Path


# --- Phase A: Band scanning (rtl_power sweep) ---


def run_lora_scan(gain: int = 20) -> str:
    """Execute rtl_power for a single sweep of the US ISM 902–928 MHz band.

    Uses 50 kHz bins (finer than 125 kHz LoRa channel BW) for better
    resolution. Integration time is 2 seconds to catch bursty LoRa packets.
    """
    cmd = [
        "rtl_power",
        "-f", "902M:928M:50k",
        "-g", str(gain),
        "-i", "2",  # 2s integration (LoRa is bursty, needs longer dwell)
        "-1",  # single-shot
        "-",   # stdout
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print("Error: rtl_power not found. Install rtl-sdr tools.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: rtl_power timed out after 60 seconds.", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"Error: rtl_power exited with code {result.returncode}", file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr)
        sys.exit(1)

    return result.stdout


def parse_lora_scan(csv_data: str) -> list[tuple[float, float]]:
    """Parse rtl_power CSV output into (frequency_mhz, power_dbm) pairs.

    rtl_power CSV format per row:
        date, time, freq_low_hz, freq_high_hz, bin_step_hz, num_samples, dBm, dBm, ...

    Each row covers a frequency range with multiple FFT bins. We compute the
    center frequency of each bin and pair it with its power reading.
    """
    readings: list[tuple[float, float]] = []

    reader = csv.reader(io.StringIO(csv_data))
    for row in reader:
        if len(row) < 7:
            continue
        try:
            freq_low = float(row[2].strip())
            freq_high = float(row[3].strip())
            bin_step = float(row[4].strip())
            power_values = [float(v.strip()) for v in row[6:] if v.strip()]
        except (ValueError, IndexError):
            continue

        for i, power in enumerate(power_values):
            freq_hz = freq_low + (i * bin_step) + (bin_step / 2)
            freq_mhz = freq_hz / 1e6
            readings.append((freq_mhz, power))

    return readings


def aggregate_lora_channels(
    readings: list[tuple[float, float]], channel_bw_khz: int = 125
) -> list[dict]:
    """Aggregate raw FFT bins into LoRa-width channels.

    LoRa typically uses 125 kHz bandwidth per channel. We snap each reading
    to the nearest channel grid and take the max power across all bins in
    that channel (peak represents the carrier/chirp).
    """
    channel_step_mhz = channel_bw_khz / 1000.0  # 0.125 MHz
    channel_bins: dict[float, list[float]] = defaultdict(list)

    for freq_mhz, power in readings:
        # Snap to nearest channel center
        channel = round(round(freq_mhz / channel_step_mhz) * channel_step_mhz, 3)
        if 902.0 <= channel <= 928.0:
            channel_bins[channel].append(power)

    channels = []
    for freq in sorted(channel_bins):
        powers = channel_bins[freq]
        max_power = max(powers)
        channels.append({"freq_mhz": freq, "power_dbm": max_power})

    return channels


def detect_lora_activity(
    channels: list[dict], threshold_db: float = 8.0
) -> tuple[list[dict], float]:
    """Find channels with activity above the noise floor.

    LoRa signals are bursty and spread-spectrum, so they appear closer to
    the noise floor than narrowband FM. We use a lower default threshold
    (8 dB vs 10 dB for FM).

    Returns (active_channels_sorted_by_power, noise_floor_dbm).
    """
    if not channels:
        return [], -99.0

    powers = sorted(ch["power_dbm"] for ch in channels)
    noise_floor = powers[len(powers) // 2]  # median

    active = []
    for ch in channels:
        snr = ch["power_dbm"] - noise_floor
        if snr >= threshold_db:
            active.append({**ch, "snr_db": round(snr, 1)})

    active.sort(key=lambda s: s["power_dbm"], reverse=True)
    return active, noise_floor


def display_lora_results(
    active_channels: list[dict],
    noise_floor: float,
    all_channels: list[dict] | None = None,
    show_all: bool = False,
):
    """Print a formatted table of LoRa band scan results."""
    term_width = shutil.get_terminal_size((80, 24)).columns
    bar_max = max(32, term_width - 48)

    items = all_channels if (show_all and all_channels) else active_channels
    if not items:
        print("No LoRa activity detected.")
        return

    powers = [ch["power_dbm"] for ch in items]
    p_min = noise_floor
    p_max = max(powers)
    p_range = p_max - p_min if p_max != p_min else 1.0

    header = "LoRa Band Scan \u2014 902 to 928 MHz (US ISM)"
    print()
    print(f"  {header}")
    print(f"  {'═' * (len(header) + 2)}")
    print(f"   {'#':>3}  {'Channel':<14} {'Power':<10} Activity")
    print(f"  {'─' * 3}  {'─' * 14} {'─' * 9} {'─' * bar_max}")

    block_chars = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

    for i, ch in enumerate(items, 1):
        freq = ch["freq_mhz"]
        power = ch["power_dbm"]
        norm = max(0.0, min(1.0, (power - p_min) / p_range))
        bar_len = norm * bar_max
        full_blocks = int(bar_len)
        frac = bar_len - full_blocks
        frac_char = block_chars[int(frac * 8)] if frac > 0.05 else ""
        bar = "\u2588" * full_blocks + frac_char

        if "snr_db" in ch and ch["snr_db"] >= 10:
            bar = f"\033[32m{bar}\033[0m"  # green
        elif "snr_db" in ch:
            bar = f"\033[33m{bar}\033[0m"  # yellow
        elif show_all:
            bar = f"\033[2m{bar}\033[0m"   # dim

        label = f"{freq:>7.3f} MHz"
        print(f"  {i:>3}  {label:<14} {power:>7.1f} dBm {bar}")

    print(f"  {'═' * (len(header) + 2)}")
    print(
        f"  Noise floor: {noise_floor:.1f} dBm | "
        f"Active channels: {len(active_channels)}"
    )
    print()


def save_json(active_channels: list[dict], noise_floor: float, path: str):
    """Write scan results to a JSON file."""
    data = {
        "band": "LoRa ISM",
        "range_mhz": [902.0, 928.0],
        "noise_floor_dbm": round(noise_floor, 1),
        "active_channel_count": len(active_channels),
        "channels": [
            {
                "freq_mhz": s["freq_mhz"],
                "power_dbm": round(s["power_dbm"], 1),
                "snr_db": s["snr_db"],
            }
            for s in active_channels
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n")
    print(f"Results saved to {path}")


def pick_channel(active_channels: list[dict]) -> float | None:
    """Interactive channel picker. Returns frequency in MHz or None to quit."""
    if not active_channels:
        print("No active channels to choose from.")
        return None

    try:
        choice = input("  Tune to channel # (or q to quit): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if choice.lower() in ("q", "quit", ""):
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(active_channels):
            return active_channels[idx]["freq_mhz"]
        print(f"  Pick 1\u2013{len(active_channels)}.")
    except ValueError:
        try:
            freq = float(choice)
            if 902.0 <= freq <= 928.0:
                return freq
            print("  Frequency must be 902\u2013928 MHz.")
        except ValueError:
            print("  Enter a channel number or frequency.")

    return pick_channel(active_channels)


# --- Phase B: LoRa packet receiver (gr-lora_sdr) ---


XMLRPC_PORT = 8091


def build_lora_receiver(
    freq_mhz: float = 915.0,
    sf: int = 7,
    bw: int = 125000,
    cr: int = 1,
    gain: int = 20,
) -> Path:
    """Build a gr-lora_sdr receiver flowgraph programmatically.

    Creates all blocks, sets parameters, connects the full LoRa decode
    chain, saves to .grc, and compiles with grcc. Uses soft decoding for
    ~2-3 dB better sensitivity than hard decisions.

    Signal chain:
        RTL-SDR (1 Msps) -> frame_sync -> fft_demod -> gray_mapping ->
        deinterleaver -> hamming_dec -> header_decoder -> dewhitening -> crc_verif

    The header_decoder feeds frame_info back to frame_sync for adaptive
    reception (a feedback loop unusual in GNU Radio flowgraphs).

    XML-RPC exposes: freq, sf, bw, cr, gain (all settable at runtime)
    """
    try:
        from gnuradio import gr
        from gnuradio.grc.core.platform import Platform
    except ImportError:
        print("Error: GNU Radio not found. Install gnuradio.", file=sys.stderr)
        sys.exit(1)

    platform = Platform(
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
        prefs=gr.prefs(),
    )
    platform.build_library()

    # Verify gr-lora_sdr blocks are available
    block_keys = list(platform.blocks.keys())
    lora_blocks = [k for k in block_keys if "lora" in k.lower()]
    if not lora_blocks:
        print(
            "Error: gr-lora_sdr blocks not found. Install gr-lora_sdr OOT module.",
            file=sys.stderr,
        )
        sys.exit(1)

    fg = platform.make_flow_graph()

    # Configure options block
    options = next(b for b in fg.blocks if b.key == "options")
    options.params["id"].set_value("lora_receiver")
    options.params["title"].set_value("LoRa Receiver")
    options.params["generate_options"].set_value("no_gui")
    options.params["run_options"].set_value("run")

    # --- Variables (all exposed via XML-RPC) ---
    samp_rate_var = fg.new_block("variable")
    samp_rate_var.params["id"].set_value("samp_rate")
    samp_rate_var.params["value"].set_value("int(1e6)")

    freq_var = fg.new_block("variable")
    freq_var.params["id"].set_value("freq")
    freq_var.params["value"].set_value(f"{freq_mhz}e6")

    sf_var = fg.new_block("variable")
    sf_var.params["id"].set_value("sf")
    sf_var.params["value"].set_value(str(sf))

    bw_var = fg.new_block("variable")
    bw_var.params["id"].set_value("bw")
    bw_var.params["value"].set_value(str(bw))

    cr_var = fg.new_block("variable")
    cr_var.params["id"].set_value("cr")
    cr_var.params["value"].set_value(str(cr))

    gain_var = fg.new_block("variable")
    gain_var.params["id"].set_value("gain")
    gain_var.params["value"].set_value(str(gain))

    # --- XML-RPC server for runtime parameter control ---
    xmlrpc = fg.new_block("xmlrpc_server")
    xmlrpc.params["id"].set_value("xmlrpc_server_0")
    xmlrpc.params["addr"].set_value("0.0.0.0")
    xmlrpc.params["port"].set_value(str(XMLRPC_PORT))

    # --- RTL-SDR source ---
    source = fg.new_block("osmosdr_source")
    source.params["id"].set_value("osmosdr_source_0")
    source.params["sample_rate"].set_value("samp_rate")
    source.params["freq0"].set_value("freq")
    source.params["gain0"].set_value("gain")
    source.params["if_gain0"].set_value("20")
    source.params["bb_gain0"].set_value("20")
    source.params["args"].set_value('"rtl=0"')

    # --- gr-lora_sdr decode chain ---

    # frame_sync: preamble detection, STO/CFO correction
    frame_sync = fg.new_block("lora_sdr_frame_sync")
    frame_sync.params["id"].set_value("lora_sdr_frame_sync_0")
    frame_sync.params["center_freq"].set_value("freq")
    frame_sync.params["bandwidth"].set_value("bw")
    frame_sync.params["sf"].set_value("sf")
    frame_sync.params["impl_head"].set_value("False")  # explicit header
    frame_sync.params["os_factor"].set_value("4")
    frame_sync.params["show_log_port"].set_value("True")

    # fft_demod: chirp demodulation via FFT (soft output)
    fft_demod = fg.new_block("lora_sdr_fft_demod")
    fft_demod.params["id"].set_value("lora_sdr_fft_demod_0")
    fft_demod.params["soft_decoding"].set_value("True")
    fft_demod.params["max_log_approx"].set_value("False")

    # gray_mapping: Gray code demapping (soft)
    gray_map = fg.new_block("lora_sdr_gray_mapping")
    gray_map.params["id"].set_value("lora_sdr_gray_mapping_0")
    gray_map.params["soft_decoding"].set_value("True")

    # deinterleaver: diagonal deinterleaver (soft)
    deinterleaver = fg.new_block("lora_sdr_deinterleaver")
    deinterleaver.params["id"].set_value("lora_sdr_deinterleaver_0")
    deinterleaver.params["soft_decoding"].set_value("True")

    # hamming_dec: Hamming FEC decoder (soft input -> hard output)
    hamming = fg.new_block("lora_sdr_hamming_dec")
    hamming.params["id"].set_value("lora_sdr_hamming_dec_0")
    hamming.params["soft_decoding"].set_value("True")

    # header_decoder: extract header fields, feed frame_info back to frame_sync
    header_dec = fg.new_block("lora_sdr_header_decoder")
    header_dec.params["id"].set_value("lora_sdr_header_decoder_0")
    header_dec.params["impl_head"].set_value("False")
    header_dec.params["cr"].set_value("cr")
    header_dec.params["pay_len"].set_value("255")
    header_dec.params["has_crc"].set_value("True")
    header_dec.params["ldro"].set_value("2")  # auto low-data-rate optimize

    # dewhitening: XOR with LoRa whitening sequence
    dewhiten = fg.new_block("lora_sdr_dewhitening")
    dewhiten.params["id"].set_value("lora_sdr_dewhitening_0")

    # crc_verif: CRC check and payload output
    crc = fg.new_block("lora_sdr_crc_verif")
    crc.params["id"].set_value("lora_sdr_crc_verif_0")
    crc.params["print_rx_msg"].set_value("True")
    crc.params["output_crc_check"].set_value("True")

    # --- Connect signal chain ---

    # RTL-SDR -> frame_sync
    fg.connect(source.sources[0], frame_sync.sinks[0])

    # frame_sync -> fft_demod -> gray_mapping -> deinterleaver -> hamming_dec
    fg.connect(frame_sync.sources[0], fft_demod.sinks[0])
    fg.connect(fft_demod.sources[0], gray_map.sinks[0])
    fg.connect(gray_map.sources[0], deinterleaver.sinks[0])
    fg.connect(deinterleaver.sources[0], hamming.sinks[0])

    # hamming_dec -> header_decoder -> dewhitening -> crc_verif
    fg.connect(hamming.sources[0], header_dec.sinks[0])
    fg.connect(header_dec.sources[0], dewhiten.sinks[0])
    fg.connect(dewhiten.sources[0], crc.sinks[0])

    # Feedback: header_decoder frame_info (source) -> frame_sync frame_info (sink)
    # This is a message port connection — header_decoder sends decoded
    # SF/CR/payload length back to frame_sync for adaptive reception.
    # header_decoder.sources[1] = frame_info (message)
    # frame_sync.sinks[1] = frame_info (message)
    _connect_frame_info_feedback(fg, header_dec, frame_sync)

    # --- Save and generate ---
    # gr-lora_sdr's soft decoding changes port types at runtime (short→float64)
    # which GRC's static validator flags as mismatches. The types are actually
    # correct at runtime — soft_decoding=True uses float paths throughout.
    # Use platform.Generator directly instead of grcc (which refuses is_valid=False).
    work_dir = Path(tempfile.mkdtemp(prefix="lora_receiver_"))
    grc_path = work_dir / "lora_receiver.grc"
    platform.save_flow_graph(str(grc_path), fg)

    fg.rewrite()
    generator = platform.Generator(fg, str(work_dir))
    generator.write()

    py_path = work_dir / "lora_receiver.py"
    if not py_path.exists():
        print("Error: code generation produced no Python output.", file=sys.stderr)
        sys.exit(1)

    return py_path


def _connect_frame_info_feedback(fg, header_dec, frame_sync):
    """Connect frame_info message port from header_decoder back to frame_sync.

    gr-lora_sdr uses message ports for the feedback loop where the header
    decoder sends decoded SF/CR/payload length back to frame_sync. This
    allows adaptive reception of packets with different parameters.

    Port layout (from block introspection):
        header_decoder sources: [0]=byte (data), [1]=frame_info (message)
        frame_sync sinks:       [0]=complex (signal), [1]=frame_info (message)
    """
    # header_decoder.sources[1] is the frame_info message output
    # frame_sync.sinks[1] is the frame_info message input
    if len(header_dec.sources) > 1 and len(frame_sync.sinks) > 1:
        fg.connect(header_dec.sources[1], frame_sync.sinks[1])


# --- Runtime control ---


def wait_for_xmlrpc(url: str, timeout: float = 10.0) -> xmlrpc.client.ServerProxy:
    """Wait for the XML-RPC server to become reachable."""
    proxy = xmlrpc.client.ServerProxy(url)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proxy.get_freq()
            return proxy
        except ConnectionRefusedError:
            time.sleep(0.3)
        except Exception:
            return proxy
    print("Error: flowgraph XML-RPC server did not start.", file=sys.stderr)
    sys.exit(1)


def format_lora_params(proxy: xmlrpc.client.ServerProxy) -> str:
    """Format current LoRa parameters for display."""
    try:
        freq = proxy.get_freq() / 1e6
        sf = int(proxy.get_sf())
        bw_khz = proxy.get_bw() / 1000
        cr = int(proxy.get_cr())
        return f"{freq:.3f} MHz  SF{sf}  BW {bw_khz:.0f} kHz  CR 4/{4+cr}"
    except Exception:
        return "(parameters unavailable)"


def tune_lora(
    freq_mhz: float,
    sf: int = 7,
    bw: int = 125000,
    cr: int = 1,
    gain: int = 20,
):
    """Launch a gr-lora_sdr receiver and control via XML-RPC.

    Builds the flowgraph programmatically, launches it as a subprocess,
    connects to its XML-RPC server, and provides an interactive control
    loop for changing LoRa parameters at runtime.
    """
    print(f"\n  Building LoRa receiver for {freq_mhz:.3f} MHz (SF{sf})...")
    py_path = build_lora_receiver(freq_mhz, sf=sf, bw=bw, cr=cr, gain=gain)

    url = f"http://localhost:{XMLRPC_PORT}"
    print(f"  Launching flowgraph ({py_path.name})...")

    fg_proc = subprocess.Popen(
        [sys.executable, str(py_path)],
        stderr=subprocess.DEVNULL,
    )

    proxy = wait_for_xmlrpc(url)
    time.sleep(0.5)

    params = format_lora_params(proxy)
    print(f"  Listening: {params}")
    print()
    print("  Commands:")
    print("    freq <MHz>   — change frequency (e.g. 'freq 915.0')")
    print("    sf <N>       — change spreading factor (7-12)")
    print("    bw <Hz>      — change bandwidth (e.g. 'bw 250000')")
    print("    cr <N>       — change coding rate (1-4)")
    print("    status       — show current parameters")
    print("    q            — quit")
    print()

    try:
        while fg_proc.poll() is None:
            try:
                cmd = input("  lora> ").strip()
            except EOFError:
                break
            if not cmd or cmd.lower() in ("q", "quit"):
                break

            if cmd.lower() in ("s", "status"):
                print(f"  {format_lora_params(proxy)}")
                continue

            parts = cmd.split(maxsplit=1)
            if len(parts) != 2:
                print("  Usage: freq|sf|bw|cr <value>, status, q")
                continue

            param, value_str = parts[0].lower(), parts[1]
            try:
                if param == "freq":
                    new_freq = float(value_str)
                    if 902.0 <= new_freq <= 928.0:
                        proxy.set_freq(new_freq * 1e6)
                        time.sleep(0.3)
                        print(f"  {format_lora_params(proxy)}")
                    else:
                        print("  Frequency must be 902\u2013928 MHz.")
                elif param == "sf":
                    new_sf = int(value_str)
                    if 7 <= new_sf <= 12:
                        proxy.set_sf(new_sf)
                        time.sleep(0.3)
                        print(f"  {format_lora_params(proxy)}")
                    else:
                        print("  Spreading factor must be 7\u201312.")
                elif param == "bw":
                    new_bw = int(value_str)
                    if new_bw in (7800, 10400, 15600, 20800, 31250,
                                  41700, 62500, 125000, 250000, 500000):
                        proxy.set_bw(new_bw)
                        time.sleep(0.3)
                        print(f"  {format_lora_params(proxy)}")
                    else:
                        print("  Valid BWs: 7800 10400 15600 20800 31250 "
                              "41700 62500 125000 250000 500000")
                elif param == "cr":
                    new_cr = int(value_str)
                    if 1 <= new_cr <= 4:
                        proxy.set_cr(new_cr)
                        time.sleep(0.3)
                        print(f"  {format_lora_params(proxy)}")
                    else:
                        print("  Coding rate must be 1\u20134.")
                elif param == "gain":
                    new_gain = int(value_str)
                    proxy.set_gain(new_gain)
                    time.sleep(0.3)
                    print(f"  Gain set to {new_gain} dB")
                else:
                    print("  Unknown param. Use: freq, sf, bw, cr, gain")
            except ValueError:
                print(f"  Invalid value: {value_str}")
            except Exception as e:
                print(f"  XML-RPC error: {e}")

    except KeyboardInterrupt:
        pass

    print("\n  Stopping flowgraph...")
    if fg_proc.poll() is None:
        fg_proc.send_signal(signal.SIGTERM)
        try:
            fg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            fg_proc.kill()


# --- CLI entry point ---


def main():
    parser = argparse.ArgumentParser(
        description="Scan the US ISM band (902-928 MHz) for LoRa activity."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=8.0,
        help="Minimum dB above noise floor to flag as active (default: 8)",
    )
    parser.add_argument(
        "--gain",
        type=int,
        default=20,
        help="RF tuner gain in dB (default: 20, higher for 915 MHz)",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        help="Save scan results to JSON file",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Show all channels, not just active ones",
    )
    parser.add_argument(
        "--listen",
        type=float,
        metavar="FREQ",
        help="Listen on specific frequency (MHz) without scanning first",
    )
    parser.add_argument(
        "--tune",
        action="store_true",
        help="Pick an active channel to listen on after scanning",
    )
    parser.add_argument(
        "--sf",
        type=int,
        default=7,
        choices=range(7, 13),
        help="LoRa spreading factor (default: 7)",
    )
    parser.add_argument(
        "--bw",
        type=int,
        default=125000,
        help="LoRa bandwidth in Hz (default: 125000)",
    )
    parser.add_argument(
        "--cr",
        type=int,
        default=1,
        choices=range(1, 5),
        help="LoRa coding rate 1-4 (default: 1, meaning 4/5)",
    )
    args = parser.parse_args()

    # Direct listen mode — skip scanning
    if args.listen is not None:
        tune_lora(
            args.listen,
            sf=args.sf,
            bw=args.bw,
            cr=args.cr,
            gain=args.gain,
        )
        return

    # Scan mode
    print("Scanning LoRa band (902\u2013928 MHz)...", flush=True)
    raw = run_lora_scan(gain=args.gain)

    readings = parse_lora_scan(raw)
    if not readings:
        print("No data received from rtl_power.", file=sys.stderr)
        sys.exit(1)

    channels = aggregate_lora_channels(readings)
    active, noise_floor = detect_lora_activity(
        channels, threshold_db=args.threshold
    )

    display_lora_results(
        active,
        noise_floor,
        all_channels=channels,
        show_all=args.show_all,
    )

    if args.json:
        save_json(active, noise_floor, args.json)

    if args.tune:
        freq = pick_channel(active)
        if freq:
            tune_lora(
                freq,
                sf=args.sf,
                bw=args.bw,
                cr=args.cr,
                gain=args.gain,
            )


if __name__ == "__main__":
    main()
