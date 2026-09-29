"""Fase vermelha do TDD para o loader transacional do core."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import pathlib
import sqlite3

import pytest

from licitamais.schema import init_schema
from licitamais.types import (
    AwardRecord,
    NormalizedBatch,
    OrgRecord,
    PhaseRecord,
    ProcessRecord,
)

AGORA = "2026-09-22T12:00:00Z"


@pytest.fixture()
def con() -> sqlite3.Connection:
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)

    conexao.executemany(
        """
        INSERT INTO source (
            id, code, transport, base_url, adapter_version_atual
        ) VALUES (?, ?, 'api_json', 'https://fonte.test', 'teste-1')
        """,
        [
            (1, "fonte-um"),
            (2, "fonte-dois"),
        ],
    )
    conexao.executemany(
        """
        INSERT INTO source_run (
            id, source_id, "trigger", adapter_version, status, started_at
        ) VALUES (?, ?, 'manual', 'teste-1', 'ok', ?)
        """,
        [
            (1, 1, AGORA),
            (2, 1, AGORA),
            (3, 1, AGORA),
            (4, 2, AGORA),
        ],
    )
    yield conexao
    conexao.close()


@pytest.fixture(scope="module")
def loader():
    caminho = pathlib.Path(__file__).parents[1] / "src" / "licitamais" / "loader.py"
    assert caminho.is_file(), "O modulo de producao licitamais.loader ainda nao existe."
    return importlib.import_module("licitamais.loader")


@pytest.fixture(scope="module")
def canonical():
    spec = importlib.util.find_spec("licitamais.canonical")
    assert spec is not None, "O modulo de producao licitamais.canonical ainda nao existe."
    return importlib.import_module("licitamais.canonical")


def batch(
    *,
    orgs: tuple[OrgRecord, ...] = (),
    processes: tuple[ProcessRecord, ...] = (),
    awards: tuple[AwardRecord, ...] = (),
    phases: tuple[PhaseRecord, ...] = (),
) -> NormalizedBatch:
    return NormalizedBatch(
        orgs=orgs,
        processes=processes,
        items=(),
        attachments=(),
        results=(),
        awards=awards,
        contracts=(),
        phases=phases,
    )


def processo(
    native_id: str = "proc-1",
    *,
    titulo: str = "Compra de computadores",
    hash_registro: str = "hash-a",
) -> ProcessRecord:
    return ProcessRecord(
        source_native_id=native_id,
        attrs={
            "number": "123/2026",
            "year": 2026,
            "modality_code": "PE",
            "title": titulo,
            "record_hash": hash_registro,
        },
    )


def fase(
    process_native_id: str = "proc-1",
    *,
    codigo: str = "aberto",
    rotulo: str = "Aberto",
) -> PhaseRecord:
    return PhaseRecord(
        source_native_id=process_native_id,
        attrs={
            "phase_code": codigo,
            "phase_label": rotulo,
        },
    )


def test_capture_record_pertence_ao_modulo_canonical(canonical) -> None:
    assert hasattr(canonical, "CaptureRecord")
    assert not hasattr(importlib.import_module("licitamais.types"), "CaptureRecord")


def test_replay_do_mesmo_batch_nao_insere_processos_nem_fase_extra(
    con: sqlite3.Connection,
    loader,
) -> None:
    entrada = batch(processes=(processo(),), phases=(fase(),))

    loader.load_batch(con, run_id=1, source_id=1, batch=entrada)
    loader.load_batch(con, run_id=2, source_id=1, batch=entrada)

    assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 1
    corrente = con.execute(
        """
        SELECT confirm_count, last_seen_run_id
        FROM phase_event
        WHERE ended_at IS NULL
        """
    ).fetchone()
    assert dict(corrente) == {
        "confirm_count": 2,
        "last_seen_run_id": 2,
    }


def test_hash_igual_so_marca_process_como_visto_e_hash_diferente_marca_mudanca(
    con: sqlite3.Connection,
    loader,
) -> None:
    loader.load_batch(
        con,
        run_id=1,
        source_id=1,
        batch=batch(processes=(processo(hash_registro="hash-original"),)),
    )
    loader.load_batch(
        con,
        run_id=2,
        source_id=1,
        batch=batch(processes=(processo(hash_registro="hash-original"),)),
    )

    visto = con.execute(
        """
        SELECT last_seen_run_id, last_changed_run_id
        FROM process
        WHERE source_id = 1 AND source_native_id = 'proc-1'
        """
    ).fetchone()
    assert dict(visto) == {
        "last_seen_run_id": 2,
        "last_changed_run_id": 1,
    }

    loader.load_batch(
        con,
        run_id=3,
        source_id=1,
        batch=batch(
            processes=(
                processo(
                    titulo="Compra de computadores atualizada",
                    hash_registro="hash-atualizado",
                ),
            )
        ),
    )

    mudado = con.execute(
        """
        SELECT title, record_hash, last_seen_run_id, last_changed_run_id
        FROM process
        WHERE source_id = 1 AND source_native_id = 'proc-1'
        """
    ).fetchone()
    assert dict(mudado) == {
        "title": "Compra de computadores atualizada",
        "record_hash": "hash-atualizado",
        "last_seen_run_id": 3,
        "last_changed_run_id": 3,
    }


def test_process_excluido_anteriormente_e_ressuscitado_pelo_upsert(
    con: sqlite3.Connection,
    loader,
) -> None:
    loader.load_batch(
        con,
        run_id=1,
        source_id=1,
        batch=batch(processes=(processo(),)),
    )
    con.execute(
        """
        UPDATE process
        SET deleted_at = '2026-09-21T00:00:00Z'
        WHERE source_id = 1 AND source_native_id = 'proc-1'
        """
    )

    loader.load_batch(
        con,
        run_id=2,
        source_id=1,
        batch=batch(processes=(processo(),)),
    )

    linha = con.execute(
        """
        SELECT deleted_at, first_seen_run_id, last_seen_run_id
        FROM process
        WHERE source_id = 1 AND source_native_id = 'proc-1'
        """
    ).fetchone()
    assert dict(linha) == {
        "deleted_at": None,
        "first_seen_run_id": 1,
        "last_seen_run_id": 2,
    }


def test_transicao_de_fase_fecha_corrente_e_abre_outra_sem_duplicar_corrente(
    con: sqlite3.Connection,
    loader,
) -> None:
    loader.load_batch(
        con,
        run_id=1,
        source_id=1,
        batch=batch(processes=(processo(),), phases=(fase(),)),
    )
    process_id = con.execute(
        """
        SELECT id FROM process
        WHERE source_id = 1 AND source_native_id = 'proc-1'
        """
    ).fetchone()["id"]

    loader.upsert_phase(
        con,
        run_id=2,
        process_id=process_id,
        new_phase_code="julgamento",
        new_phase_label="Em julgamento",
        now=AGORA,
    )

    eventos = con.execute(
        """
        SELECT phase_code, ended_at, ended_run_id, confirm_count, last_seen_run_id
        FROM phase_event
        WHERE process_id = ?
        ORDER BY id
        """,
        (process_id,),
    ).fetchall()
    assert [dict(evento) for evento in eventos] == [
        {
            "phase_code": "aberto",
            "ended_at": AGORA,
            "ended_run_id": 2,
            "confirm_count": 1,
            "last_seen_run_id": 1,
        },
        {
            "phase_code": "julgamento",
            "ended_at": None,
            "ended_run_id": None,
            "confirm_count": 1,
            "last_seen_run_id": 2,
        },
    ]

    loader.upsert_phase(
        con,
        run_id=3,
        process_id=process_id,
        new_phase_code="julgamento",
        new_phase_label="Em julgamento",
        now=AGORA,
    )

    assert (
        con.execute(
            """
        SELECT COUNT(*)
        FROM phase_event
        WHERE process_id = ? AND ended_at IS NULL
        """,
            (process_id,),
        ).fetchone()[0]
        == 1
    )
    atual = con.execute(
        """
        SELECT confirm_count, last_seen_run_id
        FROM phase_event
        WHERE process_id = ? AND ended_at IS NULL
        """,
        (process_id,),
    ).fetchone()
    assert dict(atual) == {
        "confirm_count": 2,
        "last_seen_run_id": 3,
    }


def test_award_com_valor_divergente_e_append_only_e_aponta_para_predecessor(
    con: sqlite3.Connection,
    loader,
) -> None:
    con.execute(
        """
        INSERT INTO process (
            id, source_id, source_native_id, record_hash,
            first_seen_run_id, last_seen_run_id, last_changed_run_id
        ) VALUES (10, 1, 'proc-1', 'hash-a', 1, 1, 1)
        """
    )
    con.execute(
        """
        INSERT INTO result (
            id, source_id, source_native_id, process_id, type,
            first_seen_run_id, last_seen_run_id, last_changed_run_id
        ) VALUES (20, 1, 'resultado-1', 10, 'adjudicacao', 1, 1, 1)
        """
    )
    primeiro = AwardRecord(
        source_native_id="award-1",
        attrs={
            "result_id": 20,
            "process_id": 10,
            "supplier_name_raw": "Fornecedor Ltda",
            "amount_cents": 10000,
        },
    )
    divergente = AwardRecord(
        source_native_id="award-1",
        attrs={
            "result_id": 20,
            "process_id": 10,
            "supplier_name_raw": "Fornecedor Ltda",
            "amount_cents": 12500,
        },
    )

    primeiro_id = loader.append_award(con, run_id=1, award=primeiro)
    novo_id = loader.append_award(con, run_id=2, award=divergente)

    assert novo_id != primeiro_id
    awards = con.execute(
        """
        SELECT id, amount_cents, supersedes_id, first_seen_run_id
        FROM award
        ORDER BY id
        """
    ).fetchall()
    assert [dict(award) for award in awards] == [
        {
            "id": primeiro_id,
            "amount_cents": 10000,
            "supersedes_id": None,
            "first_seen_run_id": 1,
        },
        {
            "id": novo_id,
            "amount_cents": 12500,
            "supersedes_id": primeiro_id,
            "first_seen_run_id": 2,
        },
    ]


def test_payload_repetido_nao_duplica_bytes_e_capture_repetido_e_unchanged(
    con: sqlite3.Connection,
    loader,
    canonical,
) -> None:
    corpo = b'{"processos":[{"id":"proc-1"}]}'
    sha256 = hashlib.sha256(corpo).hexdigest()

    assert loader.insert_payload(con, sha256, corpo) is True
    assert loader.insert_payload(con, sha256, corpo) is False
    assert con.execute("SELECT COUNT(*) FROM payload_store").fetchone()[0] == 1

    primeiro = canonical.CaptureRecord(
        source_id=1,
        source_run_id=1,
        endpoint="/api/processos",
        url="https://fonte.test/api/processos",
        sha256=sha256,
        body=corpo,
        http_status=200,
        content_type="application/json",
        captured_at=AGORA,
    )
    repetido = canonical.CaptureRecord(
        source_id=1,
        source_run_id=2,
        endpoint="/api/processos",
        url="https://fonte.test/api/processos",
        sha256=sha256,
        body=corpo,
        http_status=200,
        content_type="application/json",
        captured_at=AGORA,
    )

    loader.record_capture(con, primeiro)
    loader.record_capture(con, repetido)

    captures = con.execute(
        """
        SELECT source_run_id, unchanged
        FROM raw_capture
        ORDER BY id
        """
    ).fetchall()
    assert [dict(capture) for capture in captures] == [
        {"source_run_id": 1, "unchanged": 0},
        {"source_run_id": 2, "unchanged": 1},
    ]


def test_mesmo_cnpj_funde_organizacoes_entre_fontes_e_cnpj_ausente_nao_conflita(
    con: sqlite3.Connection,
    loader,
) -> None:
    comum_a = OrgRecord(
        source_native_id="org-a",
        attrs={
            "cnpj": "12.345.678/0001-90",
            "name_raw": "Empresa Exemplo Ltda",
            "kind_hint": "fornecedor",
        },
    )
    comum_b = OrgRecord(
        source_native_id="org-b",
        attrs={
            "cnpj": "12.345.678/0001-90",
            "name_raw": "Empresa Exemplo LTDA",
            "kind_hint": "fornecedor",
        },
    )
    sem_cnpj_a = OrgRecord(
        source_native_id="sem-cnpj-a",
        attrs={"cnpj": None, "name_raw": "Sem Documento A"},
    )
    sem_cnpj_b = OrgRecord(
        source_native_id="sem-cnpj-b",
        attrs={"cnpj": None, "name_raw": "Sem Documento B"},
    )

    org_comum_a = loader.upsert_organization(con, run_id=1, org=comum_a)
    loader.upsert_organization_ref(
        con,
        run_id=1,
        source_id=1,
        native_org_key="org-a",
        org_id=org_comum_a,
    )
    org_comum_b = loader.upsert_organization(con, run_id=4, org=comum_b)
    loader.upsert_organization_ref(
        con,
        run_id=4,
        source_id=2,
        native_org_key="org-b",
        org_id=org_comum_b,
    )
    org_sem_cnpj_a = loader.upsert_organization(con, run_id=1, org=sem_cnpj_a)
    org_sem_cnpj_b = loader.upsert_organization(con, run_id=4, org=sem_cnpj_b)

    assert org_comum_a == org_comum_b
    assert org_sem_cnpj_a != org_sem_cnpj_b
    assert con.execute("SELECT COUNT(*) FROM organization").fetchone()[0] == 3
    assert (
        con.execute(
            """
        SELECT COUNT(*)
        FROM organization_ref
        WHERE org_id = ?
        """,
            (org_comum_a,),
        ).fetchone()[0]
        == 2
    )
