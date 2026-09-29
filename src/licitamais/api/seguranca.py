"""Cabecalhos de seguranca e limite de corpo (docs/SEGURANCA.md)."""

from __future__ import annotations

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

LIMITE_CORPO = 16 * 1024
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
CABECALHOS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


class CorpoGrande(HTTPException):
    # HTTPException de proposito: o FastAPI converte qualquer outra excecao na leitura do corpo em 400
    def __init__(self):
        super().__init__(413, "corpo da requisição grande demais")


class LimiteCorpo:
    """Conta os bytes que chegam: vale tambem sem Content-Length (chunked), que o cabecalho nao pega."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        tamanho = dict(scope["headers"]).get(b"content-length")
        if tamanho is not None and (not tamanho.isdigit() or int(tamanho) > LIMITE_CORPO):
            resposta = JSONResponse({"erro": "corpo da requisição grande demais"}, status_code=413)
            return await resposta(scope, receive, send)
        recebido = 0

        async def receber():
            nonlocal recebido
            mensagem = await receive()
            if mensagem["type"] == "http.request":
                recebido += len(mensagem.get("body", b""))
                if recebido > LIMITE_CORPO:
                    raise CorpoGrande()
            return mensagem

        await self.app(scope, receber, send)


class Seguranca(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resposta = await call_next(request)
        for nome, valor in CABECALHOS.items():
            resposta.headers[nome] = valor
        return resposta
