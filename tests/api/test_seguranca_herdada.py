"""Regras de seguranca do site antigo (r41, r43, r44) que a API v2 precisa manter antes de o site antigo sair."""

from __future__ import annotations

import sqlite3

import pytest

from licitamais.api import create_app


@pytest.fixture()
def enviados(monkeypatch):
    lista = []
    monkeypatch.setattr("licitamais.api.rotas.auth.enviar_link", lambda e, t: lista.append((e, t)))
    return lista


def _token(anonimo, enviados, email="a@b.com"):
    anonimo.post("/api/v2/auth/link", json={"email": email})
    return enviados[-1][1]


# r41: limite de corpo nao pode ser contornado sem Content-Length
def test_post_sem_content_length_413(anonimo):
    def partes():
        yield b'{"email": "'
        yield b"x" * 20000
        yield b'"}'

    r = anonimo.post("/api/v2/auth/link", content=partes(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_content_length_negativo_413(anonimo):
    r = anonimo.post(
        "/api/v2/auth/link", content=b"{}", headers={"content-type": "application/json", "content-length": "-1"}
    )
    assert r.status_code in (400, 413)


# r43: token adulterado, expirado e reutilizado
def test_token_adulterado_expirado_reutilizado(anonimo, enviados, db_path):
    token = _token(anonimo, enviados)
    entrar = lambda t: anonimo.post(f"/api/v2/auth/entrar?t={t}", follow_redirects=False).headers["location"]  # noqa: E731
    assert entrar(token + "x") == "/entrar?erro=link"
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE login_token SET expires_at = '2000-01-01'")
    assert entrar(token) == "/entrar?erro=link"
    token = _token(anonimo, enviados)
    assert entrar(token) == "/"
    assert entrar(token) == "/entrar?erro=link"


# r43: limite de pedidos por e-mail (5/h) e por IP (20/h), com resposta identica
def test_limite_de_pedidos_de_link(anonimo, enviados, db_path):
    respostas = {anonimo.post("/api/v2/auth/link", json={"email": "n@x.com"}).text for _ in range(7)}
    assert len(respostas) == 1
    assert len([e for e in enviados if e[0] == "n@x.com"]) == 5
    for i in range(30):
        anonimo.post("/api/v2/auth/link", json={"email": f"o{i}@x.com"})
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM login_token").fetchone()[0] == 20


# r44/A5: Secure por padrao; so http://localhost ou http://127.0.0.1 dispensa
@pytest.mark.parametrize(
    "base,seguro",
    [
        ("http://localhost:8090", False),
        ("http://127.0.0.1:8090", False),
        ("http://100.100.1.1:8090", False),  # tailnet: WireGuard ja cifra; navegador recusa Secure em http
        ("http://100.128.0.1:8090", True),  # fora de 100.64.0.0/10
        ("http://localhost.exemplo.com", True),
        ("https://licitamais.com.br", True),
        ("", True),
    ],
)
def test_cookie_secure_pela_base_url(anonimo, enviados, monkeypatch, base, seguro):
    monkeypatch.delenv("LICITAMAIS_DEV", raising=False)
    if base:
        monkeypatch.setenv("LICITAMAIS_BASE_URL", base)
    else:
        monkeypatch.delenv("LICITAMAIS_BASE_URL", raising=False)
    token = _token(anonimo, enviados)
    cookie = anonimo.post(f"/api/v2/auth/entrar?t={token}", follow_redirects=False).headers["set-cookie"]
    assert ("secure" in cookie.lower()) is seguro


# r41: alerta duplicado nao cria segunda linha; maximo de 5 alertas ativos por conta
def test_alerta_duplicado_409(cliente):
    assert cliente.post("/api/v2/alertas", json={"palavras": ["pneus"]}).status_code == 201
    assert cliente.post("/api/v2/alertas", json={"palavras": ["Pneus "]}).status_code == 409
    assert len(cliente.get("/api/v2/alertas").json()) == 1


def test_limite_de_5_alertas(cliente):
    for i in range(5):
        assert cliente.post("/api/v2/alertas", json={"palavras": [f"termo{i}"]}).status_code == 201
    r = cliente.post("/api/v2/alertas", json={"palavras": ["sexto"]})
    assert r.status_code == 429 and "5" in r.json()["erro"]


# r43: subir a API sobre banco antigo aplica as migracoes (antes: 500 "no such table: login_token")
def test_create_app_migra_banco_antigo(tmp_path):
    caminho = str(tmp_path / "velho.db")
    sqlite3.connect(caminho).close()
    create_app(caminho)
    with sqlite3.connect(caminho) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 'login_token'").fetchone()
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 'v_licitacao'").fetchone()


def test_create_app_espera_a_coleta_liberar_o_banco(tmp_path):
    """Com a coleta segurando a escrita, a API caia em 'database is locked' ao migrar (5 s)."""
    import threading
    import time

    caminho = str(tmp_path / "ocupado.db")
    sqlite3.connect(caminho).close()
    coleta = sqlite3.connect(caminho, check_same_thread=False)
    coleta.execute("BEGIN EXCLUSIVE")
    threading.Timer(12, coleta.rollback).start()
    inicio = time.time()
    create_app(caminho)
    assert time.time() - inicio >= 11
