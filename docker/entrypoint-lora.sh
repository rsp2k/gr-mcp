#!/bin/bash
# Entrypoint for containerized LoRa receiver
set -e

FREQ_MHZ=${FREQ_MHZ:-915.0}
SF=${SF:-7}
BW=${BW:-125000}
CR=${CR:-1}
GAIN=${GAIN:-20}

python3 -c "
import sys, subprocess, os
sys.path.insert(0, '/flowgraphs')
sys.path.insert(0, '/src')
from lora_scanner import build_lora_receiver

freq = float(os.environ.get('FREQ_MHZ', '915.0'))
sf = int(os.environ.get('SF', '7'))
bw = int(os.environ.get('BW', '125000'))
cr = int(os.environ.get('CR', '1'))
gain = int(os.environ.get('GAIN', '20'))

print(f'Building LoRa receiver for {freq} MHz (SF{sf}, BW {bw} Hz, CR 4/{4+cr})...')
py_path = build_lora_receiver(freq, sf=sf, bw=bw, cr=cr, gain=gain)
print(f'Launching {py_path.name} — Ctrl+C to stop')
print(f'XML-RPC control at http://localhost:8091')
subprocess.run([sys.executable, str(py_path)])
"
