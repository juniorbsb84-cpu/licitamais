"""Persistencia real dos lotes gerados pelos adaptadores, sem rede."""

import json
import sqlite3
from pathlib import Path

from licitamais.adapters.sistema_industria import SistemaIndustriaAdapter, listar_vencedores_request
from licitamais.loader import load_batch
from licitamais.runner import open_source_run
from licitamais.schema import init_schema
from licitamais.types import AttachmentRecord, FetchedPage, FetchRequest, ItemRecord, NormalizedBatch

GOLDEN = Path(__file__).parent / "golden" / "brb" / "listagem_pagina1.json"


def test_replay_e_mudanca_de_item_e_anexo_atualizam_linhas(tmp_path):
    con = sqlite3.connect(tmp_path / "replay.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
        "VALUES ('teste', 'api_json', 'https://teste.invalid', 'v1')"
    ).lastrowid
    con.commit()

    def lote(description, filename):
        return NormalizedBatch(
            orgs=(),
            processes=(),
            items=(ItemRecord("item-1", {"description": description, "qty": 2}),),
            attachments=(AttachmentRecord("anexo-1", {"kind": "edital", "filename": filename}),),
            results=(),
            awards=(),
            contracts=(),
            phases=(),
        )

    try:
        original = lote("Papel", "edital-v1.pdf")
        alterado = lote("Canetas", "edital-v2.pdf")
        for entrada in (original, original, alterado):
            run_id = open_source_run(con, source_id, "manual", "v1")
            load_batch(con, run_id, source_id, entrada)
            con.commit()
        item = con.execute(
            "SELECT description, first_seen_run_id, last_seen_run_id, last_changed_run_id "
            "FROM item WHERE source_native_id = 'item-1'"
        ).fetchone()
        attachment = con.execute(
            "SELECT filename, first_seen_run_id, last_seen_run_id, last_changed_run_id "
            "FROM attachment WHERE source_ref = 'anexo-1'"
        ).fetchone()
        assert tuple(item) == ("Canetas", 1, 3, 3)
        assert tuple(attachment) == ("edital-v2.pdf", 1, 3, 3)
        assert con.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM attachment").fetchone()[0] == 1
    finally:
        con.close()


def test_lote_industria_persiste_campos_de_processo(tmp_path):
    con = sqlite3.connect(tmp_path / "industria.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
        "VALUES ('sistema_industria', 'api_json', 'https://si.test', 'v1')"
    ).lastrowid
    con.commit()
    run_id = open_source_run(con, source_id, "manual", "v1")
    request = FetchRequest("/api/processos", "GET", {}, None, {}, "discover", "process", None, 1, None)
    body = json.dumps(
        {
            "processos": [
                {
                    "id": "SI-1",
                    "numero": "PE-1/2026",
                    "orgao": "Federacao das Industrias",
                    "fase": "aberto",
                    "data_publicacao": "2026-09-01T10:00:00Z",
                    "data_abertura": "2026-10-01T10:00:00Z",
                }
            ]
        }
    ).encode("utf-8")
    page = FetchedPage(request, 200, {"content-type": "application/json"}, body, "2026-09-22T00:00:00Z", 1)
    try:
        parsed = SistemaIndustriaAdapter().parse(page)
        load_batch(con, run_id, source_id, parsed.batch)
        row = con.execute(
            "SELECT number, published_at_source, opening_at_source FROM process WHERE source_native_id = 'SI-1'"
        ).fetchone()
        assert tuple(row) == ("PE-1/2026", "2026-09-01T10:00:00Z", "2026-10-01T10:00:00Z")
        winner_request = listar_vencedores_request("SI-1")
        winner_body = json.dumps(
            {
                "vencedores": [
                    {
                        "item": "A",
                        "fornecedor": {"nome": "Fornecedor Ltda", "cnpj": "12345678000190"},
                        "valor": "1234.56",
                    }
                ]
            }
        ).encode("utf-8")
        winner_page = FetchedPage(
            winner_request,
            200,
            {"content-type": "application/json"},
            winner_body,
            "2026-09-22T00:00:00Z",
            1,
        )
        winner_batch = SistemaIndustriaAdapter().parse(winner_page).batch
        load_batch(con, run_id, source_id, winner_batch)
        award = con.execute("SELECT result_id, process_id, supplier_name_raw, amount_cents FROM award").fetchone()
        assert award is not None
        assert tuple(award)[1:] == (
            con.execute("SELECT id FROM process WHERE source_native_id = 'SI-1'").fetchone()[0],
            "Fornecedor Ltda",
            123456,
        )
        assert con.execute("SELECT COUNT(*) FROM result WHERE id = ?", (award[0],)).fetchone()[0] == 1
        con.commit()
        replay_run = open_source_run(con, source_id, "manual", "v1")
        load_batch(con, replay_run, source_id, winner_batch)
        assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    finally:
        con.close()
