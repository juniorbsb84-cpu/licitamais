from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from licitamais.evidencia import evidencia

from ..deps import Conta, db
from ..limite_consultas import limitar_consulta
from ..ocultas import fontes_ocultas

rota = APIRouter()


@rota.get("/evidencias")
def evidencias(
    ids: list[int] = Query(..., max_length=50),
    _: Conta | None = Depends(limitar_consulta),
    con: sqlite3.Connection = Depends(db),
) -> list[dict]:
    ocultas = fontes_ocultas()
    visiveis = dict.fromkeys(ids)
    if ocultas:  # id de fonte oculta nao pode vazar valor estimado nem estatistica
        marcas = ",".join("?" * len(ocultas))
        escondidos = {
            r[0]
            for r in con.execute(
                f"SELECT id FROM v_licitacao WHERE id IN ({','.join('?' * len(visiveis))}) AND fonte IN ({marcas})",
                [*visiveis, *ocultas],
            )
        }
        visiveis = [pid for pid in visiveis if pid not in escondidos]
    return [evidencia(con, pid, ocultas) for pid in visiveis]
