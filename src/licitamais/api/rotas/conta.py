from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StringConstraints

from licitamais.contas import auth

from ..deps import Conta, conta, csrf_ok, db

rota = APIRouter()
Palavra = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=2, max_length=40)]


class NovoAlerta(BaseModel):
    palavras: list[Palavra] = Field(min_length=1, max_length=10)


def _alerta(r) -> dict:
    try:
        palavras = json.loads(r["filter_expr"] or "{}").get("keywords", [])
    except (TypeError, ValueError):
        palavras = []
    return {"id": r["id"], "palavras": palavras, "ativo": bool(r["enabled"]), "telegram_vinculado": bool(r["target"])}


@rota.get("/alertas")
def listar(atual: Conta = Depends(conta), con: sqlite3.Connection = Depends(db)) -> list[dict]:
    return [
        _alerta(r)
        for r in con.execute(
            "SELECT id, filter_expr, enabled, target FROM alert_subscription WHERE account_id = ? ORDER BY id",
            (atual.id,),
        )
    ]


@rota.post("/alertas", status_code=201)
def criar(novo: NovoAlerta, atual: Conta = Depends(csrf_ok), con: sqlite3.Connection = Depends(db)) -> dict:
    filtro = json.dumps({"keywords": novo.palavras, "modalities": [], "entities": []}, ensure_ascii=False)
    # O lock cobre contagem + insercao: duas requisicoes nao podem ultrapassar a cota.
    con.execute("BEGIN IMMEDIATE")
    try:
        ativos = [
            json.loads(r[0] or "{}").get("keywords", [])
            for r in con.execute(
                "SELECT filter_expr FROM alert_subscription WHERE account_id = ? AND enabled = 1", (atual.id,)
            )
        ]
        if sorted(novo.palavras) in [sorted(k) for k in ativos]:
            raise HTTPException(409, "Você já tem um alerta com essas palavras.")
        if len(ativos) >= 5:
            raise HTTPException(429, "Limite de 5 alertas ativos. Apague um para criar outro.")
        # telegram_chat e a fonte do vinculo; vale mesmo quando ainda nao existia alerta.
        alvo = con.execute("SELECT chat_id FROM telegram_chat WHERE account_id = ?", (atual.id,)).fetchone()
        cur = con.execute(
            "INSERT INTO alert_subscription (kind, channel, target, filter_expr, enabled, created_at, attrs, account_id)"
            " VALUES ('filtro', 'telegram', ?, ?, 1, ?, ?, ?)",
            (alvo[0] if alvo else None, filtro, datetime.now(UTC).isoformat(), filtro, atual.id),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return _alerta(
        con.execute(
            "SELECT id, filter_expr, enabled, target FROM alert_subscription WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    )


@rota.delete("/alertas/{aid}", status_code=204)
def apagar(aid: int, atual: Conta = Depends(csrf_ok), con: sqlite3.Connection = Depends(db)) -> Response:
    con.execute("BEGIN IMMEDIATE")
    try:
        if (
            con.execute("SELECT 1 FROM alert_subscription WHERE id = ? AND account_id = ?", (aid, atual.id)).fetchone()
            is None
        ):
            raise HTTPException(404, "alerta não encontrado")
        # Inclui a outbox antiga, que guardava o ID so no prefixo de dedup_key.
        con.execute(
            "DELETE FROM alert_outbox WHERE status = 'pending' AND "
            "(subscription_id = ? OR (subscription_id IS NULL AND dedup_key LIKE ?))",
            (aid, f"{aid}:%"),
        )
        con.execute("UPDATE alert_outbox SET subscription_id = NULL WHERE subscription_id = ?", (aid,))
        con.execute("DELETE FROM alert_subscription WHERE id = ? AND account_id = ?", (aid, atual.id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return Response(status_code=204)


@rota.post("/conta/telegram")
def telegram(atual: Conta = Depends(csrf_ok), con: sqlite3.Connection = Depends(db)) -> dict:
    return {"codigo": auth.codigo_telegram(con, atual.id), "validade_minutos": 10}
