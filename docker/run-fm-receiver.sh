#!/bin/bash
# Run containerized FM receiver with audio output to host
#
# Usage: ./run-fm-receiver.sh [FREQ_MHZ] [GAIN]
#   FREQ_MHZ: FM frequency (default: 101.1)
#   GAIN: RF gain in dB (default: 10)
#
# Once running, use XML-RPC from host to retune:
#   python -c "import xmlrpc.client; p=xmlrpc.client.ServerProxy('http://localhost:8090'); p.set_freq(107.2e6)"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HOST_UID=$(id -u)
export FREQ_MHZ=${1:-101.1}
export GAIN=${2:-10}

echo "Starting FM receiver at $FREQ_MHZ MHz (gain: $GAIN dB)"
echo "XML-RPC control available at http://localhost:8090"
echo "Press Ctrl+C to stop"
echo

docker compose -f "$SCRIPT_DIR/docker-compose.fm-receiver.yml" up --build
