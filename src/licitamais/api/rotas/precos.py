from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query

from licitamais.precos import estatistica

from ..busca import consulta_fts
from ..deps import Conta, db
from ..limite_consultas import limitar_consulta
from ..ocultas import sem_ocultas

rota = APIRouter()
_CONTRATO = """
    SELECT COALESCE(v.assinado_em, v.vigencia_inicio) AS data, v.valor, v.fornecedor,
           v.fornecedor_cnpj AS cnpj, v.fonte, c.process_id AS processo_id
    FROM v_preco_contrato v JOIN contract c ON c.id = v.contract_id
    WHERE c.process_id IN (SELECT rowid FROM process_fts WHERE process_fts MATCH ?) AND v.valor > 0 AND {oculta}
    ORDER BY data DESC"""
_ITEM = """
    SELECT COALESCE(r.decided_at_source, a.first_seen_at) AS data, i.valor_total AS valor, i.fornecedor,
           o.cnpj, i.fonte, a.process_id AS processo_id
    FROM v_preco_item i JOIN award a ON a.id = i.award_id
    LEFT JOIN result r ON r.id = a.result_id LEFT JOIN organization o ON o.id = a.supplier_org_id
    WHERE a.process_id IN (SELECT rowid FROM process_fts WHERE process_fts MATCH ?) AND i.valor_total > 0 AND {oculta}
    ORDER BY data DESC"""


@rota.get("/precos")
def precos(
    q: str = Query(..., min_length=1, max_length=100),
    tipo: Literal["contrato", "item"] = "contrato",
    _: Conta | None = Depends(limitar_consulta),
    con: sqlite3.Connection = Depends(db),
) -> dict:
    fts = consulta_fts(q)
    oculta, args = sem_ocultas("v.fonte" if tipo == "contrato" else "i.fonte")
    sql = (_CONTRATO if tipo == "contrato" else _ITEM).format(oculta=oculta)
    linhas = [dict(r) for r in con.execute(sql, (fts, *args))] if fts else []
    ranking: dict[tuple, int] = {}
    for linha in linhas:
        chave = (linha["fornecedor"] or "Não informado", linha["cnpj"])
        ranking[chave] = ranking.get(chave, 0) + 1
    return {
        **estatistica([(linha["data"], linha["valor"]) for linha in linhas]),
        "tipo": tipo,
        "pontos": linhas[:500],
        "fornecedores": [
            {"fornecedor": f, "cnpj": c, "vitorias": n}
            for (f, c), n in sorted(ranking.items(), key=lambda x: -x[1])[:10]
        ],
    }
