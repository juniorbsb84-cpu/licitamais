"""Taxonomia fechada de erros e politica de retry."""

from __future__ import annotations

import random
from collections.abc import Mapping
from enum import Enum


class ErrorClass(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    CONTRACT = "contract"
    PARSE_RECORD = "parse_record"
    INTERNAL = "internal"


def _retry_after(headers: Mapping[str, str] | None) -> str | None:
    for chave, valor in dict(headers or {}).items():
        if str(chave).lower() == "retry-after" and str(valor).strip():
            return str(valor).strip()
    return None


def classify_error(
    exc: Exception | None,
    http_status: int | None,
    headers: Mapping[str, str],
) -> ErrorClass:
    """Classifica exatamente uma classe por erro; a classe determina a acao."""
    tem_retry_after = _retry_after(headers) is not None
    if http_status is not None:
        status = int(http_status)
        if status == 429:
            return ErrorClass.RATE_LIMITED
        if status == 403 and tem_retry_after:
            return ErrorClass.RATE_LIMITED
        if status in (401, 403):
            return ErrorClass.AUTH
        if status == 404:
            return ErrorClass.NOT_FOUND
        if 500 <= status <= 599:
            return ErrorClass.TRANSIENT
    if exc is not None:
        if isinstance(exc, OSError):
            # TimeoutError e ConnectionError sao subclasses de OSError:
            # timeout, DNS e conexao resetada sao todos transientes.
            return ErrorClass.TRANSIENT
        if isinstance(exc, ValueError):
            # Decode falha, campo canario ausente: layout quebrou, retry nao conserta.
            return ErrorClass.CONTRACT
        return ErrorClass.INTERNAL
    return ErrorClass.INTERNAL


def _backoff(base_s: float, tentativa: int, teto_s: float) -> float:
    atraso = min(teto_s, base_s * (2**tentativa))
    return atraso + random.uniform(0.0, atraso * 0.25)


def apply_retry_policy(cls: ErrorClass, attempt: int) -> tuple[bool, float]:
    """Retry apenas para transient/rate_limited; contract nunca tem retry."""
    nome = cls.value if isinstance(cls, ErrorClass) else str(cls).lower()
    tentativa = int(attempt)
    if tentativa < 0:
        return False, 0.0
    if nome == ErrorClass.TRANSIENT.value and tentativa < 3:
        return True, _backoff(1.0, tentativa, 30.0)
    if nome == ErrorClass.RATE_LIMITED.value and tentativa < 3:
        return True, _backoff(10.0, tentativa, 600.0)
    return False, 0.0
