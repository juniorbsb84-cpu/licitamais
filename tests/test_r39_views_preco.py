"""R39: inteligencia de preco em SQL (views da migracao 004)."""

import json
import pathlib
import sqlite3

from licitamais import current_version, init_schema

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"


def _con_novo():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    init_schema(con)
    return con


def _propostas(*valores):
    return json.dumps([{"nome": f"P{i}", "cnpj": None, "valor": v, "vencedor": False} for i, v in enumerate(valores)])


def _seed(con):
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual)"
        " VALUES (1, 'sistema_industria', 'api_json', 'https://x', 'v1'),"
        " (2, 'brb', 'api_json', 'https://y', 'v1')"
    )
    con.executemany(
        "INSERT INTO organization (id, cnpj, name_raw, name_norm, kind_hint) VALUES (?, ?, ?, ?, ?)",
        [
            (1, None, "SESI COMPRADOR", "sesi comprador", "comprador"),
            (2, None, "SENAI COMPRADOR", "senai comprador", "comprador"),
            (3, "11111111000111", "ALFA LTDA", "alfa ltda", "fornecedor"),
            (4, "22222222000222", "BETA SA", "beta sa", "fornecedor"),
            (5, None, "GAMA ME", "gama me", "fornecedor"),
            (6, "33333333000133", "DELTA EPP", "delta epp", "fornecedor"),
        ],
    )
    con.executemany(
        "INSERT INTO process (id, source_id, source_native_id, org_id, number, year) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "ED1", 1, "000004/2026", 2026),
            (2, 1, "ED2", 1, "000030/2026", 2026),
            (3, 2, "LIC1", 2, "057/2026", 2026),
            (4, 1, "ED3", None, "000100/2026", 2026),
        ],
    )


def _con_seed():
    con = _con_novo()
    _seed(con)
    con.executemany(
        "INSERT INTO result (id, source_id, source_native_id, process_id, type, attrs)"
        " VALUES (?, ?, ?, ?, 'adjudicacao', ?)",
        [
            (1, 1, "ED1:lote:49585", 1, json.dumps({"propostas": json.loads(_propostas(12000000.0, 13000000.0))})),
            (2, 1, "ED2:lote:46579", 2, json.dumps({"propostas": json.loads(_propostas(1300000.0, 1500000.0))})),
            (3, 2, "LIC1:contrato", 3, json.dumps({"propostas": []})),
            (4, 1, "ED3:vazio", 4, None),
        ],
    )
    con.executemany(
        "INSERT INTO award (id, result_id, process_id, supplier_org_id,"
        " supplier_name_raw, amount_cents, attrs)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (2, 2, 2, 4, "BETA SA", 115000000, json.dumps({"supplier_cnpj": "22222222000222"})),
            (3, 1, 1, 3, "ALFA LTDA-X", 1065595709, json.dumps({"supplier_cnpj": "11111111000111"})),
            (4, 3, 3, 3, "ALFA LTDA", 5000000, json.dumps({"supplier_cnpj": "11111111000111"})),
            (5, 3, 3, None, "GAMA ME", 7000000, json.dumps({})),
        ],
    )
    con.execute(
        "INSERT INTO award (id, result_id, process_id, item_id, supplier_org_id, supplier_name_raw, amount_cents, supersedes_id, attrs) VALUES (1, 1, 1, 7, 3, 'ALFA LTDA', 1065595709, 3, '{}')"
    )
    return con


def test_migracao_004_cria_as_quatro_views():
    con = _con_novo()
    vistas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
    assert {"v_vencedor_por_orgao", "v_desconto_disputa", "v_concorrencia", "v_fornecedor_ranking"} <= vistas
    assert current_version(con) == 10
    assert con.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 10


def test_vencedor_por_orgao_soma_e_conta_vigentes():
    con = _con_seed()
    linhas = con.execute(
        "SELECT fonte, comprador, fornecedor, fornecedor_cnpj, vitorias, valor_total FROM v_vencedor_por_orgao ORDER BY fornecedor, fonte"
    ).fetchall()
    assert [tuple(linha) for linha in linhas] == [
        # award 3 ("ALFA LTDA-X") foi retificado pelo award 1 ("ALFA LTDA"): vale o vigente
        ("brb", "SENAI COMPRADOR", "ALFA LTDA", "11111111000111", 1, 50000.0),
        ("sistema_industria", "SESI COMPRADOR", "ALFA LTDA", "11111111000111", 1, 10655957.09),
        ("sistema_industria", "SESI COMPRADOR", "BETA SA", "22222222000222", 1, 1150000.0),
        ("brb", "SENAI COMPRADOR", "GAMA ME", None, 1, 70000.0),
    ]
