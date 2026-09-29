from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from licitamais.schema import init_schema

from .limite_consultas import LimiteConsultas, LimiteVisitantes
from .rotas import auth, conta, detalhe, evidencias, licitacoes, operador, precos, resumo
from .seguranca import LimiteCorpo, Seguranca


def create_app(db_path: str, static_dir: str | None = None) -> FastAPI:
    con = sqlite3.connect(db_path, timeout=30)  # a coleta pode estar escrevendo: espera em vez de "database is locked"
    try:
        init_schema(con)  # banco antigo ganha as migracoes ao subir (licao do r43)
    finally:
        con.close()
    app = FastAPI(title="LicitamAIs API", version="2", docs_url=None, redoc_url=None)
    app.state.db_path = db_path
    app.state.limite_consultas = LimiteConsultas()
    app.state.limite_visitantes = LimiteVisitantes()
    app.add_middleware(LimiteCorpo)
    app.add_middleware(Seguranca)  # ultimo adicionado = mais externo: o 413 tambem leva os cabecalhos

    @app.exception_handler(HTTPException)
    async def _erro_http(request: Request, exc: HTTPException):
        return JSONResponse({"erro": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _erro_validacao(request: Request, exc: RequestValidationError):
        return JSONResponse({"erro": "parâmetros inválidos"}, status_code=422)

    for modulo in (auth, licitacoes, detalhe, evidencias, precos, resumo, conta, operador):
        app.include_router(modulo.rota, prefix="/api/v2")

    @app.api_route("/api/{resto:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"], include_in_schema=False)
    def _api_404(resto: str):
        raise HTTPException(404, "não encontrado")

    if static_dir:
        raiz = Path(static_dir).resolve()

        @app.get("/{caminho:path}", include_in_schema=False)
        def _spa(caminho: str, request: Request):
            if caminho.startswith("api/"):
                raise HTTPException(404, "não encontrado")
            if caminho.startswith("assets/"):
                # resolve() antes de checar: /assets/%2e%2e/... servia arquivo fora do dist (o banco, inclusive)
                arquivo = (raiz / caminho).resolve()
                if arquivo.is_relative_to(raiz / "assets") and arquivo.is_file():
                    return FileResponse(arquivo)
                raise HTTPException(404, "não encontrado")
            return FileResponse(raiz / "index.html")

    return app
