#!/usr/bin/env python3
"""FM Band Scanner — scan 87.5–108.0 MHz using rtl_power, rank stations by signal strength."""

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
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


if __name__ == "__main__":
    main()
