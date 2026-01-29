from __future__ import annotations

import logging
import re
from typing import Any

from gnuradio_mcp.models import (
    KNOB_TYPE_NAMES,
    KnobModel,
    KnobPropertiesModel,
    PerfCounterModel,
    ThriftConnectionInfoModel,
    VariableModel,
)

logger = logging.getLogger(__name__)

THRIFT_TIMEOUT = 5
DEFAULT_THRIFT_PORT = 9090

# Performance counter knob suffixes (used to identify perf counters)
PERF_COUNTER_SUFFIXES = [
    "::avg throughput",
    "::avg work time",
    "::total work time",
    "::avg nproduced",
    "::avg input % full",
    "::avg output % full",
    "::var nproduced",
    "::var work time",
]


class ThriftMiddleware:
    """Wraps GNU Radio's ControlPort Thrift client for runtime control.

    ControlPort provides richer functionality than XML-RPC:
    - Native type support (complex numbers, vectors)
    - Performance counters (throughput, timing, buffer utilization)
    - Knob metadata (units, min/max, descriptions)
    - PMT message injection
    - Regex-based knob queries

    Knobs are named using the pattern: block_alias::varname
    (e.g., "sig_source0::frequency")

    Requires ControlPort to be enabled in GNU Radio config:
        [ControlPort]
        on = True
    """

    def __init__(
        self,
        client: Any,  # RPCConnectionThrift
        host: str,
        port: int,
    ):
        self._client = client
        self._host = host
        self._port = port

    @classmethod
    def connect(
        cls,
        host: str = "127.0.0.1",
        port: int = DEFAULT_THRIFT_PORT,
    ) -> ThriftMiddleware:
        """Connect to a GNU Radio ControlPort server.

        Args:
            host: Hostname or IP address
            port: ControlPort Thrift port (default 9090)

        Raises:
            ImportError: If gnuradio.ctrlport is not available
            ConnectionError: If connection fails
        """
        try:
            from gnuradio.ctrlport.GNURadioControlPortClient import (
                GNURadioControlPortClient,
            )
        except ImportError as e:
            raise ImportError(
                "GNU Radio ControlPort not available. "
                "Ensure GNU Radio is installed with Thrift support."
            ) from e

        try:
            radio = GNURadioControlPortClient(host=host, port=port)
            logger.info("Connected to ControlPort at %s:%d", host, port)
            return cls(radio.client, host, port)
        except Exception as e:
            raise ConnectionError(
                f"Failed to connect to ControlPort at {host}:{port}: {e}"
            ) from e

    def get_connection_info(
        self, container_name: str | None = None
    ) -> ThriftConnectionInfoModel:
        """Return connection metadata including knob count."""
        try:
            knobs = self._client.getKnobs([])
            knob_count = len(knobs)
        except Exception:
            knob_count = 0

        return ThriftConnectionInfoModel(
            host=self._host,
            port=self._port,
            container_name=container_name,
            protocol="thrift",
            knob_count=knob_count,
        )

    # ──────────────────────────────────────────
    # Variable Operations (XML-RPC compatible API)
    # ──────────────────────────────────────────

    def list_variables(self) -> list[VariableModel]:
        """List all ControlPort knobs as variables.

        Filters out performance counters to match XML-RPC behavior.
        """
        knobs = self._client.getKnobs([])
        variables = []
        for name, knob in knobs.items():
            # Skip performance counters
            if self._is_perf_counter(name):
                continue
            variables.append(VariableModel(name=name, value=knob.value))
        return variables

    def get_variable(self, name: str) -> Any:
        """Get a variable value by name."""
        knobs = self._client.getKnobs([name])
        if name not in knobs:
            raise KeyError(f"Knob not found: {name}")
        return knobs[name].value

    def set_variable(self, name: str, value: Any) -> bool:
        """Set a variable value.

        The knob type is inferred from the existing knob's type.
        """
        # Get current knob to determine type
        knobs = self._client.getKnobs([name])
        if name not in knobs:
            raise KeyError(f"Knob not found: {name}")

        current = knobs[name]
        # Create new knob with same type but new value
        from gnuradio.ctrlport.RPCConnectionThrift import RPCConnectionThrift

        new_knob = RPCConnectionThrift.Knob(name, value, current.ktype)
        self._client.setKnobs({name: new_knob})
        return True

    # ──────────────────────────────────────────
    # ControlPort-Specific Operations
    # ──────────────────────────────────────────

    def get_knobs(self, pattern: str = "") -> list[KnobModel]:
        """Get knobs, optionally filtered by regex pattern.

        Args:
            pattern: Regex pattern for filtering knob names.
                     Empty string returns all knobs.

        Examples:
            get_knobs("")  # All knobs
            get_knobs(".*frequency.*")  # All frequency-related knobs
            get_knobs("sig_source0::.*")  # All knobs for sig_source0
        """
        if pattern:
            knobs = self._client.getRe([pattern])
        else:
            knobs = self._client.getKnobs([])

        result = []
        for name, knob in knobs.items():
            knob_type = KNOB_TYPE_NAMES.get(knob.ktype, f"UNKNOWN({knob.ktype})")
            result.append(
                KnobModel(
                    name=name,
                    value=knob.value,
                    knob_type=knob_type,
                )
            )
        return result

    def set_knobs(self, knobs: dict[str, Any]) -> bool:
        """Set multiple knobs atomically.

        Args:
            knobs: Dict mapping knob names to new values.
                   Types are inferred from existing knobs.
        """
        if not knobs:
            return True

        # Get current knobs to determine types
        current_knobs = self._client.getKnobs(list(knobs.keys()))

        from gnuradio.ctrlport.RPCConnectionThrift import RPCConnectionThrift

        to_set = {}
        for name, value in knobs.items():
            if name not in current_knobs:
                raise KeyError(f"Knob not found: {name}")
            current = current_knobs[name]
            to_set[name] = RPCConnectionThrift.Knob(name, value, current.ktype)

        self._client.setKnobs(to_set)
        return True

    def get_knob_properties(self, names: list[str]) -> list[KnobPropertiesModel]:
        """Get metadata (units, min/max, description) for specified knobs.

        Args:
            names: List of knob names to query.
        """
        if not names:
            # Get all properties
            props = self._client.properties([])
        else:
            props = self._client.properties(names)

        result = []
        for name, prop in props.items():
            knob_type = KNOB_TYPE_NAMES.get(prop.type, f"UNKNOWN({prop.type})")
            result.append(
                KnobPropertiesModel(
                    name=name,
                    description=prop.description or "",
                    units=prop.units if hasattr(prop, "units") else None,
                    min_value=prop.min.value if prop.min else None,
                    max_value=prop.max.value if prop.max else None,
                    default_value=(
                        prop.defaultvalue.value if prop.defaultvalue else None
                    ),
                    knob_type=knob_type,
                )
            )
        return result

    def get_performance_counters(
        self, block: str | None = None
    ) -> list[PerfCounterModel]:
        """Get performance metrics for blocks.

        Args:
            block: Optional block alias to filter (e.g., "sig_source0").
                   If None, returns metrics for all blocks.

        Returns:
            List of PerfCounterModel with throughput, timing, and buffer stats.
        """
        # Get all performance counter knobs
        if block:
            pattern = f"^{re.escape(block)}::.*"
        else:
            pattern = ""

        all_knobs = self.get_knobs(pattern)

        # Group by block
        blocks: dict[str, dict[str, Any]] = {}
        for knob in all_knobs:
            if not self._is_perf_counter(knob.name):
                continue

            # Parse block name and metric
            parts = knob.name.split("::", 1)
            if len(parts) != 2:
                continue

            block_name, metric = parts
            if block_name not in blocks:
                blocks[block_name] = {}
            blocks[block_name][metric] = knob.value

        # Build PerfCounterModel for each block
        result = []
        for block_name, metrics in blocks.items():
            result.append(
                PerfCounterModel(
                    block_name=block_name,
                    avg_throughput=metrics.get("avg throughput", 0.0),
                    avg_work_time_us=metrics.get("avg work time", 0.0),
                    total_work_time_us=metrics.get("total work time", 0.0),
                    avg_nproduced=metrics.get("avg nproduced", 0.0),
                    input_buffer_pct=self._to_list(metrics.get("avg input % full", [])),
                    output_buffer_pct=self._to_list(
                        metrics.get("avg output % full", [])
                    ),
                )
            )
        return result

    def post_message(self, block: str, port: str, message: Any) -> bool:
        """Send a PMT message to a block's message port.

        Args:
            block: Block alias (e.g., "msg_sink0")
            port: Message port name (e.g., "in")
            message: PMT message to send

        Note:
            The message should be a PMT object. For simple cases,
            use pmt.intern("string") or pmt.to_pmt(dict).
        """
        import pmt

        # Ensure message is a PMT
        if not pmt.is_pmt(message):
            message = pmt.to_pmt(message)

        self._client.postMessage(block, port, message)
        return True

    def close(self) -> None:
        """Close the Thrift connection."""
        try:
            if self._client is not None:
                # The client handles cleanup in __del__
                self._client = None
        except Exception:
            pass

    # ──────────────────────────────────────────
    # Private Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _is_perf_counter(name: str) -> bool:
        """Check if a knob name is a performance counter."""
        return any(name.endswith(suffix) for suffix in PERF_COUNTER_SUFFIXES)

    @staticmethod
    def _to_list(value: Any) -> list[float]:
        """Convert a value to a list of floats."""
        if isinstance(value, (list, tuple)):
            return [float(v) for v in value]
        elif value is None:
            return []
        else:
            return [float(value)]
