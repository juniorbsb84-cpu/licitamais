from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from licitamais.situacao import hoje_brasil

from ..deps import Conta, db, leitor
from ..ocultas import sem_ocultas

rota = APIRouter()


@rota.get("/resumo")
def resumo(_: Conta | None = Depends(leitor), con: sqlite3.Connection = Depends(db)) -> dict:
    oculta, args = sem_ocultas("fonte")
    linha = con.execute(
        f"""
        SELECT SUM(situacao = 'aberta'),
               SUM(situacao = 'aberta' AND substr(abertura, 1, 10) BETWEEN date('now', '-3 hours') AND date('now', '-3 hours', '+7 day')),
               COUNT(*), COUNT(DISTINCT fonte)
        FROM v_licitacao WHERE {oculta}""",
        args,
    ).fetchone()
    ultima = con.execute(
        "SELECT MAX(finished_at) FROM source_run WHERE status IN ('ok', 'ok_zero', 'partial')"
    ).fetchone()[0]
    hoje = date.fromisoformat(hoje_brasil())
    por_dia = dict(
        con.execute(
            "SELECT substr(abertura, 1, 10), COUNT(*) FROM v_licitacao WHERE situacao = 'aberta'"
            f" AND substr(abertura, 1, 10) BETWEEN ? AND ? AND {oculta} GROUP BY 1",
            (hoje.isoformat(), (hoje + timedelta(days=13)).isoformat(), *args),
        ).fetchall()
    )
    prazos = [
        {"data": d, "qtd": por_dia.get(d, 0)} for d in ((hoje + timedelta(days=i)).isoformat() for i in range(14))
    ]
    return {
        "abertas": linha[0] or 0,
        "fechando_7_dias": linha[1] or 0,
        "total": linha[2],
        "fontes": linha[3],
        "ultima_coleta": ultima,
        "prazos": prazos,
    }
