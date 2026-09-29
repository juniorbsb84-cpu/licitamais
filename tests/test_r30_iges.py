"""R30 TDD: adaptador IGES-DF contra amostras reais (indice wp-json + CSV)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from licitamais.adapters.iges import (
    CAMPOS_SONDA,
    INDICE_ENDPOINT,
    INDICE_PARAMS,
    IgesAdapter,
)
from licitamais.loader import load_batch
from licitamais.runner import open_source_run
from licitamais.schema import init_schema
from licitamais.types import FetchedPage, PlanContext

AMOSTRAS = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "iges"
INDICE_NOME = "indice_contrat.json"
CSV_NOME = "08.2026-CONTRATACOES.csv"
CSV_ENDPOINT = "/wp-content/uploads/2026/09/08.2026-CONTRATACOES.csv"


def _contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=1,
    )


def _pagina(corpo: bytes, pedido, tipo: str) -> FetchedPage:
    return FetchedPage(
        request=pedido,
        status=200,
        headers={"content-type": tipo},
        body=corpo,
        fetched_at="2026-09-24T12:00:00Z",
        duration_ms=1,
    )


def _pedido_indice():
    (pedido,) = tuple(IgesAdapter().plan(_contexto()))
    return pedido


def _resultado_csv():
    adapter = IgesAdapter()
    pedido = _pedido_indice()
    indice = adapter.parse(
        _pagina(
            (AMOSTRAS / INDICE_NOME).read_bytes(),
            pedido,
            "application/json; charset=UTF-8",
        )
    )
    assert indice.fatal is None
    (pedido_csv,) = indice.next
    return adapter.parse(_pagina((AMOSTRAS / CSV_NOME).read_bytes(), pedido_csv, "text/csv"))


def test_plan_descoberta_emite_um_get_para_o_indice_wp_json() -> None:
    pedido = _pedido_indice()

    assert pedido.method == "GET"
    assert pedido.endpoint == INDICE_ENDPOINT == "/wp-json/wp/v2/media"
    assert dict(pedido.params) == dict(INDICE_PARAMS)
    assert pedido.phase == "discover"
    assert IgesAdapter().capabilities.hydration["enabled"] is False
    assert CAMPOS_SONDA


def test_campos_sonda_presentes_em_todos_os_registros_do_indice() -> None:
    itens = json.loads((AMOSTRAS / INDICE_NOME).read_bytes().decode("utf-8"))

    assert len(itens) == 50
    for item in itens:
        for campo in CAMPOS_SONDA:
            assert item.get(campo)


def test_parse_do_indice_real_aponta_para_o_csv_mais_recente_via_next() -> None:
    adapter = IgesAdapter()
    pedido = _pedido_indice()
    bruto = (AMOSTRAS / INDICE_NOME).read_bytes()

    resultado = adapter.parse(_pagina(bruto, pedido, "application/json; charset=UTF-8"))

    assert resultado.fatal is None
    assert resultado.batch.processes == ()
    assert resultado.batch.contracts == ()
    assert resultado.signals["row_count_declared"] == 50
    (proximo,) = resultado.next
    assert proximo.method == "GET"
    assert proximo.endpoint == CSV_ENDPOINT
    assert proximo.phase == "discover"


def test_parse_do_csv_real_inteiro_88_contratos_70_processos_quarentena_zero() -> None:
    resultado = _resultado_csv()

    assert resultado.fatal is None
    assert resultado.quarantine == ()
    assert resultado.signals["row_count_declared"] == 88
    assert len(resultado.batch.contracts) == 88
    assert len(resultado.batch.processes) == 70
    pais = {p.source_native_id for p in resultado.batch.processes}
    assert {c.attrs["process_native_id"] for c in resultado.batch.contracts} <= pais
    assert all(c.attrs["supplier_cnpj"] for c in resultado.batch.contracts)
    assert all(c.attrs["supplier_name_raw"] for c in resultado.batch.contracts)


def test_primeira_linha_ancoras_valor_cnpj_e_vigencia() -> None:
    resultado = _resultado_csv()

    alvo = next(
        c for c in resultado.batch.contracts if c.source_native_id == "iges:04016-00086460/2026-46:CONTRATO:125/2026"
    )
    assert alvo.attrs["number"] == "125/2026"
    assert alvo.attrs["supplier_name_raw"] == "MEDSYSTEM EQUIPAMENTOS MEDICOS LTDA"
    assert alvo.attrs["supplier_cnpj"] == "06.189.855/0001-99"
    assert alvo.attrs["value_raw"] == "R$ 4.167,20"
    assert alvo.attrs["value_cents"] == 416720
    assert alvo.attrs["signed_at_source"] == "2026-08-03"
    assert alvo.attrs["vigency_start"] == "2026-08-03"
    assert alvo.attrs["vigency_end"] == "2027-08-03"
    assert alvo.attrs["process_native_id"] == "iges:04016-00086460/2026-46"

    pai = next(p for p in resultado.batch.processes if p.source_native_id == "iges:04016-00086460/2026-46")
    assert pai.attrs["number"] == "04016-00086460/2026-46"


def test_valor_placeholder_vira_nulo_sem_quarentena_e_numero_vazio_tem_id_estavel() -> None:
    resultado = _resultado_csv()

    nulos = [c for c in resultado.batch.contracts if c.attrs["value_raw"] is None]
    assert len(nulos) == 23
    assert all(c.attrs["value_cents"] is None for c in nulos)

    sem_numero = [c for c in resultado.batch.contracts if not c.attrs["number"]]
    assert len(sem_numero) == 1
    assert sem_numero[0].source_native_id == "iges:04016-00041497/2026-45:TERMO DE COMPROMISSO:"


def test_loader_grava_csv_inteiro_sem_quarentena_e_liga_org_por_cnpj(tmp_path) -> None:
    resultado = _resultado_csv()

    assert resultado.fatal is None
    assert resultado.quarantine == ()

    con = sqlite3.connect(tmp_path / "iges_real.db")
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)
        source_id = con.execute(
            "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
            "VALUES ('iges', 'wp_json', 'https://igesdf.org.br', '0.1.0')"
        ).lastrowid
        con.commit()
        run_id = open_source_run(con, source_id, "manual", "0.1.0")
        carga = load_batch(con, run_id, source_id, resultado.batch)
        con.commit()

        assert carga.quarantined == 0
        assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 70
        assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 88
        assert con.execute("SELECT COUNT(*) FROM parse_quarantine").fetchone()[0] == 0
        linha = con.execute(
            "SELECT number, supplier_name_raw, value_cents FROM contract "
            "WHERE source_native_id = 'iges:04016-00086460/2026-46:CONTRATO:125/2026'"
        ).fetchone()
        assert tuple(linha) == (
            "125/2026",
            "MEDSYSTEM EQUIPAMENTOS MEDICOS LTDA",
            416720,
        )
        orgs = con.execute("SELECT cnpj FROM organization").fetchall()
        assert "06189855000199" in {linha_org[0] for linha_org in orgs}
    finally:
        con.close()


def test_cadeia_tls_inclui_intermediario_sectigo_e_mantem_verificacao():
    """r30b: servidor omite o intermediario; sem ele requests falha com CERTIFICATE_VERIFY_FAILED."""
    from licitamais.adapters.iges import IgesAdapter, cadeia_certificados

    texto = open(cadeia_certificados(), encoding="ascii").read()
    assert "BEGIN CERTIFICATE" in texto
    assert texto.count("BEGIN CERTIFICATE") > 100  # raizes do certifi + intermediario
    sessao = IgesAdapter().open(type("Cfg", (), {"base_url": "https://igesdf.org.br"})())
    verify = sessao.dados["http"].verify
    assert verify not in (False, None) and str(verify).endswith(".pem")
