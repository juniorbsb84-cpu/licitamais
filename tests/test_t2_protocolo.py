"""Fase vermelha do TDD para o protocolo e tipos dos adaptadores."""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import inspect
from collections.abc import Iterator, Mapping
from typing import get_args, get_origin, get_type_hints

import pytest

COLETADO_EM = "2026-09-22T12:00:00Z"
MODULO = "licitamais.adapters.protocolo"


@pytest.fixture(scope="module")
def protocolo():
    """Falha na execucao, e nao na coleta, enquanto T2 nao existir."""
    spec = importlib.util.find_spec(MODULO)
    assert spec is not None, f"O modulo de producao {MODULO} ainda nao existe."
    return importlib.import_module(MODULO)


def criar_request(protocolo, **sobrescritos: object):
    valores: dict[str, object] = {
        "endpoint": "/api/processos",
        "method": "GET",
        "params": {"pagina": "1"},
        "body": None,
        "headers_extra": {"Accept": "application/json"},
        "phase": "discover",
        "entity_hint": "process",
        "parent_native_id": None,
        "cost_weight": 1,
        "cursor_out": None,
    }
    valores.update(sobrescritos)
    return protocolo.FetchRequest(**valores)


def criar_batch_vazio(protocolo, **sobrescritos: object):
    valores: dict[str, object] = {
        "orgs": (),
        "processes": (),
        "items": (),
        "attachments": (),
        "results": (),
        "awards": (),
        "contracts": (),
        "phases": (),
    }
    valores.update(sobrescritos)
    return protocolo.NormalizedBatch(**valores)


def test_modulo_so_usa_biblioteca_padrao(protocolo) -> None:
    assert "requests" not in vars(protocolo)
    assert "sqlite3" not in vars(protocolo)


def test_adapter_e_protocol_com_superficie_do_contrato(protocolo) -> None:
    Adapter = protocolo.Adapter

    assert getattr(Adapter, "_is_protocol", False) is True
    assert get_type_hints(Adapter) == {
        "source_code": str,
        "adapter_version": str,
        "capabilities": protocolo.Capabilities,
    }

    assert list(inspect.signature(Adapter.open).parameters) == ["self", "cfg"]
    assert list(inspect.signature(Adapter.close).parameters) == ["self", "s"]
    assert list(inspect.signature(Adapter.plan).parameters) == ["self", "ctx"]
    assert list(inspect.signature(Adapter.fetch).parameters) == ["self", "s", "req"]
    assert list(inspect.signature(Adapter.parse).parameters) == ["self", "raw"]

    assert get_type_hints(Adapter.open)["cfg"] is protocolo.SourceConfig
    assert get_type_hints(Adapter.open)["return"] is protocolo.Session
    assert get_type_hints(Adapter.plan)["ctx"] is protocolo.PlanContext
    assert get_type_hints(Adapter.parse)["raw"] is protocolo.FetchedPage
    assert get_type_hints(Adapter.parse)["return"] is protocolo.ParseResult

    retorno_plan = get_type_hints(Adapter.plan)["return"]
    assert get_origin(retorno_plan) is Iterator
    assert get_args(retorno_plan) == (protocolo.FetchRequest,)

    retorno_fetch = get_type_hints(Adapter.fetch)["return"]
    assert retorno_fetch == protocolo.FetchedPage | protocolo.FetchFailure


def test_capabilities_expoe_todas_as_chaves_declarativas(protocolo) -> None:
    assert {
        "strategy",
        "entities",
        "hydration",
        "listing_order_stable",
        "supports_conditional_get",
        "deletion_semantics",
        "rate",
        "auth",
        "probes",
    } <= set(get_type_hints(protocolo.Capabilities))


def test_tipos_de_fronteira_sao_dataclasses_frozen(protocolo) -> None:
    for tipo in (
        protocolo.FetchRequest,
        protocolo.FetchedPage,
        protocolo.FetchFailure,
        protocolo.ParseResult,
        protocolo.NormalizedBatch,
        protocolo.PlanContext,
        protocolo.SourceConfig,
        protocolo.Capabilities,
    ):
        assert dataclasses.is_dataclass(tipo)
        assert tipo.__dataclass_params__.frozen is True


def test_requests_equivalentes_tem_mesma_key(protocolo) -> None:
    primeira = criar_request(protocolo, params={"pagina": "1", "tamanho": "10"})
    segunda = criar_request(protocolo, params={"tamanho": "10", "pagina": "1"})

    assert primeira.key == segunda.key


def test_headers_distintos_nao_podem_ser_deduplicados(protocolo) -> None:
    primeiro = criar_request(
        protocolo,
        headers_extra={"Authorization": "Bearer token-a"},
    )
    segundo = criar_request(
        protocolo,
        headers_extra={"Authorization": "Bearer token-b"},
    )

    assert primeiro.key != segundo.key


def test_nome_de_header_e_case_insensitive_na_key(protocolo) -> None:
    primeiro = criar_request(
        protocolo,
        headers_extra={"Authorization": "Bearer token"},
    )
    segundo = criar_request(
        protocolo,
        headers_extra={"authorization": "Bearer token"},
    )

    assert primeiro.key == segundo.key


def test_cursor_out_e_cost_weight_nao_sao_identidade(protocolo) -> None:
    primeira = criar_request(
        protocolo,
        cursor_out={"pagina": "2"},
        cost_weight=1,
    )
    segunda = criar_request(
        protocolo,
        cursor_out={"pagina": "3"},
        cost_weight=99,
    )

    assert primeira.key == segunda.key


def test_fetch_request_isola_mappings_externos(protocolo) -> None:
    params = {"busca": "licitacao"}
    headers = {"X-Nome": "Joao"}
    request = criar_request(protocolo, params=params, headers_extra=headers)

    key_original = request.key
    params["busca"] = "alterado"
    headers["X-Nome"] = "alterado"

    assert request.key == key_original
    assert request.params == {"busca": "licitacao"}
    assert request.headers_extra == {"X-Nome": "Joao"}

    with pytest.raises(TypeError):
        request.params["busca"] = "mutacao"
    with pytest.raises(TypeError):
        request.headers_extra["X-Nome"] = "mutacao"


def test_key_nao_ascii_e_deterministica_sem_repr_de_bytes(protocolo) -> None:
    primeira = criar_request(
        protocolo,
        params={"busca": "licitacao"},
        headers_extra={"X-Nome": "Joao"},
        body=b"acao",
    )
    segunda = criar_request(
        protocolo,
        params={"busca": "licitacao"},
        headers_extra={"x-nome": "Joao"},
        body=b"acao",
    )

    assert primeira.key == segunda.key
    assert "b'" not in primeira.key
    assert 'b"' not in primeira.key


def test_plan_context_congela_cursores_e_chaves_vistas(protocolo) -> None:
    cursores = {"pagina": "1"}
    vistas = {"request-1"}

    contexto = protocolo.PlanContext(
        cursors=cursores,
        seen_request_keys=vistas,
        parsed_so_far=0,
        source_id=1,
    )
    cursores["pagina"] = "2"
    vistas.add("request-2")

    assert isinstance(contexto.cursors, Mapping)
    assert contexto.cursors == {"pagina": "1"}
    assert contexto.seen_request_keys == frozenset({"request-1"})

    with pytest.raises(TypeError):
        contexto.cursors["pagina"] = "3"
    with pytest.raises(AttributeError):
        contexto.seen_request_keys.add("request-3")


def test_parse_so_recebe_pagina_de_sucesso(protocolo) -> None:
    assert get_type_hints(protocolo.Adapter.parse)["raw"] is protocolo.FetchedPage


def test_fetched_page_so_representa_status_2xx_e_body_bytes(protocolo) -> None:
    request = criar_request(protocolo)

    pagina = protocolo.FetchedPage(
        request=request,
        status=200,
        headers={"content-type": "application/json"},
        body=b'{"registros": []}',
        fetched_at=COLETADO_EM,
        duration_ms=10,
    )
    assert pagina.body == b'{"registros": []}'

    with pytest.raises(ValueError, match="status"):
        protocolo.FetchedPage(
            request=request,
            status=503,
            headers={},
            body=b"<html>erro</html>",
            fetched_at=COLETADO_EM,
            duration_ms=10,
        )

    with pytest.raises(ValueError, match="bytes"):
        protocolo.FetchedPage(
            request=request,
            status=200,
            headers={},
            body="{}",
            fetched_at=COLETADO_EM,
            duration_ms=10,
        )


def test_fetch_failure_exige_erro_nao_vazio(protocolo) -> None:
    with pytest.raises(ValueError, match="error"):
        protocolo.FetchFailure(
            request=criar_request(protocolo),
            error="   ",
            fetched_at=COLETADO_EM,
            duration_ms=10,
            status=None,
        )


def test_parse_result_tem_fatal_opcional(protocolo) -> None:
    batch_vazio = criar_batch_vazio(protocolo)

    sucesso_vazio = protocolo.ParseResult(
        batch=batch_vazio,
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={},
    )
    falha_fatal = protocolo.ParseResult(
        batch=batch_vazio,
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={},
        fatal="HTML recebido onde era esperado JSON",
    )

    assert sucesso_vazio.fatal is None
    assert falha_fatal.fatal == "HTML recebido onde era esperado JSON"


def test_dataclasses_frozen_nao_retem_colecoes_mutaveis(protocolo) -> None:
    processes = ["processo-1"]
    quarantine = ["registro invalido"]
    proximas = [criar_request(protocolo)]
    cursor_out = {"pagina": "2"}
    signals = {"total": 1}

    batch = criar_batch_vazio(protocolo, processes=processes)
    resultado = protocolo.ParseResult(
        batch=batch,
        quarantine=quarantine,
        next=proximas,
        cursor_out=cursor_out,
        signals=signals,
    )

    processes.clear()
    quarantine.clear()
    proximas.clear()
    cursor_out["pagina"] = "3"
    signals["total"] = 2

    assert batch.processes == ("processo-1",)
    assert resultado.quarantine == ("registro invalido",)
    assert resultado.next == (criar_request(protocolo),)
    assert resultado.cursor_out == {"pagina": "2"}
    assert resultado.signals == {"total": 1}

    with pytest.raises(AttributeError):
        batch.processes.clear()
    with pytest.raises(AttributeError):
        resultado.quarantine.clear()
    with pytest.raises(AttributeError):
        resultado.next.clear()
    with pytest.raises(TypeError):
        resultado.cursor_out["pagina"] = "4"
    with pytest.raises(TypeError):
        resultado.signals["total"] = 3


def test_source_config_exige_source_code_e_base_url(protocolo) -> None:
    with pytest.raises(ValueError):
        protocolo.SourceConfig(
            source_code="",
            base_url="https://exemplo.test",
        )

    with pytest.raises(ValueError):
        protocolo.SourceConfig(
            source_code="fonte_teste",
            base_url="",
        )
