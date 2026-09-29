"""Fase vermelha TDD T21: adapter Sistema Industria na API real.

Contrato real medido em 2026-09-23, amostra em
tests/fixtures/amostras/sistema_industria_portal_2026-09-23.json:

- handshake GET /app/portalpublico, GET /api/csrf/token
- toda chamada leva cookies, X-XSRF-TOKEN url-decoded,
  Content-Type application/json; charset=utf-8 e Referer
  https://compras.sistemaindustria.com.br/compras/app/portalpublico
- descoberta GET listarpaginadoritensgrupospainelpublicacaoportal
  devolve envelope {Data:[{Id}], RowsCount, Pages, Page, PageSize}
- detalhe GET listaritensgrupospainelpublicacaoporempresamaster
  com IdsEdital repetido devolve LISTA com Codigo, Id,
  NumeroProcesso, Objeto, DataAbertura, Modalidade,
  CodigoModalidade, NomeEmpresa, DescricaoStatusEdital, NumeroEdital
- vencedores fora de escopo
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from licitamais.adapters.sistema_industria import (
    USER_AGENT,
    SistemaIndustriaAdapter,
)
from licitamais.sessions.csrf import CsrfSessionManager, handshake_3_steps
from licitamais.types import FetchedPage, FetchRequest, PlanContext

BASE_URL = "https://compras.sistemaindustria.com.br/compras"
REFERER_ESPERADO = "https://compras.sistemaindustria.com.br/compras/app/portalpublico"
CONTENT_TYPE_ESPERADO = "application/json; charset=utf-8"
RAW_TOKEN = "abc%2Fdef%2Bghi%3D%3D"
TOKEN_DECODIFICADO = "abc/def+ghi=="

AMOSTRA = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "sistema_industria_portal_2026-09-23.json"


def carregar_amostra() -> dict:
    with open(AMOSTRA, encoding="utf-8") as arq:
        return json.load(arq)


def contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=7,
    )


def pagina(request: FetchRequest, payload: object) -> FetchedPage:
    return FetchedPage(
        request=request,
        status=200,
        headers={"Content-Type": "application/json; charset=utf-8"},
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        fetched_at="2026-09-23T12:00:00Z",
        duration_ms=1,
    )


def envelope_listagem_real(page: int = 1) -> dict:
    amostra = carregar_amostra()
    resposta = amostra["listagem"]["resposta"]
    return {
        "Data": [{"Id": reg["Id"]} for reg in resposta["Data"]],
        "Pages": resposta["Pages"],
        "RowsCount": resposta["RowsCount"],
        "Page": page,
        "PageSize": resposta["PageSize"],
        "FirstRow": resposta.get("FirstRow", 0),
    }


def detalhe_real() -> list:
    amostra = carregar_amostra()
    return amostra["detalhe"]["resposta"]


class RespostaDublada:
    def __init__(self, status, *, corpo=b"{}", headers=None, cookies=None):
        self.status_code = status
        self.content = corpo
        self.headers = headers or {}
        self.cookies = requests.utils.cookiejar_from_dict(cookies or {})


def instalar_rede(monkeypatch, responder):
    chamadas = []

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


def e_portal(chamada) -> bool:
    return "portalpublico" in str(chamada["url"]).lower()


def e_token(chamada) -> bool:
    url = str(chamada["url"]).lower()
    return "csrf" in url and "token" in url


def e_dados(chamada) -> bool:
    return not e_portal(chamada) and not e_token(chamada)


def responder_handshake(chamada):
    if e_portal(chamada):
        return RespostaDublada(200, cookies={"JSESSIONID": "sessao-1"})
    if e_token(chamada):
        return RespostaDublada(
            200,
            corpo=json.dumps({"token": RAW_TOKEN}).encode("utf-8"),
            cookies={"XSRF-TOKEN": RAW_TOKEN},
        )
    return RespostaDublada(200, corpo=b'{"Data": []}')


def test_toda_chamada_apos_handshake_leva_xsrf_content_type_e_referer(
    monkeypatch,
) -> None:
    chamadas = instalar_rede(monkeypatch, responder_handshake)
    manager = CsrfSessionManager(
        base_url=BASE_URL,
        user_agent=USER_AGENT,
        ttl_seconds=600,
    )
    sessao = manager.open()
    handshake_3_steps(manager, sessao)
    req = FetchRequest(
        endpoint="/api/painelpublicacao/listarpaginadoritensgrupospainelpublicacaoportal",
        method="GET",
        params={"page": "1"},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )
    manager.call(sessao, req)

    dados = [c for c in chamadas if e_dados(c)]
    assert dados, "esperava ao menos uma chamada de dados apos o handshake"
    for chamada in dados:
        assert chamada["headers"]["X-XSRF-TOKEN"] == TOKEN_DECODIFICADO
        assert chamada["headers"]["Content-Type"] == CONTENT_TYPE_ESPERADO
        assert chamada["headers"]["Referer"] == REFERER_ESPERADO


def test_plan_descoberta_usa_painelpublicacao_nunca_api_processos() -> None:
    adapter = SistemaIndustriaAdapter()
    primeira = next(adapter.plan(contexto()))
    assert "listarpaginadoritensgrupospainelpublicacaoportal" in primeira.endpoint
    assert "/api/processos" not in primeira.endpoint
    params = dict(primeira.params)
    assert params["page"] == "1"
    assert params["pageSize"] == "8"
    assert params["pageIndex"] == "0"


def test_parse_listagem_real_emite_detalhe_com_ids_e_signals_total() -> None:
    adapter = SistemaIndustriaAdapter()
    primeira = next(adapter.plan(contexto()))
    envelope = envelope_listagem_real(page=1)
    assert envelope["RowsCount"] == 294
    resultado = adapter.parse(pagina(primeira, envelope))

    assert resultado.fatal is None
    assert resultado.signals["row_count_declared"] == 294
    assert resultado.signals["row_count_scope"] == "total"
    assert resultado.next, "listagem deve emitir request de detalhe"
    detalhe = [r for r in resultado.next if "porempresamaster" in r.endpoint]
    assert detalhe, "esperava request de detalhe por lote de Ids"
    params = dict(detalhe[0].params)
    ids_param = params.get("IdsEdital")
    if isinstance(ids_param, str):
        ids_param = [ids_param]
    else:
        ids_param = list(ids_param)
    ids_esperados = [reg["Id"] for reg in envelope["Data"]]
    assert sorted(ids_param) == sorted(ids_esperados)


def _request_detalhe_corrente(adapter, primeira) -> FetchRequest:
    resultado = adapter.parse(pagina(primeira, envelope_listagem_real(page=1)))
    for prox in resultado.next:
        if "porempresamaster" in prox.endpoint:
            return prox
    return FetchRequest(
        endpoint="/api/painelpublicacao/listaritensgrupospainelpublicacaoporempresamaster",
        method="GET",
        params={"IdsEdital": []},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="hydrate",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=2,
        cursor_out=None,
    )


def test_parse_detalhe_real_produz_3_processos_mapeados() -> None:
    adapter = SistemaIndustriaAdapter()
    primeira = next(adapter.plan(contexto()))
    req_detalhe = _request_detalhe_corrente(adapter, primeira)
    registros = detalhe_real()
    assert len(registros) == 3
    resultado = adapter.parse(pagina(req_detalhe, registros))

    assert resultado.fatal is None
    assert len(resultado.batch.processes) == 3
    por_id = {p.source_native_id: p for p in resultado.batch.processes}
    for esperado in registros:
        proc = por_id[esperado["Id"]]
        assert proc.attrs["number"] == esperado["NumeroProcesso"]
        assert proc.attrs["object"] == esperado["Objeto"]
        assert proc.attrs["entidade"] == esperado["NomeEmpresa"]
        assert proc.attrs["opening_at_source"] == esperado["DataAbertura"]


def test_parse_invalido_envelope_sem_data_e_detalhe_nao_lista_dao_fatal() -> None:
    adapter = SistemaIndustriaAdapter()
    primeira = next(adapter.plan(contexto()))

    sem_data = envelope_listagem_real(page=1)
    del sem_data["Data"]
    resultado_lista = adapter.parse(pagina(primeira, sem_data))
    assert resultado_lista.fatal is not None

    req_detalhe = _request_detalhe_corrente(adapter, primeira)
    resultado_detalhe = adapter.parse(pagina(req_detalhe, {"nao": "e lista"}))
    assert resultado_detalhe.fatal is not None


def test_paginacao_avanca_ate_pages_e_para_no_final() -> None:
    adapter = SistemaIndustriaAdapter()
    primeira = next(adapter.plan(contexto()))
    amostra = carregar_amostra()
    pages = amostra["listagem"]["resposta"]["Pages"]

    meio = adapter.parse(pagina(primeira, envelope_listagem_real(page=1)))
    assert meio.fatal is None
    proxima = [r for r in meio.next if "listarpaginador" in r.endpoint]
    assert proxima, "pagina 1 deve gerar pagina 2"
    params = dict(proxima[0].params)
    valores = " ".join([str(v) for v in params.values()])
    assert "2" in valores

    ultima_req = FetchRequest(
        endpoint=proxima[0].endpoint,
        method="GET",
        params=dict(proxima[0].params),
        body=None,
        headers_extra={"Accept": "application/json"},
        phase=proxima[0].phase,
        entity_hint=proxima[0].entity_hint,
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )
    # simula que a ultima requisicao pede a pagina final
    params_fim = dict(ultima_req.params)
    params_fim["page"] = str(pages)
    params_fim["pageIndex"] = str(pages - 1)
    ultima_req_fim = FetchRequest(
        endpoint=ultima_req.endpoint,
        method="GET",
        params=params_fim,
        body=None,
        headers_extra={"Accept": "application/json"},
        phase=ultima_req.phase,
        entity_hint=ultima_req.entity_hint,
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )
    fim = adapter.parse(pagina(ultima_req_fim, envelope_listagem_real(page=pages)))
    assert fim.fatal is None
    assert [r for r in fim.next if "listarpaginador" in r.endpoint] == []
