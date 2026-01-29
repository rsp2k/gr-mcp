#!/usr/bin/env python3
"""FM Band Scanner — scan 87.5–108.0 MHz using rtl_power, rank stations by signal strength.

Tuning builds a GNU Radio flowgraph programmatically using the same GRC
Platform API that gr-mcp uses, compiles it with grcc, and controls it at
runtime via XML-RPC for live frequency changes.
"""

import argparse
import csv
import io
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xmlrpc.client
from collections import defaultdict
from pathlib import Path


def run_rtl_power(gain: int = 10) -> str:
    """Execute rtl_power for a single FM band sweep and return raw CSV output.

    The scan covers 87.5–108.0 MHz in 200 kHz bins (US FM channel spacing)
    with 1-second integration time per sweep segment.
    """
    cmd = [
        "rtl_power",
        "-f", "87.5M:108M:200k",
        "-g", str(gain),
        "-i", "1",
        "-1",  # single-shot
        "-",   # stdout
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print("Error: rtl_power not found. Install rtl-sdr tools.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: rtl_power timed out after 30 seconds.", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"Error: rtl_power exited with code {result.returncode}", file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr)
        sys.exit(1)

    return result.stdout


def parse_scan(csv_data: str) -> list[tuple[float, float]]:
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
            # num_samples = int(row[5].strip())  # not needed
            power_values = [float(v.strip()) for v in row[6:] if v.strip()]
        except (ValueError, IndexError):
            continue

        # Map each FFT bin to its center frequency
        for i, power in enumerate(power_values):
            freq_hz = freq_low + (i * bin_step) + (bin_step / 2)
            freq_mhz = freq_hz / 1e6
            readings.append((freq_mhz, power))

    return readings


def aggregate_channels(readings: list[tuple[float, float]]) -> list[dict]:
    """Aggregate raw FFT bins into 200 kHz FM channels.

    FM stations in the US are spaced at odd multiples of 100 kHz
    (87.9, 88.1, 88.3, ..., 107.9). Each occupies ~200 kHz bandwidth.
    We snap each reading to the nearest standard channel and take the
    max power across all bins in that channel.
    """
    channel_bins: dict[float, list[float]] = defaultdict(list)

    for freq_mhz, power in readings:
        # Snap to nearest 0.2 MHz FM channel (87.5, 87.7, 87.9, ...)
        channel = round(round(freq_mhz / 0.2) * 0.2, 1)
        if 87.5 <= channel <= 108.0:
            channel_bins[channel].append(power)

    channels = []
    for freq in sorted(channel_bins):
        powers = channel_bins[freq]
        # Use max power — peak represents the carrier
        max_power = max(powers)
        channels.append({"freq_mhz": freq, "power_dbm": max_power})

    return channels


def detect_stations(
    channels: list[dict], threshold_db: float = 10.0
) -> tuple[list[dict], float]:
    """Find stations that rise above the noise floor.

    The noise floor is estimated as the median power across all channels.
    A channel is flagged as a station if its power exceeds
    noise_floor + threshold_db.

    Returns (stations_sorted_by_power, noise_floor_dbm).
    """
    if not channels:
        return [], -99.0

    powers = sorted(ch["power_dbm"] for ch in channels)
    noise_floor = powers[len(powers) // 2]  # median

    stations = []
    for ch in channels:
        snr = ch["power_dbm"] - noise_floor
        if snr >= threshold_db:
            stations.append({**ch, "snr_db": round(snr, 1)})

    stations.sort(key=lambda s: s["power_dbm"], reverse=True)
    return stations, noise_floor


def display_results(
    stations: list[dict],
    noise_floor: float,
    all_channels: list[dict] | None = None,
    show_all: bool = False,
):
    """Print a formatted table of scan results to the terminal."""
    term_width = shutil.get_terminal_size((80, 24)).columns
    bar_max = max(32, term_width - 42)

    items = all_channels if (show_all and all_channels) else stations
    if not items:
        print("No stations detected.")
        return

    # Bar scaling: use noise floor as baseline so every station gets a visible bar
    powers = [ch["power_dbm"] for ch in items]
    p_min = noise_floor
    p_max = max(powers)
    p_range = p_max - p_min if p_max != p_min else 1.0

    header = "FM Band Scan \u2014 87.5 to 108.0 MHz"
    print()
    print(f"  {header}")
    print(f"  {'═' * (len(header) + 2)}")
    print(f"   {'#':>3}  {'Frequency':<12} {'Power':<10} Signal")
    print(f"  {'─' * 3}  {'─' * 12} {'─' * 9} {'─' * bar_max}")

    block_chars = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

    for i, ch in enumerate(items, 1):
        freq = ch["freq_mhz"]
        power = ch["power_dbm"]
        # Normalize to [0, 1]
        norm = max(0.0, min(1.0, (power - p_min) / p_range))
        bar_len = norm * bar_max
        full_blocks = int(bar_len)
        frac = bar_len - full_blocks
        frac_char = block_chars[int(frac * 8)] if frac > 0.05 else ""
        bar = "\u2588" * full_blocks + frac_char

        # Color: green for strong, yellow for mid, dim for weak
        if "snr_db" in ch and ch["snr_db"] >= 10:
            bar = f"\033[32m{bar}\033[0m"  # green
        elif "snr_db" in ch:
            bar = f"\033[33m{bar}\033[0m"  # yellow
        elif show_all:
            bar = f"\033[2m{bar}\033[0m"   # dim

        label = f"{freq:>5.1f} MHz"
        print(f"  {i:>3}  {label:<12} {power:>7.1f} dBm {bar}")

    print(f"  {'═' * (len(header) + 2)}")
    print(
        f"  Noise floor: {noise_floor:.1f} dBm | "
        f"Stations found: {len(stations)}"
    )
    print()


def save_json(stations: list[dict], noise_floor: float, path: str):
    """Write scan results to a JSON file."""
    data = {
        "band": "FM",
        "range_mhz": [87.5, 108.0],
        "noise_floor_dbm": round(noise_floor, 1),
        "station_count": len(stations),
        "stations": [
            {
                "freq_mhz": s["freq_mhz"],
                "power_dbm": round(s["power_dbm"], 1),
                "snr_db": s["snr_db"],
            }
            for s in stations
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n")
    print(f"Results saved to {path}")


def pick_station(stations: list[dict]) -> float | None:
    """Interactive station picker. Returns frequency in MHz or None to quit."""
    if not stations:
        print("No stations to choose from.")
        return None

    try:
        choice = input("  Tune to station # (or q to quit): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if choice.lower() in ("q", "quit", ""):
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(stations):
            return stations[idx]["freq_mhz"]
        print(f"  Pick 1–{len(stations)}.")
    except ValueError:
        # Maybe they typed a frequency directly
        try:
            freq = float(choice)
            if 87.5 <= freq <= 108.0:
                return freq
            print("  Frequency must be 87.5–108.0 MHz.")
        except ValueError:
            print("  Enter a station number or frequency.")

    return pick_station(stations)


XMLRPC_PORT = 8090


def build_fm_receiver(freq_mhz: float, gain: int = 10) -> Path:
    """Build an FM receiver flowgraph programmatically — no GRC template needed.

    Creates all blocks, sets parameters, connects the signal chain, saves to
    .grc, and compiles with grcc. This uses the same middleware that gr-mcp's
    MCP tools use, proving end-to-end programmatic flowgraph construction.

    Signal chain:
        RTL-SDR (2.4 MHz) → LPF (decim 5) → WBFM Demod (decim 10) → Audio (48 kHz)
    """
    # Late import to avoid dependency when just scanning (no --tune)
    try:
        from gnuradio import gr
        from gnuradio.grc.core.platform import Platform
    except ImportError:
        print("Error: GNU Radio not found. Install gnuradio.", file=sys.stderr)
        sys.exit(1)

    # Initialize platform (same as gr-mcp main.py)
    platform = Platform(
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
        prefs=gr.prefs(),
    )
    platform.build_library()

    # Create flowgraph
    fg = platform.make_flow_graph()

    # Configure options block (flowgraph metadata)
    options = next(b for b in fg.blocks if b.key == "options")
    options.params["id"].set_value("fm_receiver")
    options.params["title"].set_value("FM Receiver")
    options.params["generate_options"].set_value("no_gui")
    options.params["run_options"].set_value("run")

    # Add samp_rate variable (not included by default, unlike GRC GUI)
    samp_rate = fg.new_block("variable")
    samp_rate.params["id"].set_value("samp_rate")
    samp_rate.params["value"].set_value("int(2.4e6)")

    # Add freq variable
    freq_block = fg.new_block("variable")
    freq_block.params["id"].set_value("freq")
    freq_block.params["value"].set_value(f"{freq_mhz}e6")

    # RTL-SDR source
    source = fg.new_block("osmosdr_source")
    source.params["id"].set_value("osmosdr_source_0")
    source.params["sample_rate"].set_value("samp_rate")
    source.params["freq0"].set_value("freq")  # Reference the variable
    source.params["gain0"].set_value(str(gain))
    source.params["if_gain0"].set_value("20")
    source.params["bb_gain0"].set_value("20")
    source.params["args"].set_value('"rtl=0"')

    # Low-pass filter: 2.4 MHz → 480 kHz (decim 5)
    lpf = fg.new_block("low_pass_filter")
    lpf.params["id"].set_value("low_pass_filter_0")
    lpf.params["type"].set_value("fir_filter_ccf")
    lpf.params["decim"].set_value("5")
    lpf.params["gain"].set_value("1")
    lpf.params["samp_rate"].set_value("samp_rate")
    lpf.params["cutoff_freq"].set_value("100e3")
    lpf.params["width"].set_value("10e3")
    lpf.params["win"].set_value("window.WIN_HAMMING")
    lpf.params["beta"].set_value("6.76")

    # WBFM demodulator: 480 kHz → 48 kHz (decim 10)
    wfm = fg.new_block("analog_wfm_rcv")
    wfm.params["id"].set_value("analog_wfm_rcv_0")
    wfm.params["quad_rate"].set_value("480e3")
    wfm.params["audio_decimation"].set_value("10")

    # Audio sink
    audio = fg.new_block("audio_sink")
    audio.params["id"].set_value("audio_sink_0")
    audio.params["samp_rate"].set_value("48000")
    audio.params["ok_to_block"].set_value("True")

    # XML-RPC server for runtime control
    xmlrpc = fg.new_block("xmlrpc_server")
    xmlrpc.params["id"].set_value("xmlrpc_server_0")
    xmlrpc.params["addr"].set_value("0.0.0.0")
    xmlrpc.params["port"].set_value(str(XMLRPC_PORT))

    # Connect signal chain
    # source:0 → lpf:0
    fg.connect(source.sources[0], lpf.sinks[0])
    # lpf:0 → wfm:0
    fg.connect(lpf.sources[0], wfm.sinks[0])
    # wfm:0 → audio:0
    fg.connect(wfm.sources[0], audio.sinks[0])

    # Save and compile
    work_dir = Path(tempfile.mkdtemp(prefix="fm_receiver_"))
    grc_path = work_dir / "fm_receiver.grc"
    platform.save_flow_graph(str(grc_path), fg)

    result = subprocess.run(
        ["grcc", "-o", str(work_dir), str(grc_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: grcc compilation failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    py_files = list(work_dir.glob("*.py"))
    if not py_files:
        print("Error: grcc produced no Python output.", file=sys.stderr)
        sys.exit(1)

    return py_files[0]


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
            # Fault from missing method is fine — server is up
            return proxy
    print("Error: flowgraph XML-RPC server did not start.", file=sys.stderr)
    sys.exit(1)


def tune_station(freq_mhz: float, gain: int = 10):
    """Launch a GNU Radio FM receiver and tune via XML-RPC.

    Builds a flowgraph programmatically using the GRC Platform API (the same
    approach gr-mcp uses), compiles it with grcc, launches the Python flowgraph
    as a subprocess, and connects to its XML-RPC server for live frequency
    control.
    """
    print(f"\n  Building FM receiver for {freq_mhz:.1f} MHz...")
    py_path = build_fm_receiver(freq_mhz, gain)

    url = f"http://localhost:{XMLRPC_PORT}"
    print(f"  Launching flowgraph ({py_path.name})...")

    fg_proc = subprocess.Popen(
        [sys.executable, str(py_path)],
        stderr=subprocess.DEVNULL,
    )

    proxy = wait_for_xmlrpc(url)
    current = proxy.get_freq()
    print(f"  Receiving {current / 1e6:.1f} MHz — enter frequency to retune, q to quit\n")

    try:
        while fg_proc.poll() is None:
            try:
                cmd = input("  freq> ").strip()
            except EOFError:
                break
            if cmd.lower() in ("q", "quit", ""):
                break
            try:
                new_freq = float(cmd)
                if 87.5 <= new_freq <= 108.0:
                    proxy.set_freq(new_freq * 1e6)
                    print(f"  Tuned to {new_freq:.1f} MHz")
                else:
                    print("  Frequency must be 87.5–108.0 MHz.")
            except ValueError:
                print("  Enter a frequency (MHz) or q to quit.")
    except KeyboardInterrupt:
        pass

    print("\n  Stopping flowgraph...")
    if fg_proc.poll() is None:
        fg_proc.send_signal(signal.SIGTERM)
        try:
            fg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            fg_proc.kill()


def main():
    parser = argparse.ArgumentParser(
        description="Scan the US FM band and rank stations by signal strength."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="Minimum dB above noise floor to flag as station (default: 10)",
    )
    parser.add_argument(
        "--gain",
        type=int,
        default=10,
        help="RF tuner gain in dB (default: 10)",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Show all channels, not just detected stations",
    )
    parser.add_argument(
        "--tune",
        nargs="?",
        const="pick",
        metavar="FREQ",
        help="Tune to a station after scanning (optionally specify frequency in MHz)",
    )
    args = parser.parse_args()

    print("Scanning FM band (87.5–108.0 MHz)...", flush=True)
    raw = run_rtl_power(gain=args.gain)

    readings = parse_scan(raw)
    if not readings:
        print("No data received from rtl_power.", file=sys.stderr)
        sys.exit(1)

    channels = aggregate_channels(readings)
    stations, noise_floor = detect_stations(channels, threshold_db=args.threshold)

    display_results(
        stations,
        noise_floor,
        all_channels=channels,
        show_all=args.show_all,
    )

    if args.json:
        save_json(stations, noise_floor, args.json)

    if args.tune is not None:
        if args.tune == "pick":
            freq = pick_station(stations)
        else:
            freq = float(args.tune)
        if freq:
            tune_station(freq, gain=args.gain)


if __name__ == "__main__":
    main()
