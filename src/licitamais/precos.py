"""Estatistica da regua de preco: quartis de Tukey e mediana por ano."""

from __future__ import annotations

import statistics


def estatistica(valores_por_data: list[tuple[str | None, float]]) -> dict:
    valores = sorted(float(v) for _, v in valores_por_data)
    por_ano: dict[str, list[float]] = {}
    for data, v in valores_por_data:
        texto = str(data or "")
        if len(texto) >= 4 and texto[:4].isdigit():
            por_ano.setdefault(texto[:4], []).append(float(v))
    metade = len(valores) // 2
    q1 = statistics.median(valores[:metade]) if metade else (valores[0] if valores else None)
    q3 = statistics.median(valores[-metade:]) if metade else (valores[0] if valores else None)
    r = lambda x: round(x, 2) if x is not None else None  # noqa: E731
    return {
        "n": len(valores),
        "min": r(valores[0]) if valores else None,
        "q1": r(q1),
        "mediana": r(statistics.median(valores)) if valores else None,
        "q3": r(q3),
        "max": r(valores[-1]) if valores else None,
        "serie_ano": [
            {"ano": a, "n": len(vs), "mediana": r(statistics.median(vs))} for a, vs in sorted(por_ano.items())
        ],
    }
