from __future__ import annotations

import ipaddress
import logging
import os
import sqlite3
import urllib.parse

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from licitamais.contas import auth
from licitamais.contas.email import enviar_link

from ..deps import COOKIE, Conta, conta, csrf_ok, db
from ..limite_consultas import ip_visitante

rota = APIRouter()
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


class PedidoLink(BaseModel):
    email: str


@rota.get("/me", response_model=Conta)
def me(atual: Conta = Depends(conta)) -> Conta:
    return atual


@rota.post("/auth/link", status_code=202)
def pedir_link(pedido: PedidoLink, request: Request, con: sqlite3.Connection = Depends(db)) -> dict:
    # 202 sempre: nao revela se o e-mail tem conta nem se bateu no limite
    ip = ip_visitante(request)  # mesmo IP do limite de consultas (atras da Cloudflare, o real)
    token = auth.pedir_link(con, pedido.email, ip)
    if token:
        try:
            enviar_link(pedido.email.strip().lower(), token)
        except Exception:
            logging.exception("falha ao enviar link de login")
    return {"mensagem": "Se o e-mail for válido, o link chega em instantes. Ele vale 15 minutos."}


@rota.get("/auth/entrar")
def confirmar_entrada(t: str = "") -> HTMLResponse:
    # Scanners de e-mail podem abrir links por GET. A sessão só nasce após confirmação humana.
    destino = "/api/v2/auth/entrar?t=" + urllib.parse.quote(t, safe="")
    pagina = (
        "<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Confirmar acesso — LicitamAIs</title></head><body>"
        "<main><h1>Confirmar acesso</h1><p>Seu link dá acesso à sua conta.</p>"
        f"<form action='{destino}' method='post'><button type='submit'>Confirmar acesso</button></form>"
        "</main></body></html>"
    )
    return HTMLResponse(pagina, headers={"Cache-Control": "no-store"})


@rota.post("/auth/entrar")
def entrar(t: str = "", con: sqlite3.Connection = Depends(db)) -> Response:
    sessao = auth.entrar(con, t)
    if not sessao:
        return RedirectResponse("/entrar?erro=link", status_code=303)
    resposta = RedirectResponse("/", status_code=303)
    # r44/A5: Secure por padrao; so dispensa em http local de desenvolvimento
    base = os.environ.get("LICITAMAIS_BASE_URL", "")
    host = urllib.parse.urlsplit(base).hostname or ""
    # tailnet (100.64.0.0/10): o WireGuard ja cifra, e o navegador descarta cookie Secure em http
    try:
        tailnet = ipaddress.ip_address(host) in _TAILNET
    except ValueError:
        tailnet = False
    local = base.startswith("http://") and (host in ("localhost", "127.0.0.1") or tailnet)
    resposta.set_cookie(
        COOKIE, sessao, max_age=30 * 24 * 3600, path="/", httponly=True, samesite="lax", secure=not local
    )
    return resposta


@rota.post("/auth/sair", status_code=204)
def sair(request: Request, atual: Conta = Depends(csrf_ok), con: sqlite3.Connection = Depends(db)) -> Response:
    con.execute("DELETE FROM session WHERE session_hash = ?", (auth.hash_token(request.cookies.get(COOKIE, "")),))
    con.commit()
    resposta = Response(status_code=204)
    resposta.delete_cookie(COOKIE, path="/")
    return resposta
