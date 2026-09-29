"""Tipos e funcoes canonicas da camada de persistencia."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureRecord:
    source_id: int
    source_run_id: int
    endpoint: str
    url: str
    sha256: str
    body: bytes
    http_status: int
    content_type: str
    captured_at: str
