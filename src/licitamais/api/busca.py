"""Consulta FTS5 segura: todo termo vira string entre aspas; so o ultimo ganha prefixo."""

from __future__ import annotations

import re

_TERMO = re.compile(r"\w+", re.UNICODE)


def consulta_fts(q: str) -> str | None:
    termos = [t.lower() for t in _TERMO.findall(q or "")][:8]
    if not termos:
        return None
    inicio = " ".join(f'"{t}"' for t in termos[:-1])
    fim = f'"{termos[-1]}"*'
    return f"{inicio} {fim}" if inicio else fim
