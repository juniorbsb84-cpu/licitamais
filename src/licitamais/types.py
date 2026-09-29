"""Tipos imutaveis da fronteira entre adaptadores e nucleo."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


def _congelar(valor: object) -> object:
    if isinstance(valor, Mapping):
        return MappingProxyType({str(chave): _congelar(item) for chave, item in valor.items()})
    if isinstance(valor, list | tuple):
        return tuple(_congelar(item) for item in valor)
    if isinstance(valor, set | frozenset):
        return frozenset(_congelar(item) for item in valor)
    if isinstance(valor, bytearray):
        return bytes(valor)
    return valor


def _congelar_mapping(valor: Mapping[str, object]) -> Mapping[str, object]:
    congelado = _congelar(valor)
    assert isinstance(congelado, Mapping)
    return congelado


def _chave_canonica(
    *,
    endpoint: str,
    method: str,
    params: Mapping[str, str],
    body: bytes | None,
    headers_extra: Mapping[str, str],
    phase: str,
    entity_hint: str | None,
    parent_native_id: str | None,
) -> str:
    identidade = {
        "body_hex": None if body is None else body.hex(),
        "endpoint": endpoint,
        "entity_hint": entity_hint,
        "headers_extra": sorted((nome.lower(), valor) for nome, valor in headers_extra.items()),
        "method": method.upper(),
        "params": sorted(params.items()),
        "parent_native_id": parent_native_id,
        "phase": phase,
    }
    serializado = json.dumps(
        identidade,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serializado).hexdigest()


@dataclass(frozen=True)
class SourceConfig:
    source_code: str
    base_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_code, str) or not self.source_code.strip():
            raise ValueError("source_code nao pode ser vazio")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url nao pode ser vazio")


@dataclass(frozen=True)
class Session:
    source_code: str = ""
    dados: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dados", _congelar_mapping(self.dados))


@dataclass(frozen=True)
class Capabilities:
    strategy: str
    entities: tuple[str, ...]
    hydration: Mapping[str, object]
    listing_order_stable: bool
    supports_conditional_get: bool
    deletion_semantics: str
    rate: Mapping[str, object]
    auth: str
    probes: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entities", tuple(self.entities))
        object.__setattr__(self, "hydration", _congelar_mapping(self.hydration))
        object.__setattr__(self, "rate", _congelar_mapping(self.rate))
        object.__setattr__(self, "probes", _congelar_mapping(self.probes))


@dataclass(frozen=True)
class FetchRequest:
    endpoint: str
    method: str
    params: Mapping[str, str]
    body: bytes | None
    headers_extra: Mapping[str, str]
    phase: str
    entity_hint: str | None
    parent_native_id: str | None
    cost_weight: int
    cursor_out: Mapping[str, str] | None
    key: str = field(init=False)

    def __post_init__(self) -> None:
        if self.body is not None and not isinstance(self.body, bytes):
            raise ValueError("body precisa ser bytes ou None")

        params = MappingProxyType(dict(self.params))
        headers_extra = MappingProxyType(dict(self.headers_extra))
        cursor_out = None if self.cursor_out is None else MappingProxyType(dict(self.cursor_out))

        object.__setattr__(self, "params", params)
        object.__setattr__(self, "headers_extra", headers_extra)
        object.__setattr__(self, "cursor_out", cursor_out)
        object.__setattr__(
            self,
            "key",
            _chave_canonica(
                endpoint=self.endpoint,
                method=self.method,
                params=params,
                body=self.body,
                headers_extra=headers_extra,
                phase=self.phase,
                entity_hint=self.entity_hint,
                parent_native_id=self.parent_native_id,
            ),
        )


@dataclass(frozen=True)
class FetchedPage:
    request: FetchRequest
    status: int
    headers: Mapping[str, str]
    body: bytes
    fetched_at: str
    duration_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, int) or not 200 <= self.status < 300:
            raise ValueError("status de FetchedPage precisa estar entre 200 e 299")
        if not isinstance(self.body, bytes):
            raise ValueError("body precisa ser bytes")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True)
class FetchFailure:
    request: FetchRequest
    error: str
    fetched_at: str
    duration_ms: int
    status: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.error, str) or not self.error.strip():
            raise ValueError("error nao pode ser vazio")


@dataclass(frozen=True)
class OrgRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class ProcessRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class ItemRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class AttachmentRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class ResultRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class AwardRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class ContractRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class PhaseRecord:
    source_native_id: str
    attrs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", _congelar_mapping(self.attrs))


@dataclass(frozen=True)
class QuarantineItem:
    raw_excerpt: str
    reason: str
    pointer: str | None = None


@dataclass(frozen=True)
class NormalizedBatch:
    orgs: tuple[OrgRecord, ...]
    processes: tuple[ProcessRecord, ...]
    items: tuple[ItemRecord, ...]
    attachments: tuple[AttachmentRecord, ...]
    results: tuple[ResultRecord, ...]
    awards: tuple[AwardRecord, ...]
    contracts: tuple[ContractRecord, ...]
    phases: tuple[PhaseRecord, ...]

    def __post_init__(self) -> None:
        for nome in (
            "orgs",
            "processes",
            "items",
            "attachments",
            "results",
            "awards",
            "contracts",
            "phases",
        ):
            object.__setattr__(self, nome, tuple(getattr(self, nome)))


@dataclass(frozen=True)
class ParseResult:
    batch: NormalizedBatch
    quarantine: tuple[QuarantineItem, ...]
    next: tuple[FetchRequest, ...]
    cursor_out: Mapping[str, str] | None
    signals: Mapping[str, object]
    fatal: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quarantine", tuple(self.quarantine))
        object.__setattr__(self, "next", tuple(self.next))
        object.__setattr__(
            self,
            "cursor_out",
            None if self.cursor_out is None else MappingProxyType(dict(self.cursor_out)),
        )
        object.__setattr__(self, "signals", _congelar_mapping(self.signals))


@dataclass(frozen=True)
class PlanContext:
    cursors: Mapping[str, str]
    seen_request_keys: frozenset[str]
    parsed_so_far: int
    source_id: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursors", MappingProxyType(dict(self.cursors)))
        object.__setattr__(
            self,
            "seen_request_keys",
            frozenset(self.seen_request_keys),
        )
