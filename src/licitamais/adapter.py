"""Protocolo do adaptador."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from licitamais.types import (
    Capabilities,
    FetchedPage,
    FetchFailure,
    FetchRequest,
    ParseResult,
    PlanContext,
    ProcessRecord,
    Session,
    SourceConfig,
)


class Adapter(Protocol):
    source_code: str
    adapter_version: str
    capabilities: Capabilities

    def open(self, cfg: SourceConfig) -> Session: ...
    def close(self, s: Session) -> None: ...
    def plan(self, ctx: PlanContext) -> Iterator[FetchRequest]: ...
    def fetch(self, s: Session, req: FetchRequest) -> FetchedPage | FetchFailure: ...
    def parse(self, raw: FetchedPage) -> ParseResult: ...
    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]: ...
