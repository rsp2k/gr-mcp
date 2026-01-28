# GR-MCP: GNU Radio MCP Server

[![Python Version](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)

**GR-MCP** is a FastMCP server for [GNU Radio](https://www.gnuradio.org/) that enables programmatic, automated, and AI-driven creation and control of GNU Radio flowgraphs. It exposes 36 MCP tools for building, modifying, validating, running, and monitoring `.grc` files.

> **Why GR-MCP?**
> - Build and validate flowgraphs programmatically
> - Run flowgraphs in Docker containers with XML-RPC control
> - Adjust variables in real-time without restarting
> - Collect Python code coverage from containerized flowgraphs
> - Integrate with LLMs, automation frameworks, and custom tools


## Features

### Flowgraph Building (15 tools)
Build, edit, and validate `.grc` files programmatically:
- `get_blocks` / `make_block` / `remove_block` - Block management
- `get_block_params` / `set_block_params` - Parameter control
- `get_block_sources` / `get_block_sinks` - Port inspection
- `get_connections` / `connect_blocks` / `disconnect_blocks` - Wiring
- `validate_block` / `validate_flowgraph` / `get_all_errors` - Validation
- `save_flowgraph` - Save to `.grc` file
- `get_all_available_blocks` - List available block types

### Runtime Control (11 tools)
Run flowgraphs in Docker containers with headless QT rendering:
- `launch_flowgraph` - Start a flowgraph in a container (Xvfb + optional VNC)
- `list_containers` / `stop_flowgraph` / `remove_flowgraph` - Container lifecycle
- `connect` / `connect_to_container` / `disconnect` - XML-RPC connection
- `list_variables` / `get_variable` / `set_variable` - Real-time variable control
- `start` / `stop` / `lock` / `unlock` - Flowgraph execution control
- `capture_screenshot` / `get_container_logs` - Visual feedback
- `get_status` - Connection and container status

### Coverage Collection (4 tools)
Collect Python code coverage from containerized flowgraphs:
- `collect_coverage` - Gather coverage data after flowgraph stops
- `generate_coverage_report` - Generate HTML/XML/JSON reports
- `combine_coverage` - Aggregate coverage across multiple runs
- `delete_coverage` - Clean up coverage data


## Requirements

- Python >= 3.14
- GNU Radio (tested with GRC v3.10.12.0)
- Docker (optional, for runtime control features)
- UV package manager


## Quickstart

### 1. Clone and setup

```bash
git clone https://github.com/rsp2k/gr-mcp
cd gr-mcp

# Create venv with system site-packages (required for gnuradio)
uv venv --system-site-packages --python 3.14
uv sync
```

### 2. Configure your MCP client

Add to Claude Desktop, Cursor, or other MCP client config:

```json
{
  "mcpServers": {
    "gr-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/gr-mcp", "run", "main.py"]
    }
  }
}
```

### 3. (Optional) Build Docker images for runtime control

```bash
# Build the runtime image (Xvfb + VNC + ImageMagick)
docker build -f docker/Dockerfile.gnuradio-runtime -t gnuradio-runtime:latest docker/

# Build the coverage image (adds python3-coverage)
docker build -f docker/Dockerfile.gnuradio-coverage -t gnuradio-coverage:latest docker/
```


## Usage Examples

### Building a flowgraph

```python
# Create a signal generator block
make_block(block_type="analog_sig_source_x", name="sig_source")

# Set parameters
set_block_params(block_name="sig_source", params={
    "freq": "1000",
    "amplitude": "0.5",
    "waveform": "analog.GR_COS_WAVE"
})

# Connect blocks
connect_blocks(
    source_block="sig_source", source_port="0",
    sink_block="audio_sink", sink_port="0"
)

# Validate and save
validate_flowgraph()
save_flowgraph(path="/tmp/my_flowgraph.grc")
```

### Running a flowgraph with runtime control

```python
# Launch in Docker container
launch_flowgraph(
    flowgraph_path="/path/to/flowgraph.py",
    name="my-sdr",
    xmlrpc_port=8080,
    enable_vnc=True  # Optional: VNC on port 5900
)

# Connect and control
connect_to_container(name="my-sdr")
list_variables()  # See available variables
set_variable(name="freq", value=2.4e9)  # Tune in real-time

# Visual feedback
capture_screenshot(name="my-sdr")  # Get QT GUI screenshot
get_container_logs(name="my-sdr")  # Check for errors

# Clean up
stop_flowgraph(name="my-sdr")
remove_flowgraph(name="my-sdr")
```

### Collecting code coverage

```python
# Launch with coverage enabled
launch_flowgraph(
    flowgraph_path="/path/to/flowgraph.py",
    name="coverage-test",
    enable_coverage=True
)

# Run your test scenario...
# Then stop (graceful shutdown required for coverage data)
stop_flowgraph(name="coverage-test")

# Collect and report
collect_coverage(name="coverage-test")
generate_coverage_report(name="coverage-test", format="html")
```


## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
pytest

# Run with coverage
pytest --cov=gnuradio_mcp --cov-report=term-missing

# Pre-commit hooks
pre-commit run --all-files
```


## Architecture

```
main.py                          # FastMCP app entry point
src/gnuradio_mcp/
├── models.py                    # Pydantic models for all tools
├── middlewares/
│   ├── platform.py              # GNU Radio Platform wrapper
│   ├── flowgraph.py             # Flowgraph block/connection management
│   ├── block.py                 # Block parameter/port access
│   ├── docker.py                # Docker container lifecycle
│   └── xmlrpc.py                # XML-RPC variable control
└── providers/
    ├── base.py                  # PlatformProvider (flowgraph tools)
    ├── mcp.py                   # McpPlatformProvider (registers tools)
    ├── runtime.py               # RuntimeProvider (Docker/XML-RPC)
    └── mcp_runtime.py           # McpRuntimeProvider (registers tools)
```


## Project Status

**Active development.** Core flowgraph building is stable. Runtime control (Docker + XML-RPC) is Phase 1 complete. Coverage collection is functional.

Contributions and feedback welcome!
