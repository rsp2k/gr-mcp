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
    def to_key(self) -> str:
        ...


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
    device_paths: list[str] = []
    coverage_enabled: bool = False


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
