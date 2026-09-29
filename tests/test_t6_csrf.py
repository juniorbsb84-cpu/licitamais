"""Fase vermelha do TDD para o handshake CSRF de Sistema Industria."""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

MODULO_CSRF = "licitamais.sessions.csrf"
BASE_URL = "https://sistema-industria.exemplo.test"
USER_AGENT = "licitamais-sistema-industria/1.0 (contato: operador@exemplo.test)"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"
RAW_TOKEN = "abc%2Fdef%2Bghi%3D%3D"
TOKEN_DECODIFICADO = "abc/def+ghi=="


class RespostaDublada:
    """Resposta minima, sem qualquer chamada de rede real."""

    def __init__(
        self,
        status: int,
        *,
        corpo: bytes = b"{}",
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self.content = corpo
        self.headers = headers or {}
        self.cookies = requests.utils.cookiejar_from_dict(cookies or {})

    def json(self) -> object:
        return json.loads(self.content.decode("utf-8"))


def carregar_producao():
    """Falha em vermelho pela ausencia do modulo de producao, nao na coleta."""
    spec = importlib.util.find_spec(MODULO_CSRF)
    assert spec is not None, f"O modulo de producao {MODULO_CSRF} ainda nao existe."
    return importlib.import_module(MODULO_CSRF)


def criar_request() -> object:
    """Usa o tipo de fronteira entregue pelo protocolo do adaptador."""
    from licitamais.types import FetchRequest

    return FetchRequest(
        endpoint="/api/processos",
        method="GET",
        params={"pagina": "1", "tamanho": "20"},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def instalar_rede_dublada(monkeypatch, responder):
    """Registra requests.Session.request e devolve apenas respostas dubladas."""
    chamadas: list[dict[str, object]] = []

    def request_dublada(session, method, url, **kwargs):
        chamada = {
            "method": str(method).upper(),
            "url": str(url),
            "headers": dict(kwargs.get("headers") or {}),
            "params": dict(kwargs.get("params") or {}),
            "cookies": dict(kwargs.get("cookies") or {}),
        }
        chamadas.append(chamada)
        return responder(chamada)

    monkeypatch.setattr(requests.Session, "request", request_dublada)
    return chamadas


def e_portal_publico(chamada: dict[str, object]) -> bool:
    return "portalpublico" in str(chamada["url"]).lower()


def e_token_csrf(chamada: dict[str, object]) -> bool:
    url = str(chamada["url"]).lower()
    return "csrf" in url and "token" in url


def e_dados(chamada: dict[str, object]) -> bool:
    return not e_portal_publico(chamada) and not e_token_csrf(chamada)


def resposta_handshake(chamada: dict[str, object]) -> RespostaDublada:
    if e_portal_publico(chamada):
        return RespostaDublada(200, cookies={"JSESSIONID": "sessao-do-run"})

    if e_token_csrf(chamada):
        return RespostaDublada(
            200,
            corpo=json.dumps({"token": RAW_TOKEN}).encode("utf-8"),
            cookies={"XSRF-TOKEN": RAW_TOKEN},
        )

    return RespostaDublada(200, corpo=b'{"processos": []}')


def abrir_sessao(modulo):
    manager = modulo.CsrfSessionManager(
        base_url=BASE_URL,
        user_agent=USER_AGENT,
        ttl_seconds=600,
    )
    sessao = manager.open()
    return manager, modulo.handshake_3_steps(manager, sessao)


def test_url_decode_token_e_classificacao_de_auth() -> None:
    modulo = carregar_producao()

    assert modulo.url_decode_token(RAW_TOKEN) == TOKEN_DECODIFICADO
    assert modulo.url_decode_token("sem-encoding") == "sem-encoding"
    assert modulo.is_retryable_auth(401) is True
    assert modulo.is_retryable_auth(403) is True
    assert modulo.is_retryable_auth(404) is False
    assert modulo.is_retryable_auth(429) is False
    assert modulo.is_retryable_auth(500) is False


def test_handshake_tem_tres_passos_em_ordem_e_headers_corretos(monkeypatch) -> None:
    modulo = carregar_producao()
    chamadas = instalar_rede_dublada(monkeypatch, resposta_handshake)

    manager, sessao = abrir_sessao(modulo)
    manager.call(sessao, criar_request())

    assert len(chamadas) == 3
    assert e_portal_publico(chamadas[0])
    assert e_token_csrf(chamadas[1])
    assert e_dados(chamadas[2])

    dados = chamadas[2]
    assert dados["method"] == "GET"
    assert dados["headers"]["Content-Type"] == CONTENT_TYPE_JSON
    # 2026-09-23 (curl real): sem Referer da pagina do portal a API devolve 403
    assert dados["headers"]["Referer"] == f"{BASE_URL}/app/portalpublico"
    assert dados["headers"]["X-XSRF-TOKEN"] == TOKEN_DECODIFICADO
    assert dados["headers"]["X-XSRF-TOKEN"] != RAW_TOKEN

    for chamada in chamadas:
        assert chamada["headers"]["User-Agent"] == USER_AGENT
        assert chamada["headers"]["Content-Type"] == CONTENT_TYPE_JSON
        assert chamada["headers"]["Referer"] == f"{BASE_URL}/app/portalpublico"


@pytest.mark.parametrize("status", [401, 403])
def test_primeiro_auth_refaz_handshake_e_repete_request_exata_uma_vez(monkeypatch, status) -> None:
    modulo = carregar_producao()
    tentativas_dados = 0

    def responder(chamada: dict[str, object]) -> RespostaDublada:
        nonlocal tentativas_dados

        if not e_dados(chamada):
            return resposta_handshake(chamada)

        tentativas_dados += 1
        if tentativas_dados == 1:
            return RespostaDublada(status, corpo=b"auth recusada")
        return RespostaDublada(200, corpo=b'{"processos": []}')

    chamadas = instalar_rede_dublada(monkeypatch, responder)
    manager, sessao = abrir_sessao(modulo)

    pagina = manager.call(sessao, criar_request())

    assert pagina.status_code == 200
    assert tentativas_dados == 2

    dados = [chamada for chamada in chamadas if e_dados(chamada)]
    assert len(dados) == 2
    assert dados[0]["method"] == dados[1]["method"] == "GET"
    assert dados[0]["url"] == dados[1]["url"]
    assert dados[0]["params"] == dados[1]["params"]
    assert dados[0]["headers"] == dados[1]["headers"]

    assert len([chamada for chamada in chamadas if e_portal_publico(chamada)]) == 2
    assert len([chamada for chamada in chamadas if e_token_csrf(chamada)]) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_segundo_auth_nao_faz_terceira_chamada_e_propaga_erro(monkeypatch, status) -> None:
    modulo = carregar_producao()
    tentativas_dados = 0

    def responder(chamada: dict[str, object]) -> RespostaDublada:
        nonlocal tentativas_dados

        if not e_dados(chamada):
            return resposta_handshake(chamada)

        tentativas_dados += 1
        return RespostaDublada(status, corpo=b"auth recusada")

    chamadas = instalar_rede_dublada(monkeypatch, responder)
    manager, sessao = abrir_sessao(modulo)

    with pytest.raises(Exception):
        manager.call(sessao, criar_request())

    assert tentativas_dados == 2
    assert len([chamada for chamada in chamadas if e_portal_publico(chamada)]) == 2
    assert len([chamada for chamada in chamadas if e_token_csrf(chamada)]) == 2


def test_ttl_expirado_forca_novo_handshake_antes_da_proxima_chamada(monkeypatch) -> None:
    modulo = carregar_producao()
    chamadas = instalar_rede_dublada(monkeypatch, resposta_handshake)
    manager, sessao = abrir_sessao(modulo)

    sessao.last_handshake_at = (datetime.now(UTC) - timedelta(seconds=601)).isoformat()

    manager.call(sessao, criar_request())

    assert len([chamada for chamada in chamadas if e_portal_publico(chamada)]) == 2
    assert len([chamada for chamada in chamadas if e_token_csrf(chamada)]) == 2


def test_user_agent_e_constante_e_identificavel_em_todo_o_run(monkeypatch) -> None:
    modulo = carregar_producao()
    chamadas = instalar_rede_dublada(monkeypatch, resposta_handshake)
    manager, sessao = abrir_sessao(modulo)

    manager.call(sessao, criar_request())
    manager.call(sessao, criar_request())

    assert "licitamais" in USER_AGENT.lower()
    assert USER_AGENT != "python-requests"
    assert chamadas
    assert all(chamada["headers"].get("User-Agent") == USER_AGENT for chamada in chamadas)


def test_golden_403_sem_content_type_impede_regressao(monkeypatch, tmp_path) -> None:
    """O golden reproduz a assinatura real: 403 sem Content-Type na resposta."""
    modulo = carregar_producao()
    golden = Path(tmp_path) / "403_sem_content_type.json"
    golden.write_text(
        json.dumps(
            {
                "descricao": ("GoBuyer responde 403 sem Content-Type quando a chamada de dados omite Content-Type."),
                "request": {
                    "method": "GET",
                    "path": "/api/processos",
                    "headers_ausentes": ["Content-Type"],
                },
                "response": {
                    "status": 403,
                    "headers": {},
                    "body_utf8": "forbidden: missing Content-Type",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    evidencia = json.loads(golden.read_text(encoding="utf-8"))

    def responder(chamada: dict[str, object]) -> RespostaDublada:
        if not e_dados(chamada):
            return resposta_handshake(chamada)

        if chamada["headers"].get("Content-Type") != CONTENT_TYPE_JSON:
            return RespostaDublada(
                evidencia["response"]["status"],
                corpo=evidencia["response"]["body_utf8"].encode("utf-8"),
                headers=evidencia["response"]["headers"],
            )
        return RespostaDublada(200, corpo=b'{"processos": []}')

    chamadas = instalar_rede_dublada(monkeypatch, responder)
    manager, sessao = abrir_sessao(modulo)

    pagina = manager.call(sessao, criar_request())

    assert pagina.status_code == 200
    dados = [chamada for chamada in chamadas if e_dados(chamada)]
    assert len(dados) == 1
    assert dados[0]["headers"]["Content-Type"] == CONTENT_TYPE_JSON


def test_sessao_nao_vaza_cookies_entre_runs_distintos(monkeypatch) -> None:
    """Regressao: cada run possui jar proprio, sem cookie de outro manager."""
    modulo = carregar_producao()

    def responder(chamada: dict[str, object]) -> RespostaDublada:
        if e_portal_publico(chamada):
            marcador = "cookie-run-1" if "//run1." in str(chamada["url"]) else "cookie-run-2"
            return RespostaDublada(200, cookies={"JSESSIONID": marcador})

        if e_token_csrf(chamada):
            return RespostaDublada(
                200,
                corpo=json.dumps({"token": RAW_TOKEN}).encode("utf-8"),
                cookies={"XSRF-TOKEN": RAW_TOKEN},
            )

        return RespostaDublada(200, corpo=b'{"processos": []}')

    instalar_rede_dublada(monkeypatch, responder)

    manager_1 = modulo.CsrfSessionManager(
        base_url="https://run1.sistema-industria.exemplo.test",  # run distinto por host
        user_agent=USER_AGENT,
        ttl_seconds=600,
    )
    sessao_1 = modulo.handshake_3_steps(manager_1, manager_1.open())

    manager_2 = modulo.CsrfSessionManager(
        base_url="https://run2.sistema-industria.exemplo.test",
        user_agent=USER_AGENT,
        ttl_seconds=600,
    )
    sessao_2 = modulo.handshake_3_steps(manager_2, manager_2.open())

    assert sessao_1.cookie_jar is not sessao_2.cookie_jar
    assert sessao_1.cookie_jar["JSESSIONID"] == "cookie-run-1"
    assert sessao_2.cookie_jar["JSESSIONID"] == "cookie-run-2"

    sessao_1.cookie_jar["SOMENTE_RUN_1"] = "segredo"
    assert "SOMENTE_RUN_1" not in sessao_2.cookie_jar


@pytest.mark.parametrize("status", [404, 500, 502, 503])
def test_erro_http_nao_auth_nao_vira_pagina_de_sucesso(monkeypatch, status) -> None:
    """Evita interpretar corpo de erro como lista vazia ou captura valida."""
    modulo = carregar_producao()

    def responder(chamada: dict[str, object]) -> RespostaDublada:
        if not e_dados(chamada):
            return resposta_handshake(chamada)
        return RespostaDublada(
            status,
            corpo=b"<html><body>erro do portal</body></html>",
            headers={"Content-Type": "text/html"},
        )

    chamadas = instalar_rede_dublada(monkeypatch, responder)
    manager, sessao = abrir_sessao(modulo)

    with pytest.raises(Exception):
        manager.call(sessao, criar_request())

    assert len([chamada for chamada in chamadas if e_dados(chamada)]) == 1
