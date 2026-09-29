"""M5: consulta de historico de preco responde so do banco, com proveniencia."""

import sqlite3

from licitamais.schema import init_schema


def _con():
    con = sqlite3.connect(":memory:")
    init_schema(con)
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual) VALUES (1,'brb','api_json','x','v1')"
    )
    con.execute(
        "INSERT INTO source_run (id, source_id, \"trigger\", adapter_version, status, started_at) VALUES (1,1,'manual','v1','ok','2026-09-23')"
    )
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, number, year, object) VALUES (1,1,'2367','057',2026,'mobiliario')"
    )
    for cid, valor, sup in ((1, 3199649, None), (2, 5000000, 1)):
        con.execute(
            "INSERT INTO contract (id, source_id, source_native_id, process_id, number, supplier_name_raw,"
            " value_cents, vigency_start, supersedes_id, first_seen_run_id, last_seen_run_id, last_changed_run_id, attrs)"
            " VALUES (?,1,?,1,'450','RIVERA MOVEIS',?,'2026-09-02',?,1,1,1,'{\"object\": \"Fornecimento de mobiliario\"}')",
            (cid, f"2367:contrato:{cid}", valor, sup),
        )
    return con


def test_preco_contrato_mostra_so_versao_vigente_com_proveniencia():
    linhas = (
        _con()
        .execute(
            "SELECT fonte, objeto, fornecedor, valor, visto_no_run FROM v_preco_contrato"
            " WHERE objeto LIKE '%mobiliario%'"
        )
        .fetchall()
    )
    assert linhas == [("brb", "Fornecimento de mobiliario", "RIVERA MOVEIS", 50000.0, 1)]


def test_resumo_por_fornecedor():
    r = (
        _con()
        .execute("SELECT contratos, valor_total FROM v_fornecedor_resumo WHERE fornecedor='RIVERA MOVEIS'")
        .fetchone()
    )
    assert r == (1, 50000.0)
