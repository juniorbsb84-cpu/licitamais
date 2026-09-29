from __future__ import annotations

import hmac
import os
import sqlite3
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from licitamais.contas import auth

COOKIE = "licitamais_session"


class Conta(BaseModel):
    id: int
    email: str
    operador: bool
    csrf: str


def db(request: Request) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(request.app.state.db_path, check_same_thread=False, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _operadores() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("LICITAMAIS_OPERADORES", "").split(",") if e.strip()}


def conta(request: Request, con: sqlite3.Connection = Depends(db)) -> Conta:
    sessao = request.cookies.get(COOKIE, "")
    linha = auth.conta_da_sessao(con, sessao)
    if linha is None:
        raise HTTPException(401, "autenticação necessária")
    return Conta(
        id=linha["id"], email=linha["email"], operador=linha["email"].lower() in _operadores(), csrf=auth.csrf(sessao)
    )


def site_aberto() -> bool:
    return os.environ.get("LICITAMAIS_ABERTO") == "1"


def leitor(request: Request, con: sqlite3.Connection = Depends(db)) -> Conta | None:
    """Rotas de leitura: conta quando há sessão; visitante (None) só com o site aberto."""
    sessao = request.cookies.get(COOKIE, "")
    linha = auth.conta_da_sessao(con, sessao) if sessao else None
    if linha is not None:
        return Conta(
            id=linha["id"],
            email=linha["email"],
            operador=linha["email"].lower() in _operadores(),
            csrf=auth.csrf(sessao),
        )
    if site_aberto():
        return None
    raise HTTPException(401, "autenticação necessária")


def csrf_ok(request: Request, atual: Conta = Depends(conta)) -> Conta:
    if not hmac.compare_digest(request.headers.get("X-CSRF", ""), atual.csrf):
        raise HTTPException(403, "token de segurança inválido; recarregue a página")
    return atual


def operador(atual: Conta = Depends(conta)) -> Conta:
    if not atual.operador:
        raise HTTPException(403, "acesso restrito ao operador")
    return atual
