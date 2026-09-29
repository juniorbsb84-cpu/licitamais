from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from licitamais.situacao import hoje_brasil

from ..busca import consulta_fts
from ..deps import Conta, db
from ..esquemas import LicitacaoResumo, PaginaLicitacoes, Situacao
from ..limite_consultas import limitar_consulta
from ..ocultas import sem_ocultas

rota = APIRouter()
_ORDEM_SITUACAO = (
    "CASE v.situacao WHEN 'aberta' THEN 0 WHEN 'andamento' THEN 1 WHEN 'suspensa' THEN 2 "
    "WHEN 'encerrada' THEN 3 WHEN 'cancelada' THEN 4 ELSE 5 END"
)
_DATA = r"^\d{4}-\d{2}-\d{2}$"


def _where(fts, situacao, fonte, modalidade, de, ate, sem: str | None = None):
    oculta, args = sem_ocultas("v.fonte")
    partes = [oculta]
    if fts:
        partes.append("v.id IN (SELECT rowid FROM process_fts WHERE process_fts MATCH ?)")
        args.append(fts)
    if situacao and sem != "situacao":
        partes.append(f"v.situacao IN ({','.join('?' * len(situacao))})")
        args += situacao
    if fonte and sem != "fonte":
        partes.append(f"v.fonte IN ({','.join('?' * len(fonte))})")
        args += fonte
    if modalidade and sem != "modalidade":
        partes.append("v.modalidade = ?")
        args.append(modalidade)
    if de:
        partes.append("substr(v.abertura, 1, 10) >= ?")
        args.append(de)
    if ate:
        partes.append("substr(v.abertura, 1, 10) <= ?")
        args.append(ate)
    return " AND ".join(partes), args


def _dias(abertura: str | None) -> int | None:
    try:
        return (date.fromisoformat(str(abertura)[:10]) - date.fromisoformat(hoje_brasil())).days if abertura else None
    except ValueError:
        return None


@rota.get("/licitacoes", response_model=PaginaLicitacoes)
def listar(
    q: str = Query("", max_length=100),
    situacao: list[Situacao] = Query([], max_length=10),
    fonte: list[str] = Query([], max_length=10),
    modalidade: str | None = Query(None, max_length=100),
    abertura_de: str | None = Query(None, pattern=_DATA),
    abertura_ate: str | None = Query(None, pattern=_DATA),
    ordem: Literal["relevancia", "prazo", "abertura"] | None = None,
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(20, ge=1, le=50),
    _: Conta | None = Depends(limitar_consulta),
    con: sqlite3.Connection = Depends(db),
) -> PaginaLicitacoes:
    fts = consulta_fts(q)
    ordem = ordem or ("relevancia" if fts else "prazo")
    # abertas por data mais proxima; data NULL sobe no ASC do SQLite, entao vai explicitamente para o fim
    prazo = (
        f"{_ORDEM_SITUACAO}, v.abertura IS NULL, "
        "CASE WHEN v.situacao = 'aberta' THEN v.abertura END ASC, v.abertura DESC"
    )
    abertura = "v.abertura DESC"
    where, args = _where(fts, situacao, fonte, modalidade, abertura_de, abertura_ate)
    total = con.execute(f"SELECT COUNT(*) FROM v_licitacao v WHERE {where}", args).fetchone()[0]
    if ordem == "relevancia" and fts:
        sql = (
            f"SELECT v.* FROM v_licitacao v JOIN process_fts ON process_fts.rowid = v.id AND process_fts MATCH ? "
            f"WHERE {where} ORDER BY {_ORDEM_SITUACAO}, bm25(process_fts) LIMIT ? OFFSET ?"
        )
        linhas = con.execute(sql, [fts, *args, por_pagina, (pagina - 1) * por_pagina]).fetchall()
    else:
        ordenar = prazo if ordem == "prazo" else abertura
        sql = f"SELECT v.* FROM v_licitacao v WHERE {where} ORDER BY {ordenar} LIMIT ? OFFSET ?"
        linhas = con.execute(sql, [*args, por_pagina, (pagina - 1) * por_pagina]).fetchall()
    facetas = {}
    for campo in ("situacao", "fonte", "modalidade"):
        w, a = _where(fts, situacao, fonte, modalidade, abertura_de, abertura_ate, sem=campo)
        facetas[campo] = {
            r[0]: r[1]
            for r in con.execute(
                f"SELECT v.{campo}, COUNT(*) FROM v_licitacao v WHERE {w} AND v.{campo} IS NOT NULL"
                " GROUP BY 1 ORDER BY 2 DESC LIMIT 15",
                a,
            )
        }
    itens = [
        LicitacaoResumo(
            id=r["id"],
            fonte=r["fonte"],
            numero=r["numero"],
            objeto=r["objeto"],
            modalidade=r["modalidade"],
            orgao=r["orgao"],
            abertura=r["abertura"],
            situacao=r["situacao"],
            rotulo=r["rotulo"],
            dias_para_abertura=_dias(r["abertura"]),
        )
        for r in linhas
    ]
    return PaginaLicitacoes(itens=itens, total=total, pagina=pagina, por_pagina=por_pagina, facetas=facetas)
