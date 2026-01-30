#!/bin/bash
# Run containerized LoRa receiver
#
# Usage: ./run-lora-receiver.sh [FREQ_MHZ] [SF] [BW] [CR] [GAIN]
#   FREQ_MHZ: Center frequency in MHz (default: 915.0)
#   SF:       Spreading factor 7-12 (default: 7)
#   BW:       Bandwidth in Hz (default: 125000)
#   CR:       Coding rate 1-4 (default: 1)
#   GAIN:     RF gain in dB (default: 20)
#
# Once running, use XML-RPC from host to change parameters:
#   python -c "import xmlrpc.client; p=xmlrpc.client.ServerProxy('http://localhost:8091'); p.set_sf(10)"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export FREQ_MHZ=${1:-915.0}
export SF=${2:-7}
export BW=${3:-125000}
export CR=${4:-1}
export GAIN=${5:-20}

echo "Starting LoRa receiver at $FREQ_MHZ MHz (SF$SF, BW $BW Hz, CR 4/$((4+CR)), gain $GAIN dB)"
echo "XML-RPC control available at http://localhost:8091"
echo "Press Ctrl+C to stop"
echo

docker compose -f "$SCRIPT_DIR/docker-compose.lora-receiver.yml" up --build
