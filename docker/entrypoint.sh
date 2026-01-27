#!/bin/bash
set -e

# Start Xvfb for headless QT rendering
Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to be ready
while ! xdpyinfo -display :99 >/dev/null 2>&1; do
    sleep 0.1
done
echo "Xvfb ready on :99"

# Optional VNC server for visual debugging
if [ "${ENABLE_VNC:-0}" = "1" ]; then
    x11vnc -display :99 -forever -nopw -shared -rfbport 5900 &
    echo "VNC server on :5900"
fi

# Run the flowgraph (passed as CMD arguments)
exec "$@"
