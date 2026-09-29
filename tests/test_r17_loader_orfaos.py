"""Fase vermelha TDD T17: remediacao loader orfaos e mudanca real.

(1) item, attachment, result e award cujo processo/resultado pai nao existe
devem ir para parse_quarantine via quarantine_record com motivo explicito e
somar em LoadResult.quarantined, nunca persistir com FK nula em silencio.
(2) append_contract e append_award comparam so value_cents/amount_cents e
perdem mudanca real: qualquer coluna de negocio alterada deve gerar
retificacao (supersedes_id).
(3) increment_run_counts so conta batch.processes: lote so com colecoes
especificas deve contar os registros efetivamente gravados no contador new
do source_run.
(4) Regressao achada na revisao: registros sem referencia de processo
(process_native_id ausente/None) eram inseridos com process_id NULL contado
como new em vez de quarentena.
"""

from __future__ import annotations

import sqlite3

import pytest

from licitamais.loader import append_award, append_contract, load_batch
from licitamais.schema import init_schema
from licitamais.types import (
    AttachmentRecord,
    AwardRecord,
    ContractRecord,
    ItemRecord,
    NormalizedBatch,
    ResultRecord,
)

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
        [(1, AGORA), (2, AGORA), (3, AGORA), (4, AGORA), (5, AGORA), (6, AGORA)],
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


def lote(
    *,
    items: tuple = (),
    attachments: tuple = (),
    results: tuple = (),
    awards: tuple = (),
    contracts: tuple = (),
) -> NormalizedBatch:
    return NormalizedBatch(
        orgs=(),
        processes=(),
        items=items,
        attachments=attachments,
        results=results,
        awards=awards,
        contracts=contracts,
        phases=(),
    )


def quarentena(con: sqlite3.Connection) -> list[dict]:
    return [
        dict(linha)
        for linha in con.execute(
            "SELECT source_id, source_run_id, native_ref, error FROM parse_quarantine ORDER BY id"
        ).fetchall()
    ]


def test_item_orfao_vai_para_quarentena(con: sqlite3.Connection) -> None:
    item = ItemRecord(
        source_native_id="item-orfao-1",
        attrs={
            "process_native_id": "proc-inexistente",
            "description": "Item sem pai",
            "qty": 2,
        },
    )

    resultado = load_batch(con, 1, 1, lote(items=(item,)))

    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert linhas[0]["source_id"] == 1
    assert linhas[0]["source_run_id"] == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "item-orfao-1" in (linhas[0]["native_ref"] or "") or "item-orfao-1" in (linhas[0]["error"] or "")
    contadores = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1


def test_item_sem_referencia_de_processo_vai_para_quarentena(
    con: sqlite3.Connection,
) -> None:
    item = ItemRecord(
        source_native_id="item-sem-proc-ref",
        attrs={
            "description": "Item sem referencia de processo",
            "qty": 2,
        },
    )

    resultado = load_batch(con, 1, 1, lote(items=(item,)))

    assert resultado.quarantined == 1
    assert resultado.new == 0
    assert con.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert linhas[0]["source_id"] == 1
    assert linhas[0]["source_run_id"] == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "item-sem-proc-ref" in (linhas[0]["native_ref"] or "") or "item-sem-proc-ref" in (linhas[0]["error"] or "")
    contadores = con.execute("SELECT new_count, quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1
    assert dict(contadores)["new_count"] == 0


def test_attachment_orfao_vai_para_quarentena(con: sqlite3.Connection) -> None:
    att = AttachmentRecord(
        source_native_id="att-orfao-1",
        attrs={
            "process_native_id": "proc-inexistente",
            "kind": "edital",
            "url_download": "https://fonte.test/edital.pdf",
        },
    )

    resultado = load_batch(con, 1, 1, lote(attachments=(att,)))

    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM attachment").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert linhas[0]["source_id"] == 1
    assert linhas[0]["source_run_id"] == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "att-orfao-1" in (linhas[0]["native_ref"] or "") or "att-orfao-1" in (linhas[0]["error"] or "")
    contadores = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1


def test_attachment_sem_referencia_de_processo_vai_para_quarentena(
    con: sqlite3.Connection,
) -> None:
    att = AttachmentRecord(
        source_native_id="att-sem-proc-ref",
        attrs={
            "kind": "edital",
            "url_download": "https://fonte.test/edital.pdf",
        },
    )

    resultado = load_batch(con, 1, 1, lote(attachments=(att,)))

    assert resultado.quarantined == 1
    assert resultado.new == 0
    assert con.execute("SELECT COUNT(*) FROM attachment").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "att-sem-proc-ref" in (linhas[0]["native_ref"] or "") or "att-sem-proc-ref" in (linhas[0]["error"] or "")
    contadores = con.execute("SELECT new_count, quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1
    assert dict(contadores)["new_count"] == 0


def test_result_orfao_vai_para_quarentena(con: sqlite3.Connection) -> None:
    res = ResultRecord(
        source_native_id="res-orfao-1",
        attrs={
            "process_native_id": "proc-inexistente",
            "type": "adjudicacao",
        },
    )

    resultado = load_batch(con, 1, 1, lote(results=(res,)))

    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM result WHERE source_native_id = 'res-orfao-1'").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "res-orfao-1" in (linhas[0]["native_ref"] or "") or "res-orfao-1" in (linhas[0]["error"] or "")


def test_result_sem_referencia_de_processo_vai_para_quarentena(
    con: sqlite3.Connection,
) -> None:
    res = ResultRecord(
        source_native_id="res-sem-proc-ref",
        attrs={
            "type": "adjudicacao",
        },
    )

    resultado = load_batch(con, 1, 1, lote(results=(res,)))

    assert resultado.quarantined == 1
    assert resultado.new == 0
    assert con.execute("SELECT COUNT(*) FROM result WHERE source_native_id = 'res-sem-proc-ref'").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "res-sem-proc-ref" in (linhas[0]["native_ref"] or "") or "res-sem-proc-ref" in (linhas[0]["error"] or "")
    contadores = con.execute("SELECT new_count, quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1
    assert dict(contadores)["new_count"] == 0


def test_award_orfao_vai_para_quarentena(con: sqlite3.Connection) -> None:
    award = AwardRecord(
        source_native_id="award-orfao-1",
        attrs={
            "result_native_id": "resultado-inexistente",
            "process_native_id": "proc-1",
            "supplier_name_raw": "Fornecedor",
            "amount_cents": 1000,
        },
    )

    resultado = load_batch(con, 1, 1, lote(awards=(award,)))

    assert resultado.quarantined == 1
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert linhas[0]["source_id"] == 1
    assert linhas[0]["source_run_id"] == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "award-orfao-1" in (linhas[0]["native_ref"] or "") or "award-orfao-1" in (linhas[0]["error"] or "")
    contadores = con.execute("SELECT quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1


def test_award_sem_referencia_de_processo_vai_para_quarentena(
    con: sqlite3.Connection,
) -> None:
    award = AwardRecord(
        source_native_id="award-sem-process-ref",
        attrs={
            "result_id": 20,
            "supplier_name_raw": "Fornecedor",
            "amount_cents": 1000,
        },
    )

    resultado = load_batch(con, 1, 1, lote(awards=(award,)))

    assert resultado.quarantined == 1
    assert resultado.new == 0
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 0
    linhas = quarentena(con)
    assert len(linhas) == 1
    assert (linhas[0]["error"] or "").strip() != ""
    assert "award-sem-process-ref" in (linhas[0]["native_ref"] or "") or "award-sem-process-ref" in (
        linhas[0]["error"] or ""
    )
    contadores = con.execute("SELECT new_count, quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 1
    assert dict(contadores)["new_count"] == 0


def test_contrato_mesmo_valor_vigency_end_diferente_gera_retificacao(
    con: sqlite3.Connection,
) -> None:
    base = ContractRecord(
        source_native_id="CTR-RET",
        attrs={
            "process_native_id": "proc-1",
            "value_cents": 50000,
            "supplier_name_raw": "Fornecedor",
            "vigency_end": "2026-01-01",
        },
    )
    alterado = ContractRecord(
        source_native_id="CTR-RET",
        attrs={
            "process_native_id": "proc-1",
            "value_cents": 50000,
            "supplier_name_raw": "Fornecedor",
            "vigency_end": "2026-06-30",
        },
    )

    primeiro_id = append_contract(con, 1, base)
    novo_id = append_contract(con, 2, alterado)

    assert novo_id != primeiro_id
    linha = con.execute(
        "SELECT source_native_id, value_cents, vigency_end, supersedes_id FROM contract WHERE id = ?",
        (novo_id,),
    ).fetchone()
    assert dict(linha)["value_cents"] == 50000
    assert dict(linha)["vigency_end"] == "2026-06-30"
    assert dict(linha)["supersedes_id"] == primeiro_id
    assert dict(linha)["source_native_id"].startswith("CTR-RET#retificacao-")
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 2


def test_contrato_mesmo_valor_fornecedor_diferente_gera_retificacao(
    con: sqlite3.Connection,
) -> None:
    base = ContractRecord(
        source_native_id="CTR-SUPP",
        attrs={
            "process_native_id": "proc-1",
            "value_cents": 50000,
            "supplier_name_raw": "Fornecedor Antigo",
            "vigency_end": "2026-01-01",
        },
    )
    alterado = ContractRecord(
        source_native_id="CTR-SUPP",
        attrs={
            "process_native_id": "proc-1",
            "value_cents": 50000,
            "supplier_name_raw": "Fornecedor Novo",
            "vigency_end": "2026-01-01",
        },
    )

    primeiro_id = append_contract(con, 1, base)
    novo_id = append_contract(con, 2, alterado)

    assert novo_id != primeiro_id
    linha = con.execute(
        "SELECT source_native_id, value_cents, supplier_name_raw, supersedes_id FROM contract WHERE id = ?",
        (novo_id,),
    ).fetchone()
    assert dict(linha)["value_cents"] == 50000
    assert dict(linha)["supplier_name_raw"] == "Fornecedor Novo"
    assert dict(linha)["supersedes_id"] == primeiro_id
    assert dict(linha)["source_native_id"].startswith("CTR-SUPP#retificacao-")
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 2


def test_award_mesmo_valor_qty_diferente_gera_nova_versao_e_replay_nao_duplica(
    con: sqlite3.Connection,
) -> None:
    base = AwardRecord(
        source_native_id="award-qty-1",
        attrs={
            "result_id": 20,
            "process_id": 10,
            "supplier_name_raw": "Fornecedor",
            "amount_cents": 10000,
            "qty_awarded": 1,
        },
    )
    alterado = AwardRecord(
        source_native_id="award-qty-1",
        attrs={
            "result_id": 20,
            "process_id": 10,
            "supplier_name_raw": "Fornecedor",
            "amount_cents": 10000,
            "qty_awarded": 5,
        },
    )

    primeiro_id = append_award(con, 1, base)
    novo_id = append_award(con, 2, alterado)

    assert novo_id != primeiro_id
    linha = con.execute(
        "SELECT amount_cents, qty_awarded, supersedes_id FROM award WHERE id = ?",
        (novo_id,),
    ).fetchone()
    assert dict(linha)["amount_cents"] == 10000
    assert dict(linha)["qty_awarded"] == 5
    assert dict(linha)["supersedes_id"] == primeiro_id
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 2

    repetido_id = append_award(con, 3, alterado)
    assert repetido_id == novo_id
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 2


def test_lote_so_contratos_conta_new_no_source_run(
    con: sqlite3.Connection,
) -> None:
    contratos = (
        ContractRecord(
            source_native_id="CTR-NEW-1",
            attrs={
                "process_native_id": "proc-1",
                "value_cents": 1000,
                "supplier_name_raw": "Fornecedor",
            },
        ),
        ContractRecord(
            source_native_id="CTR-NEW-2",
            attrs={
                "process_native_id": "proc-1",
                "value_cents": 2000,
                "supplier_name_raw": "Fornecedor",
            },
        ),
    )

    resultado = load_batch(con, 1, 1, lote(contracts=contratos))

    assert resultado.quarantined == 0
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 2
    contadores = con.execute("SELECT new_count, quarantined_count FROM source_run WHERE id = 1").fetchone()
    assert dict(contadores)["quarantined_count"] == 0
    assert dict(contadores)["new_count"] == 2
