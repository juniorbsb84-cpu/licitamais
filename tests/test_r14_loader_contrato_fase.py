"""Fase vermelha TDD T14: remediacao loader contrato e fase orfa.

Cobre os dois defeitos da tarefa mais as regressoes achadas na revisao:
lote totalmente vazio nao pode commitar silencio e contrato orfao nao
pode entrar com process_id nulo.
"""

from __future__ import annotations

import sqlite3

import pytest

from licitamais.loader import append_contract, load_batch
from licitamais.schema import init_schema
from licitamais.types import ContractRecord, NormalizedBatch, PhaseRecord

AGORA = "2026-09-22T12:00:00Z"


@pytest.fixture()
def con() -> sqlite3.Connection:
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    conexao.execute(
        """
        INSERT INTO source (
            id, code, transport, base_url, adapter_version_atual
        ) VALUES (1, 'fonte-um', 'api_json', 'https://fonte.test', 'teste-1')
        """
    )
    conexao.executemany(
        """
        INSERT INTO source_run (
            id, source_id, "trigger", adapter_version, status, started_at
        ) VALUES (?, 1, 'manual', 'teste-1', 'ok', ?)
        """,
        [(1, AGORA), (2, AGORA), (3, AGORA), (4, AGORA), (5, AGORA)],
    )
    yield conexao
    conexao.close()


def contrato(native_id: str, valor_cents: int) -> ContractRecord:
    return ContractRecord(
        source_native_id=native_id,
        attrs={
            "value_cents": valor_cents,
            "supplier_name_raw": "Fornecedor",
        },
    )


def contrato_orfao(native_id: str, valor_cents: int, processo: str) -> ContractRecord:
    return ContractRecord(
        source_native_id=native_id,
        attrs={
            "value_cents": valor_cents,
            "supplier_name_raw": "Fornecedor",
            "process_native_id": processo,
        },
    )


def fase_orfa(process_native_id: str) -> PhaseRecord:
    return PhaseRecord(
        source_native_id=process_native_id,
        attrs={
            "phase_code": "aberto",
            "phase_label": "Aberto",
        },
    )


def lote_vazio_com_fases(fases: tuple) -> NormalizedBatch:
    return NormalizedBatch(
        orgs=(),
        processes=(),
        items=(),
        attachments=(),
        results=(),
        awards=(),
        contracts=(),
        phases=fases,
    )


def lote_com_contratos(contratos: tuple) -> NormalizedBatch:
    return NormalizedBatch(
        orgs=(),
        processes=(),
        items=(),
        attachments=(),
        results=(),
        awards=(),
        contracts=contratos,
        phases=(),
    )


def lote_totalmente_vazio() -> NormalizedBatch:
    return NormalizedBatch(
        orgs=(),
        processes=(),
        items=(),
        attachments=(),
        results=(),
        awards=(),
        contracts=(),
        phases=(),
    )


def test_prefixo_4_nao_casa_com_45(con: sqlite3.Connection) -> None:
    append_contract(con, 1, contrato("123:contrato:45", 45000))
    append_contract(con, 2, contrato("123:contrato:4", 4000))

    linhas = con.execute(
        """
        SELECT source_native_id, value_cents
        FROM contract
        ORDER BY id
        """
    ).fetchall()
    como_dict = [dict(linha) for linha in linhas]

    assert como_dict == [
        {"source_native_id": "123:contrato:45", "value_cents": 45000},
        {"source_native_id": "123:contrato:4", "value_cents": 4000},
    ]


def test_curinga_percent_e_underscore_nao_casam(con: sqlite3.Connection) -> None:
    append_contract(con, 1, contrato("WILDCARD100X200", 111))
    append_contract(con, 2, contrato("WILDCARD100%200", 222))
    append_contract(con, 3, contrato("AXB", 333))
    append_contract(con, 4, contrato("A_B", 444))

    linhas = con.execute(
        """
        SELECT source_native_id, value_cents
        FROM contract
        ORDER BY id
        """
    ).fetchall()
    como_dict = [dict(linha) for linha in linhas]

    assert como_dict == [
        {"source_native_id": "WILDCARD100X200", "value_cents": 111},
        {"source_native_id": "WILDCARD100%200", "value_cents": 222},
        {"source_native_id": "AXB", "value_cents": 333},
        {"source_native_id": "A_B", "value_cents": 444},
    ]


def test_retificacao_aponta_predecessor_exato_e_replay_nao_duplica(
    con: sqlite3.Connection,
) -> None:
    con.execute(
        """
        INSERT INTO contract (
            source_id, source_native_id, supplier_name_raw, value_cents,
            first_seen_run_id, last_seen_run_id, last_changed_run_id
        ) VALUES (1, '123:contrato:4', '', 10000, 1, 1, 1)
        """
    )
    con.execute(
        """
        INSERT INTO contract (
            source_id, source_native_id, supplier_name_raw, value_cents,
            first_seen_run_id, last_seen_run_id, last_changed_run_id
        ) VALUES (1, '123:contrato:45', '', 99999, 1, 1, 1)
        """
    )
    id_4 = con.execute(
        "SELECT id FROM contract WHERE source_id = 1 AND source_native_id = '123:contrato:4'"
    ).fetchone()["id"]

    novo_id = append_contract(con, 2, contrato("123:contrato:4", 20000))

    assert novo_id != id_4
    linha = con.execute(
        """
        SELECT source_native_id, value_cents, supersedes_id
        FROM contract
        WHERE id = ?
        """,
        (novo_id,),
    ).fetchone()
    assert dict(linha)["value_cents"] == 20000
    assert dict(linha)["supersedes_id"] == id_4
    assert dict(linha)["source_native_id"].startswith("123:contrato:4#retificacao-")

    repetido_id = append_contract(con, 3, contrato("123:contrato:4", 20000))
    assert repetido_id == novo_id
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 3


def test_fase_orfa_vai_para_quarentena(con: sqlite3.Connection) -> None:
    resultado = load_batch(
        con,
        1,
        1,
        lote_vazio_com_fases((fase_orfa("proc-inexistente"),)),
    )

    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM phase_event").fetchone()[0] == 0

    quarentena = con.execute(
        """
        SELECT source_id, source_run_id, native_ref, error
        FROM parse_quarantine
        """
    ).fetchall()
    assert len(quarentena) == 1
    linha = dict(quarentena[0])
    assert linha["source_id"] == 1
    assert linha["source_run_id"] == 1
    assert (linha["error"] or "").strip() != ""
    assert "proc-inexistente" in (linha["native_ref"] or "") or "proc-inexistente" in (linha["error"] or "")

    contadores = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1


def test_lote_totalmente_vazio_levanta_em_vez_de_commitar_silencio(
    con: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        load_batch(con, 1, 1, lote_totalmente_vazio())

    contadores = con.execute(
        """
        SELECT fetched_count, new_count, changed_count, unchanged_count,
               quarantined_count
        FROM source_run
        WHERE id = 1
        """
    ).fetchone()
    assert tuple(contadores) == (0, 0, 0, 0, 0)


def test_contrato_orfao_vai_para_quarentena(con: sqlite3.Connection) -> None:
    resultado = load_batch(
        con,
        1,
        1,
        lote_com_contratos((contrato_orfao("CTR-ORFAO", 5000, "proc-inexistente"),)),
    )

    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 0

    quarentena = con.execute(
        """
        SELECT source_id, source_run_id, native_ref, error
        FROM parse_quarantine
        """
    ).fetchall()
    assert len(quarentena) == 1
    linha = dict(quarentena[0])
    assert linha["source_id"] == 1
    assert linha["source_run_id"] == 1
    assert (linha["error"] or "").strip() != ""
    assert "CTR-ORFAO" in (linha["native_ref"] or "") or "CTR-ORFAO" in (linha["error"] or "")

    contadores = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1
