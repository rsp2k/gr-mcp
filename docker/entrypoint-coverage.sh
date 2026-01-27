#!/bin/bash
# Coverage-aware entrypoint for GNU Radio flowgraph execution
#
# When ENABLE_COVERAGE=1:
#   - Wraps the command with `coverage run`
#   - Writes coverage data to /coverage/.coverage
#   - Coverage can be collected after container stops
#
# When ENABLE_COVERAGE=0 (default):
#   - Behaves identically to the standard entrypoint
#
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

# Ensure coverage directory exists and is writable
if [ "${ENABLE_COVERAGE:-0}" = "1" ]; then
    mkdir -p /coverage
    echo "Coverage enabled, data will be written to ${COVERAGE_FILE:-/coverage/.coverage}"
fi

# Run the flowgraph with or without coverage
if [ "${ENABLE_COVERAGE:-0}" = "1" ]; then
    # Use coverage run to instrument Python execution
    # Note: apt installs as python3-coverage; use python3 -m coverage for flexibility
    # --parallel-mode enables unique data files for parallel runs
    # --source limits coverage to GNU Radio packages
    #
    # Strip 'python3' prefix if present (Docker middleware passes "python3 /script.py")
    if [ "$1" = "python3" ] || [ "$1" = "python" ]; then
        shift
    fi
    exec python3 -m coverage run \
        --rcfile="${COVERAGE_RCFILE:-/etc/coveragerc}" \
        --data-file="${COVERAGE_FILE:-/coverage/.coverage}" \
        "$@"
else
    exec "$@"
fi
