from __future__ import annotations

from typing import Any, Literal, Protocol, Type, get_args

from gnuradio.grc.core.blocks.block import Block
from gnuradio.grc.core.Connection import Connection
from gnuradio.grc.core.params.param import Param
from gnuradio.grc.core.ports.port import Port
from pydantic import BaseModel, field_validator


class BlockTypeModel(BaseModel):
    label: str
    key: str

    @classmethod
    def from_block_type(cls, block: Type[Block]) -> BlockTypeModel:
        return cls(label=block.label, key=block.key)


class KeyedModel(Protocol):
    def to_key(self) -> str: ...


class BlockModel(BaseModel):
    label: str
    name: str

    @classmethod
    def from_block(cls, block: Block) -> BlockModel:
        return cls(label=block.label, name=block.name)

    def to_key(self) -> str:
        return f"{self.label}:{self.name}"


class ParamModel(BaseModel):
    parent: str
    key: str
    name: str
    dtype: str
    value: Any

    @classmethod
    def from_param(cls, param: Param) -> ParamModel:
        return cls(
            parent=param.parent.name,
            key=param.key,
            name=param.name,
            dtype=param.dtype,
            value=param.get_value(),
        )

    def to_key(self) -> str:
        return f"{self.parent}:{self.key}"


DirectionType = Literal["sink", "source"]
SINK, SOURCE = get_args(DirectionType)


class PortModel(BaseModel):
    parent: str
    key: str
    name: str
    dtype: str
    direction: DirectionType
    optional: bool = False
    hidden: bool = False

    @classmethod
    def from_port(
        cls,
        port: Port,
        direction: DirectionType | None = None,
    ) -> PortModel:
        direction = direction or port._dir
        return cls(
            parent=port.parent.name,
            key=port.key,
            name=port.name,
            dtype=port.dtype,
            direction=direction,
            optional=port.optional,
            hidden=port.hidden,
        )

    def to_key(self) -> str:
        return f"{self.parent}:{self.direction}[{self.key}]"


class ConnectionModel(BaseModel):
    source: PortModel
    sink: PortModel

    @classmethod
    def from_connection(cls, connection: Connection) -> "ConnectionModel":
        return cls(
            source=PortModel.from_port(connection.source_port),
            sink=PortModel.from_port(connection.sink_port),
        )

    def to_key(self) -> str:
        return f"{self.source.to_key()}-{self.sink.to_key()}"


class ErrorModel(BaseModel):
    type: str
    key: str
    message: str

    @field_validator("key", mode="before")
    @classmethod
    def transform_key(cls, v: KeyedModel) -> str:
        return v.to_key()


# ──────────────────────────────────────────────
# Runtime Models (Phase 1: Docker + XML-RPC)
# ──────────────────────────────────────────────


class ContainerModel(BaseModel):
    name: str
    container_id: str
    status: str
    flowgraph_path: str
    xmlrpc_port: int
    vnc_port: int | None = None
    controlport_port: int | None = None  # Phase 2: Thrift ControlPort
    device_paths: list[str] = []
    coverage_enabled: bool = False
    controlport_enabled: bool = False  # Phase 2: Thrift ControlPort


class VariableModel(BaseModel):
    name: str
    value: Any


class ConnectionInfoModel(BaseModel):
    url: str
    container_name: str | None = None
    xmlrpc_port: int
    methods: list[str] = []


class ScreenshotModel(BaseModel):
    container_name: str
    image_base64: str
    format: str = "png"
    width: int | None = None
    height: int | None = None


class RuntimeStatusModel(BaseModel):
    connected: bool
    connection: ConnectionInfoModel | None = None
    containers: list[ContainerModel] = []


# ──────────────────────────────────────────────
# ControlPort/Thrift Models (Phase 2)
# ──────────────────────────────────────────────


# Knob types from GNU Radio's ControlPort Thrift API
# Maps to gnuradio.ctrlport.GNURadio.ttypes.BaseTypes
KNOB_TYPE_NAMES = {
    0: "BOOL",
    1: "BYTE",
    2: "SHORT",
    3: "INT",
    4: "LONG",
    5: "DOUBLE",
    6: "STRING",
    7: "COMPLEX",
    8: "F32VECTOR",
    9: "F64VECTOR",
    10: "S64VECTOR",
    11: "S32VECTOR",
    12: "S16VECTOR",
    13: "S8VECTOR",
    14: "C32VECTOR",
}


class KnobModel(BaseModel):
    """ControlPort knob with type information.

    Knobs are named using the pattern: block_alias::varname
    (e.g., "sig_source0::frequency")
    """

    name: str
    value: Any
    knob_type: str  # BOOL, INT, DOUBLE, COMPLEX, F32VECTOR, etc.


class KnobPropertiesModel(BaseModel):
    """Rich metadata for a ControlPort knob.

    Includes units, min/max bounds, and description from the
    block's property registration.
    """

    name: str
    description: str
    units: str | None = None
    min_value: Any | None = None
    max_value: Any | None = None
    default_value: Any | None = None
    knob_type: str | None = None


class PerfCounterModel(BaseModel):
    """Block performance metrics from ControlPort.

    These are automatically exposed when [PerfCounters] on = True
    in the GNU Radio config. Performance counters use the naming
    pattern: block_alias::metric_name
    """

    block_name: str
    avg_throughput: float  # samples/sec (avg nproduced * sample rate)
    avg_work_time_us: float  # microseconds per work() call
    total_work_time_us: float  # cumulative time in work()
    avg_nproduced: float  # average samples produced per work() call
    input_buffer_pct: list[float] = []  # buffer fullness per input port
    output_buffer_pct: list[float] = []  # buffer fullness per output port


class ThriftConnectionInfoModel(BaseModel):
    """Connection information for ControlPort/Thrift."""

    host: str
    port: int
    container_name: str | None = None
    protocol: str = "thrift"
    knob_count: int = 0


# ──────────────────────────────────────────────
# Coverage Models (Cross-Process Code Coverage)
# ──────────────────────────────────────────────


class CoverageDataModel(BaseModel):
    """Summary of collected coverage data."""

    container_name: str
    coverage_file: str
    summary: str
    lines_covered: int | None = None
    lines_total: int | None = None
    coverage_percent: float | None = None


class CoverageReportModel(BaseModel):
    """Generated coverage report (HTML, XML, JSON)."""

    container_name: str
    format: Literal["html", "xml", "json"]
    report_path: str


# ──────────────────────────────────────────────
# Platform / Design-Time Models (Phase 3: Gap Fills)
# ──────────────────────────────────────────────


class BlockTypeDetailModel(BaseModel):
    """Extended block type info with category for search/browsing."""

    label: str
    key: str
    category: list[str] = []
    documentation: str = ""
    flags: list[str] = []
    deprecated: bool = False

    @classmethod
    def from_block_type(cls, block: Type[Block]) -> BlockTypeDetailModel:
        flags = []
        if hasattr(block, "flags") and hasattr(block.flags, "data"):
            flags = sorted(block.flags.data)
        doc = ""
        if hasattr(block, "documentation") and isinstance(block.documentation, dict):
            doc = block.documentation.get("", "")
        deprecated = False
        if hasattr(block, "is_deprecated") and callable(block.is_deprecated):
            try:
                # is_deprecated() requires an instance; check category fallback
                deprecated = any(
                    "deprecated" in c.lower()
                    for c in (block.category or [])
                )
            except Exception:
                pass
        return cls(
            label=block.label,
            key=block.key,
            category=list(block.category) if block.category else [],
            documentation=doc,
            flags=flags,
            deprecated=deprecated,
        )


class GeneratedFileModel(BaseModel):
    """A single generated file."""

    filename: str
    content: str
    is_main: bool = False


class GeneratedCodeModel(BaseModel):
    """Generated code from a flowgraph.

    Unlike grcc, code generation does NOT block on validation errors.
    The ``is_valid`` and ``warnings`` fields report validation state
    without gating generation.
    """

    files: list[GeneratedFileModel]
    generate_options: str
    flowgraph_id: str
    output_dir: str = ""
    is_valid: bool = True
    warnings: list[ErrorModel] = []


class FlowgraphOptionsModel(BaseModel):
    """Flowgraph-level options from the 'options' block."""

    id: str
    title: str = ""
    author: str = ""
    description: str = ""
    generate_options: str = ""
    run_options: str = ""
    output_language: str = ""
    catch_exceptions: str = ""
    all_params: dict[str, Any] = {}


class EmbeddedBlockIOModel(BaseModel):
    """I/O signature extracted from embedded Python block source."""

    name: str
    cls: str
    params: list[tuple[str, str]]
    sinks: list[tuple[str, str, int]]
    sources: list[tuple[str, str, int]]
    doc: str = ""
    callbacks: list[str] = []
