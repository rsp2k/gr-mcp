#!/bin/bash
# Entrypoint for containerized FM receiver
set -e

FREQ_MHZ=${FREQ_MHZ:-101.1}
GAIN=${GAIN:-10}

python3 -c "
import sys, subprocess, os
sys.path.insert(0, '/flowgraphs')
sys.path.insert(0, '/src')
from fm_scanner import build_fm_receiver

freq = float(os.environ.get('FREQ_MHZ', '101.1'))
gain = int(os.environ.get('GAIN', '10'))

print(f'Building FM receiver for {freq} MHz (gain {gain} dB)...')
py_path = build_fm_receiver(freq, gain=gain)
print(f'Launching {py_path.name} — Ctrl+C to stop')
print(f'XML-RPC control at http://localhost:8090')
subprocess.run([sys.executable, str(py_path)])
"
