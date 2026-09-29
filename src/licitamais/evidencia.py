"""Evidência do histórico para uma licitação: compras com termos parecidos e o que se pagou nelas.

"Parecida" = mesmo conjunto das 3 palavras mais longas do objeto (FTS, AND); se não achar nada,
tenta com as 2 mais longas. É aproximação por texto, não casamento semântico.
"""

from __future__ import annotations

import re
import sqlite3
from statistics import median

_PALAVRA = re.compile(r"[^\W\d_]{5,}", re.UNICODE)
_VAZIAS = {
    "contratacao",
    "contratação",
    "empresa",
    "empresas",
    "prestacao",
    "prestação",
    "servico",
    "serviço",
    "servicos",
    "serviços",
    "aquisicao",
    "aquisição",
    "fornecimento",
    "especializada",
    "referente",
    "atender",
    "necessidades",
    "unidade",
    "unidades",
    "conforme",
    "demanda",
    "demais",
    "através",
    "atraves",
    "objeto",
    "registro",
    "precos",
    "preços",
    "eventual",
    "futura",
    "sesc",
    "senac",
    "senai",
    "sesi",
    "senat",
    "departamento",
    "regional",
    "nacional",
    "brasilia",
    "brasília",
    "executivo",
    "sobre",
    "entre",
    "durante",
    "incluindo",
    "materiais",
    "material",
    "pessoa",
    "juridica",
    "jurídica",
    "periodo",
    "período",
    "meses",
    "visando",
    "destinados",
    "destinada",
    "especificacoes",
    "especificações",
    "quantitativos",
    "especializado",
    "especializados",
    "administrativo",
    "operacional",
    "capacidade",
    "realizacao",
    "realização",
    "diversos",
    "diversas",
}


def termos_chave(objeto: str | None, n: int) -> list[str]:
    vistos: list[str] = []
    for p in _PALAVRA.findall((objeto or "").lower()):
        if p not in _VAZIAS and p not in vistos:
            vistos.append(p)
    return sorted(vistos, key=len, reverse=True)[:n]


def _parecidos(con: sqlite3.Connection, pid: int, termos: list[str]) -> list[int]:
    fts = " ".join(f'"{t}"' for t in termos)
    return [
        r[0]
        for r in con.execute(
            "SELECT rowid FROM process_fts WHERE process_fts MATCH ? AND rowid != ? LIMIT 2000", (fts, pid)
        )
    ]


def evidencia(con: sqlite3.Connection, pid: int, ocultas: list[str] | tuple[str, ...] = ()) -> dict:
    lic = con.execute("SELECT objeto FROM v_licitacao WHERE id = ?", (pid,)).fetchone()
    est = con.execute(
        "SELECT SUM(total_price_estimated_cents) FROM item WHERE process_id = ? AND deleted_at IS NULL", (pid,)
    ).fetchone()[0]
    saida = {
        "id": pid,
        "valor_estimado": est / 100 if est else None,
        "parecidas": 0,
        "desde": None,
        "valor_mediano": None,
        "desconto_medio": None,
        "mais_venceu": None,
    }
    if lic is None:
        return saida
    ids: list[int] = []
    for n in (3, 2):
        termos = termos_chave(lic[0], n)
        if len(termos) < n:
            continue
        ids = _parecidos(con, pid, termos)
        if ids:
            break
    if ids and ocultas:
        ids = [
            r[0]
            for r in con.execute(
                f"SELECT id FROM v_licitacao WHERE id IN ({','.join('?' * len(ids))})"
                f" AND fonte NOT IN ({','.join('?' * len(ocultas))})",
                [*ids, *ocultas],
            )
        ]
    if not ids:
        return saida
    marca = ",".join("?" * len(ids))
    contratos = con.execute(
        f"SELECT v.valor, COALESCE(v.assinado_em, v.vigencia_inicio) AS data, v.fornecedor FROM v_preco_contrato v "
        f"JOIN contract c ON c.id = v.contract_id WHERE c.process_id IN ({marca}) AND v.valor > 0",
        ids,
    ).fetchall()
    vencedores = con.execute(
        f"SELECT i.valor_total, COALESCE(r.decided_at_source, a.first_seen_at), i.fornecedor "
        f"FROM v_preco_item i JOIN award a ON a.id = i.award_id LEFT JOIN result r ON r.id = a.result_id "
        f"WHERE a.process_id IN ({marca}) AND i.valor_total > 0",
        ids,
    ).fetchall()
    compras = list(contratos) + list(vencedores)
    if not compras:
        return saida
    anos = [str(d)[:4] for _, d, _ in compras if d and str(d)[:4].isdigit()]
    ranking: dict[str, int] = {}
    for _, _, f in compras:
        if f:
            ranking[f] = ranking.get(f, 0) + 1
    topo = max(ranking.items(), key=lambda x: x[1]) if ranking else None
    descontos = [
        r[0]
        for r in con.execute(
            f"SELECT d.desconto_vs_media FROM v_desconto_disputa d JOIN award a ON a.id = d.award_id "
            f"WHERE a.process_id IN ({marca}) AND d.desconto_vs_media IS NOT NULL",
            ids,
        )
    ]
    saida.update(
        parecidas=len(compras),
        desde=int(min(anos)) if anos else None,
        valor_mediano=round(median(v for v, _, _ in compras), 2),
        desconto_medio=round(sum(descontos) / len(descontos), 4) if descontos else None,
        mais_venceu=topo[0] if topo and topo[1] > 1 else None,
    )
    return saida
