"""r46: situacao e orgao ficavam presos em process.attrs.

Os adaptadores gravam a situacao da fonte em attrs (phase_label, fase) e o orgao
em attrs (org_native_id + org_nome/nome_filial/entidade), mas nao emitem
PhaseRecord nem OrgRecord: phase_current_label e org_id ficavam NULL em 100%
dos processos. O loader passa a derivar os dois do proprio processo.
"""

from __future__ import annotations

import sqlite3

import pytest

from licitamais.loader import backfill_situacao_orgao, load_batch
from licitamais.schema import init_schema
from licitamais.types import NormalizedBatch, ProcessRecord

AGORA = "2026-09-24T12:00:00Z"
VAZIO = {c: () for c in NormalizedBatch.__dataclass_fields__}


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
        [(1, AGORA), (2, AGORA)],
    )
    return conexao


def _lote(*processos):
    return NormalizedBatch(**{**VAZIO, "processes": tuple(processos)})


def test_situacao_e_orgao_vem_do_processo(con):
    load_batch(
        con,
        1,
        1,
        _lote(
            ProcessRecord(
                "p1",
                {"object": "pneus", "phase_label": "Publicada", "org_native_id": "SENAC-DF", "org_nome": "SENAC DF"},
            ),
            ProcessRecord("p2", {"object": "frota", "fase": "Homologado", "org_native_id": "SENAC-DF"}),
        ),
    )
    linhas = {
        r["source_native_id"]: r
        for r in con.execute("SELECT source_native_id, phase_current_label, org_id FROM process")
    }
    assert linhas["p1"]["phase_current_label"] == "Publicada"
    assert linhas["p2"]["phase_current_label"] == "Homologado"
    assert linhas["p1"]["org_id"] is not None
    assert linhas["p1"]["org_id"] == linhas["p2"]["org_id"]
    nome = con.execute("SELECT name_raw FROM organization WHERE id = ?", (linhas["p1"]["org_id"],)).fetchone()[0]
    assert nome == "SENAC DF"


def test_mudanca_de_situacao_fecha_fase_anterior(con):
    load_batch(con, 1, 1, _lote(ProcessRecord("p1", {"phase_label": "Publicada"})))
    load_batch(con, 2, 1, _lote(ProcessRecord("p1", {"phase_label": "Homologada"})))
    fases = con.execute("SELECT phase_label, ended_at FROM phase_event ORDER BY id").fetchall()
    assert [f["phase_label"] for f in fases] == ["Publicada", "Homologada"]
    assert fases[0]["ended_at"] is not None and fases[1]["ended_at"] is None


def test_backfill_preenche_processos_ja_gravados(con):
    con.execute(
        "INSERT INTO process (source_id, source_native_id, record_hash, attrs,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
        " VALUES (1, 'antigo', 'h', ?, 1, 1, 1)",
        ('{"situacao_ignorada": 1, "phase_label": "Encerrada", "org_native_id": "X", "nome_filial": "Unidade X"}',),
    )
    assert backfill_situacao_orgao(con, 1) == 1
    linha = con.execute("SELECT phase_current_label, org_id, phase_current_group FROM process").fetchone()
    assert linha["phase_current_label"] == "Encerrada"
    assert linha["org_id"] is not None
    assert linha["phase_current_group"] == "encerrada"
    assert backfill_situacao_orgao(con, 1) == 0
