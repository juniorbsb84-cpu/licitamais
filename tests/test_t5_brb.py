"""
2026-09-23 (orquestrador): 5 testes deste arquivo foram aposentados -- verificavam
o contrato INVENTADO do BRB (/api/processos + sub-endpoints), que devolve 404 na API
real. Substituidos por test_r20_brb_api_real.py, r20b e r20c (fixture real).
Fase vermelha do TDD para o adapter da API aberta do BRB."""

from __future__ import annotations

import importlib
import socket
from pathlib import Path

import pytest

from licitamais.types import FetchedPage, FetchRequest, PlanContext

GOLDEN_DIR = Path(__file__).parent / "golden" / "brb"
MODULO = "licitamais.adapters.brb"


@pytest.fixture(scope="module")
def brb():
    """A fase vermelha deve decorrer apenas da ausencia do adapter T5."""
    return importlib.import_module(MODULO)


def _request_listagem(pagina: str = "1") -> FetchRequest:
    return FetchRequest(
        endpoint="/api/processos",
        method="GET",
        params={"pagina": pagina},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _pagina(request: FetchRequest, nome: str) -> FetchedPage:
    return FetchedPage(
        request=request,
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=(GOLDEN_DIR / nome).read_bytes(),
        fetched_at="2026-09-22T12:00:00Z",
        duration_ms=1,
    )


def _contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=1,
    )


def test_capacidades_declaradas_para_full_diff(brb) -> None:
    adapter = brb.BRBAdapter()

    assert adapter.source_code == "brb"
    assert adapter.adapter_version == "0.1.0"
    assert adapter.capabilities.strategy == "full_diff"
    assert adapter.capabilities.auth == "none"
    assert adapter.capabilities.listing_order_stable is True
    assert adapter.capabilities.deletion_semantics == "absence"
    assert adapter.capabilities.probes["absence_confirm_runs"] == 2


def test_plan_emite_listagem_completa_reentrante(brb) -> None:
    adapter = brb.BRBAdapter()

    primeiro = tuple(adapter.plan(_contexto()))
    segundo = tuple(adapter.plan(_contexto()))

    assert primeiro
    assert primeiro == segundo
    assert all(request.phase == "discover" for request in primeiro)
    assert all(request.entity_hint == "process" for request in primeiro)


def test_decode_usa_utf8_estrito_e_nunca_cp1252(brb) -> None:
    texto = '{"objeto":"Licitação com ação"}'

    assert brb.decode_brb_bytes(texto.encode("utf-8")) == texto
    with pytest.raises(UnicodeDecodeError):
        brb.decode_brb_bytes(b'{"objeto":"licita\xe7\xe3o"}')


def test_fetch_usa_apenas_sessao_dupla(brb, monkeypatch: pytest.MonkeyPatch) -> None:
    class RespostaDupla:
        status_code = 200
        content = b'{"registros": []}'
        headers = {"content-type": "application/json; charset=utf-8"}

    class SessaoDupla:
        def __init__(self) -> None:
            self.chamadas: list[tuple[str, str]] = []

        def request(self, method: str, url: str, **kwargs: object) -> RespostaDupla:
            self.chamadas.append((method, url))
            return RespostaDupla()

        def get(self, url: str, **kwargs: object) -> RespostaDupla:
            return self.request("GET", url, **kwargs)

    def conexao_proibida(*args: object, **kwargs: object) -> None:
        raise AssertionError("o teste nao pode abrir conexao de rede real")

    monkeypatch.setattr(socket, "create_connection", conexao_proibida)
    sessao = SessaoDupla()

    resposta = brb.BRBAdapter().fetch(sessao, _request_listagem())

    assert sessao.chamadas
    assert resposta.status == 200
    assert resposta.body == RespostaDupla.content
