"""TDD R15: valores monetarios BR e quarentenas do loader."""

from __future__ import annotations

import sqlite3

import pytest

from licitamais.loader import _money_cents, load_batch
from licitamais.schema import init_schema
from licitamais.types import AwardRecord, ContractRecord, NormalizedBatch

AGORA = "2026-09-22T12:00:00Z"


@pytest.fixture()
def con() -> sqlite3.Connection:
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    conexao.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual)"
        " VALUES (1, 'fonte-um', 'api_json', 'https://fonte.test', 'teste-1')"
    )
    conexao.executemany(
        'INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at)'
        " VALUES (?, 1, 'manual', 'teste-1', 'ok', ?)",
        [(1, AGORA), (2, AGORA), (3, AGORA)],
    )
    conexao.execute(
        "INSERT INTO process (id, source_id, source_native_id, record_hash,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
        " VALUES (10, 1, 'proc-1', 'hash-a', 1, 1, 1)"
    )
    conexao.execute(
        "INSERT INTO result (id, source_id, source_native_id, process_id, type,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
        " VALUES (20, 1, 'resultado-1', 10, 'adjudicacao', 1, 1, 1)"
    )
    yield conexao
    conexao.close()


def lote(*, awards=(), contracts=()) -> NormalizedBatch:
    return NormalizedBatch(
        orgs=(),
        processes=(),
        items=(),
        attachments=(),
        results=(),
        awards=awards,
        contracts=contracts,
        phases=(),
    )


def premio(native_id: str, attrs: dict) -> AwardRecord:
    return AwardRecord(source_native_id=native_id, attrs=attrs)


def contrato(native_id: str, attrs: dict) -> ContractRecord:
    return ContractRecord(source_native_id=native_id, attrs=attrs)


def erros(con: sqlite3.Connection) -> list[dict]:
    return [
        dict(linha) for linha in con.execute("SELECT native_ref, error FROM parse_quarantine ORDER BY id").fetchall()
    ]


def test_milhar_br_sem_centavos() -> None:
    assert _money_cents("1.234") == 123400
    assert _money_cents("R$ 12.500") == 1250000
    assert _money_cents("1.234.567") == 123456700


def test_mantem_virgula_decimal() -> None:
    assert _money_cents("1.234,56") == 123456
    assert _money_cents("10,5") == 1050


def test_ponto_com_um_ou_dois_digitos_e_decimal() -> None:
    assert _money_cents("12.5") == 1250
    assert _money_cents("12.50") == 1250


def test_vazio_e_nulo_dao_none() -> None:
    assert _money_cents(None) is None
    assert _money_cents("") is None
    assert _money_cents("   ") is None


def test_ilegivel_dao_none() -> None:
    assert _money_cents("N/D") is None


A_ILEGIVEL = {
    "result_id": 20,
    "process_id": 10,
    "supplier_name_raw": "Fornecedor",
    "amount_raw": "N/D",
}

C_ILEGIVEL = {
    "process_native_id": "proc-1",
    "value_raw": "N/D",
    "supplier_name_raw": "Fornecedor",
}


def test_award_ilegivel_quarentena(con: sqlite3.Connection) -> None:
    entrada = lote(awards=(premio("awar-1", A_ILEGIVEL),))
    resultado = load_batch(con, 1, 1, entrada)
    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 0
    linhas = erros(con)
    assert len(linhas) == 1
    assert "ilegivel" in (linhas[0]["error"] or "")
    assert "awar-1" in (linhas[0]["native_ref"] or "") or "awar-1" in (linhas[0]["error"] or "")
    n = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(n)["quarantined_count"] == 1


def test_contract_ilegivel_quarentena(con: sqlite3.Connection) -> None:
    entrada = lote(contracts=(contrato("CTR-1", C_ILEGIVEL),))
    resultado = load_batch(con, 1, 1, entrada)
    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 0
    linhas = erros(con)
    assert len(linhas) == 1
    assert "ilegivel" in (linhas[0]["error"] or "")


C_SEM_REF = {"value_cents": 5000, "supplier_name_raw": "Fornecedor"}

C_OK = {"process_native_id": "proc-1", "value_cents": 5000, "supplier_name_raw": "Fornecedor"}


def test_sem_ref_quarentena(con: sqlite3.Connection) -> None:
    entrada = lote(contracts=(contrato("CTR-SEM-REF", C_SEM_REF),))
    resultado = load_batch(con, 1, 1, entrada)
    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 0
    linhas = erros(con)
    assert len(linhas) == 1
    assert "sem referencia de processo" in (linhas[0]["error"] or "")
    n = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(n)["quarantined_count"] == 1


def test_com_ref_persiste(con: sqlite3.Connection) -> None:
    entrada = lote(contracts=(contrato("CTR-OK", C_OK),))
    resultado = load_batch(con, 1, 1, entrada)
    assert resultado.quarantined == 0
    linha = con.execute("SELECT process_id, value_cents FROM contract WHERE source_native_id = 'CTR-OK'").fetchone()
    assert dict(linha) == {"process_id": 10, "value_cents": 5000}


C_MILHAR = {
    "process_native_id": "proc-1",
    "value_raw": "R$ 12.500",
    "supplier_name_raw": "Fornecedor",
}


def test_milhar_persiste_correto(con: sqlite3.Connection) -> None:
    entrada = lote(contracts=(contrato("CTR-MILHAR", C_MILHAR),))
    resultado = load_batch(con, 1, 1, entrada)
    assert resultado.quarantined == 0
    linha = con.execute("SELECT value_cents FROM contract WHERE source_native_id = 'CTR-MILHAR'").fetchone()
    assert dict(linha)["value_cents"] == 1250000
