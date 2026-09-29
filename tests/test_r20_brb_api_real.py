"""Fase vermelha TDD T20: adapter BRB contra a API real /api/licitacao."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from licitamais.adapters.brb import _CAMPOS_OBRIGATORIOS, BRBAdapter
from licitamais.loader import load_batch
from licitamais.runner import open_source_run
from licitamais.schema import init_schema
from licitamais.types import FetchedPage, FetchRequest, PlanContext

FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "brb_api_licitacao_2026-09-23.json"


def _contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=1,
    )


def _pedido_licitacao() -> FetchRequest:
    return FetchRequest(
        endpoint="/api/licitacao",
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _pagina(corpo: bytes) -> FetchedPage:
    return FetchedPage(
        request=_pedido_licitacao(),
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=corpo,
        fetched_at="2026-09-23T12:00:00Z",
        duration_ms=1,
    )


def _registros_fixture() -> list[dict]:
    return json.loads(FIXTURE.read_bytes().decode("utf-8"))


def test_plan_descoberta_emite_um_get_para_api_licitacao() -> None:
    pedidos = tuple(BRBAdapter().plan(_contexto()))

    assert len(pedidos) == 1
    assert pedidos[0].method == "GET"
    assert pedidos[0].endpoint == "/api/licitacao"
    assert all(pedido.endpoint != "/api/processos" for pedido in pedidos)


def test_parse_da_fixture_golden_gera_cinco_processos_mapeados() -> None:
    bruto = FIXTURE.read_bytes()
    registros = json.loads(bruto.decode("utf-8"))
    assert len(registros) == 5

    resultado = BRBAdapter().parse(_pagina(bruto))

    assert resultado.fatal is None
    assert len(resultado.batch.processes) == 5
    esperados = {str(registro["id"]): registro for registro in registros}
    assert {p.source_native_id for p in resultado.batch.processes} == set(esperados)
    for processo in resultado.batch.processes:
        registro = esperados[processo.source_native_id]
        assert processo.attrs["object"] == registro["objeto"]
        assert str(processo.attrs["number"]) == str(registro["numero"])
        assert processo.attrs["year"] == registro["ano"]
        assert processo.attrs["title"] == registro["titulo"]
        assert processo.attrs["opening_at_source"] == registro["realizacao"]


def test_anexos_e_contratos_embutidos_viram_registros_sem_request_extra() -> None:
    registros = _registros_fixture()
    n_anexos = sum(len(registro["anexos"] or []) for registro in registros)
    n_contratos = sum(len(registro["contratos"] or []) for registro in registros)
    assert n_contratos == 1

    resultado = BRBAdapter().parse(_pagina(FIXTURE.read_bytes()))

    assert resultado.fatal is None
    assert len(resultado.batch.attachments) == n_anexos
    assert resultado.next == ()
    # 2026-09-23 (orquestrador): a API real traz o contrato ESQUELETO na
    # listagem (tudo null salvo id/numero). Gravar esse esqueleto enshrinava
    # valor NULL. Ele nao vira contrato; o id vai para a hidratacao
    # (/api/contrato/{id}, tests/test_r20c_brb_contrato_detalhe.py).
    assert resultado.batch.contracts == ()
    ids = [i for p in resultado.batch.processes for i in p.attrs["contratos_ids"]]
    assert len(ids) == n_contratos


def test_payload_nao_lista_ou_lista_vazia_e_fatal() -> None:
    adapter = BRBAdapter()

    vazio = adapter.parse(_pagina(b"[]"))
    assert vazio.fatal
    assert vazio.batch.processes == ()

    legado = adapter.parse(_pagina(json.dumps({"registros": []}).encode("utf-8")))
    assert legado.fatal
    assert legado.batch.processes == ()


def test_campos_obrigatorios_somente_chaves_presentes_na_fixture() -> None:
    registros = _registros_fixture()
    presentes_em_todos = set(registros[0])
    for registro in registros[1:]:
        presentes_em_todos &= set(registro)

    assert _CAMPOS_OBRIGATORIOS
    assert set(_CAMPOS_OBRIGATORIOS) <= presentes_em_todos


def test_loader_grava_cinco_processos_da_fixture_sem_quarentena(tmp_path) -> None:
    registros = _registros_fixture()
    resultado = BRBAdapter().parse(_pagina(FIXTURE.read_bytes()))

    assert resultado.fatal is None
    assert resultado.quarantine == ()

    con = sqlite3.connect(tmp_path / "brb_real.db")
    try:
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)
        source_id = con.execute(
            "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
            "VALUES ('brb', 'api_json', 'https://pdd.brb.com.br/PLC', '0.1.0')"
        ).lastrowid
        con.commit()
        run_id = open_source_run(con, source_id, "manual", "0.1.0")
        load_batch(con, run_id, source_id, resultado.batch)
        con.commit()

        linhas = con.execute("SELECT source_native_id, number, year, object FROM process").fetchall()
        assert len(linhas) == 5
        esperados = {str(registro["id"]): registro for registro in registros}
        for native_id, numero, ano, objeto in linhas:
            assert esperados[native_id]["objeto"] == objeto
            assert str(esperados[native_id]["numero"]) == str(numero)
            assert esperados[native_id]["ano"] == ano
        assert con.execute("SELECT COUNT(*) FROM parse_quarantine").fetchone()[0] == 0
    finally:
        con.close()
