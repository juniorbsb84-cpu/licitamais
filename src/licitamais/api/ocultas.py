"""Fontes fora do site (LICITAMAIS_FONTES_OCULTAS), ex.: IGES, cujos termos vedam uso comercial."""

from __future__ import annotations

import os


def fontes_ocultas() -> list[str]:
    return [f.strip() for f in os.environ.get("LICITAMAIS_FONTES_OCULTAS", "").split(",") if f.strip()]


def sem_ocultas(coluna: str) -> tuple[str, list[str]]:
    """Cláusula SQL que exclui as fontes ocultas da coluna dada ("1=1" quando não há nenhuma)."""
    ocultas = fontes_ocultas()
    if not ocultas:
        return "1=1", []
    return f"{coluna} NOT IN ({','.join('?' * len(ocultas))})", ocultas
