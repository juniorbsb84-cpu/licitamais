"""R28: fornecedor sem supplier_org_id ficava solto (organization vazia no banco real,
3.197 contratos BRB e 257 vencedores SI). BRB nao publica CNPJ: liga pelo nome normalizado."""

import sqlite3

from licitamais.loader import load_batch
from licitamais.runner import open_source_run
from licitamais.schema import init_schema
from licitamais.types import ContractRecord, NormalizedBatch, ProcessRecord


def test_contrato_sem_cnpj_liga_fornecedor_pelo_nome():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    sid = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) VALUES ('brb', 'api_json', 'https://x.test', 'v1')"
    ).lastrowid
    run = open_source_run(con, sid, "manual", "v1")
    vazio = dict(orgs=(), processes=(), items=(), attachments=(), results=(), awards=(), contracts=(), phases=())
    load_batch(con, run, sid, NormalizedBatch(**{**vazio, "processes": (ProcessRecord("p1", {"number": "1"}),)}))
    contratos = tuple(
        ContractRecord(
            f"c{i}", {"process_native_id": "p1", "number": str(i), "supplier_name_raw": nome, "value_cents": 100}
        )
        for i, nome in enumerate(("Rivera Móveis", "RIVERA MOVEIS"))
    )
    load_batch(con, run, sid, NormalizedBatch(**{**vazio, "contracts": contratos}))
    orgs = {r[0] for r in con.execute("SELECT supplier_org_id FROM contract")}
    assert len(orgs) == 1 and None not in orgs
    assert con.execute("SELECT COUNT(*) FROM organization").fetchone()[0] == 1
    con.close()
