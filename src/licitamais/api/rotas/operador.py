from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..deps import Conta, db, operador

rota = APIRouter()


@rota.get("/operador/saude")
def saude(_: Conta = Depends(operador), con: sqlite3.Connection = Depends(db)) -> dict:
    sql_fontes = """
        SELECT s.code AS fonte,
               sr.status,
               sr.started_at AS inicio,
               sr.fetched_count AS respostas,
               sr.new_count AS novos,
               sr.error AS erro
        FROM source s
        LEFT JOIN source_run sr ON sr.id = (
            SELECT id FROM source_run WHERE source_id = s.id ORDER BY id DESC LIMIT 1
        )
        ORDER BY s.code
        LIMIT 50
    """
    fontes_rows = con.execute(sql_fontes).fetchall()
    sql_incidentes = """
        SELECT i.id, s.code AS fonte, i.kind AS tipo, i.severity AS severidade,
               i.opened_at AS aberto_em, i.message AS mensagem
        FROM incident i
        JOIN source s ON s.id = i.source_id
        WHERE i.closed_at IS NULL
        ORDER BY i.opened_at DESC
        LIMIT 50
    """
    incidentes_rows = con.execute(sql_incidentes).fetchall()
    return {
        "fontes": [
            {
                "fonte": r["fonte"],
                "status": r["status"] or "nunca_executado",
                "inicio": r["inicio"],
                "respostas": r["respostas"] or 0,
                "novos": r["novos"] or 0,
                "erro": r["erro"],
            }
            for r in fontes_rows
        ],
        "incidentes": [
            {
                "id": r["id"],
                "fonte": r["fonte"],
                "tipo": r["tipo"],
                "severidade": r["severidade"],
                "aberto_em": r["aberto_em"],
                "mensagem": r["mensagem"],
            }
            for r in incidentes_rows
        ],
    }
