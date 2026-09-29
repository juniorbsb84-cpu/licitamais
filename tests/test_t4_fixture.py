"""Fase vermelha do TDD para o adapter sintetico fixture (T4)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import socket
import sqlite3
from pathlib import Path

import pytest

from licitamais.schema import init_schema
from licitamais.types import FetchedPage, FetchRequest, PlanContext

GOLDEN_DIR = Path(__file__).parent / "golden" / "synthetic"
MODULO = "licitamais.adapters.synthetic"
COLETADO_EM = "2026-09-22T12:00:00Z"
ID_MALFORMADO = "proc-malformed"


@pytest.fixture(scope="module")
def synthetic():
    """Falha no teste, nao na coleta, enquanto o adapter T4 nao existir."""
    spec = importlib.util.find_spec(MODULO)
    assert spec is not None, f"O modulo de producao {MODULO} ainda nao existe."
    return importlib.import_module(MODULO)


def _candidatos_fixture() -> list[str]:
    nomes = ["processos.json", "processos"]
    if GOLDEN_DIR.is_dir():
        for caminho in sorted(GOLDEN_DIR.glob("*.json")):
            if caminho.name not in nomes:
                nomes.append(caminho.name)
            if caminho.stem not in nomes:
                nomes.append(caminho.stem)
    return nomes


def _bytes_fixture(synthetic) -> bytes:
    ultimo_erro: Exception | None = None
    for nome in _candidatos_fixture():
        try:
            corpo = synthetic.load_fixture(nome)
        except Exception as exc:  # noqa: BLE001 - tenta proximo candidato
            ultimo_erro = exc
            continue
        assert isinstance(corpo, bytes), "load_fixture precisa devolver bytes"
        assert corpo, f"fixture {nome} veio vazia"
        return corpo
    raise AssertionError(f"load_fixture nao abriu nenhum candidato: {ultimo_erro}")


def _contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=1,
    )


def _request_manual() -> FetchRequest:
    return FetchRequest(
        endpoint="/synthetic/processos",
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _request_descoberta(synthetic) -> FetchRequest:
    adapter = synthetic.SyntheticAdapter()
    try:
        primeiro = next(adapter.plan(_contexto()))
    except StopIteration:
        return _request_manual()
    assert isinstance(primeiro, FetchRequest)
    return primeiro


def _pagina(request: FetchRequest, corpo: bytes) -> FetchedPage:
    return FetchedPage(
        request=request,
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=corpo,
        fetched_at=COLETADO_EM,
        duration_ms=1,
    )


def _bloquear_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def _proibido(*args: object, **kwargs: object) -> None:
        raise AssertionError("parse nao pode tocar rede")

    monkeypatch.setattr(socket, "socket", _proibido)
    monkeypatch.setattr(socket, "create_connection", _proibido)


def _snapshot_sem_volateis(con: sqlite3.Connection) -> dict[str, list[dict]]:
    foto: dict[str, list[dict]] = {}
    for tabela in (
        "organization",
        "process",
        "item",
        "attachment",
        "result",
        "award",
        "contract",
        "phase_event",
    ):
        existe = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (tabela,),
        ).fetchone()
        if existe is None:
            continue
        linhas = con.execute(f"SELECT * FROM {tabela} ORDER BY id").fetchall()
        normalizadas: list[dict] = []
        for linha in linhas:
            dado = dict(linha)
            for chave in [k for k in dado if k.startswith("last_seen")]:
                dado.pop(chave, None)
            dado.pop("confirm_count", None)
            normalizadas.append(dado)
        foto[tabela] = normalizadas
    return foto


def test_contrato_superficie(synthetic) -> None:
    assert synthetic.SyntheticAdapter.source_code == "synthetic"
    assert synthetic.SyntheticAdapter.adapter_version == "0.1.0"
    assert callable(synthetic.load_fixture)

    adapter = synthetic.SyntheticAdapter()
    assert adapter.source_code == "synthetic"
    assert adapter.adapter_version == "0.1.0"

    caps = adapter.capabilities
    for chave in (
        "strategy",
        "entities",
        "hydration",
        "listing_order_stable",
        "supports_conditional_get",
        "deletion_semantics",
        "rate",
        "auth",
        "probes",
    ):
        assert hasattr(caps, chave), f"capabilities sem {chave}"
    assert caps.strategy in ("full_diff", "watermark", "two_phase")
    assert caps.auth in ("none", "csrf_handshake", "token")

    assert callable(adapter.open)
    assert callable(adapter.close)
    assert callable(adapter.plan)
    assert callable(adapter.fetch)
    assert callable(adapter.parse)


def test_load_fixture_devolve_bytes_utf8(synthetic) -> None:
    corpo = _bytes_fixture(synthetic)
    texto = corpo.decode("utf-8", errors="strict")
    dados = json.loads(texto)
    assert isinstance(dados, dict)
    assert "proc-1" in texto
    assert "proc-2" in texto


def test_parse_deterministico(synthetic) -> None:
    adapter = synthetic.SyntheticAdapter()
    corpo = _bytes_fixture(synthetic)
    raw = _pagina(_request_descoberta(synthetic), corpo)

    primeiro = adapter.parse(raw)
    segundo = adapter.parse(raw)

    assert primeiro == segundo
    assert primeiro.batch.processes
    ids = [p.source_native_id for p in primeiro.batch.processes]
    assert "proc-1" in ids
    assert "proc-2" in ids


def test_parse_com_socket_bloqueado(synthetic, monkeypatch: pytest.MonkeyPatch) -> None:
    _bloquear_socket(monkeypatch)
    adapter = synthetic.SyntheticAdapter()
    corpo = _bytes_fixture(synthetic)
    raw = _pagina(_request_descoberta(synthetic), corpo)

    resultado = adapter.parse(raw)

    assert resultado.batch.processes


def test_parse_sem_conexao_sqlite(synthetic, monkeypatch: pytest.MonkeyPatch) -> None:
    def _conectar_proibido(*args: object, **kwargs: object) -> None:
        raise AssertionError("parse nao pode abrir banco")

    monkeypatch.setattr(sqlite3, "connect", _conectar_proibido)
    con = None
    assert con is None

    adapter = synthetic.SyntheticAdapter()
    corpo = _bytes_fixture(synthetic)
    raw = _pagina(_request_descoberta(synthetic), corpo)

    resultado = adapter.parse(raw)

    assert resultado.batch.processes


def test_registro_malformado_vai_para_quarentena(synthetic) -> None:
    adapter = synthetic.SyntheticAdapter()
    corpo = _bytes_fixture(synthetic)
    raw = _pagina(_request_descoberta(synthetic), corpo)

    resultado = adapter.parse(raw)

    assert resultado.fatal is None
    assert resultado.quarantine
    assert all(item.reason.strip() for item in resultado.quarantine)
    assert all(item.pointer for item in resultado.quarantine)
    ids = {p.source_native_id for p in resultado.batch.processes}
    assert ID_MALFORMADO not in ids


def test_replay_2_runs_snapshot_identico_exceto_volateis(synthetic) -> None:
    import licitamais.loader as loader

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        init_schema(con)
        con.execute(
            """
            INSERT INTO source (id, code, transport, base_url, adapter_version_atual)
            VALUES (1, 'synthetic', 'api_json', 'https://synthetic.test', '0.1.0')
            """
        )
        con.executemany(
            """
            INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at)
            VALUES (?, 1, 'manual', '0.1.0', 'ok', ?)
            """,
            [(1, COLETADO_EM), (2, COLETADO_EM)],
        )

        adapter = synthetic.SyntheticAdapter()
        corpo = _bytes_fixture(synthetic)
        request = _request_descoberta(synthetic)

        batch1 = adapter.parse(_pagina(request, corpo)).batch
        saida1 = loader.load_batch(con, run_id=1, source_id=1, batch=batch1)
        assert saida1.new > 0
        foto1 = _snapshot_sem_volateis(con)

        batch2 = adapter.parse(_pagina(request, corpo)).batch
        assert batch2 == batch1
        saida2 = loader.load_batch(con, run_id=2, source_id=1, batch=batch2)

        assert saida2.new == 0
        assert saida2.changed == 0
        assert saida2.unchanged == saida1.new

        foto2 = _snapshot_sem_volateis(con)
        assert foto2.keys() == foto1.keys()
        for tabela in foto1:
            assert foto2[tabela] == foto1[tabela], f"snapshot divergiu em {tabela}"

        total_process = con.execute("SELECT COUNT(*) FROM process").fetchone()[0]
        assert total_process == len(batch1.processes)

        visto = con.execute("SELECT last_seen_run_id, last_changed_run_id FROM process LIMIT 1").fetchone()
        assert dict(visto) == {"last_seen_run_id": 2, "last_changed_run_id": 1}

        fases = con.execute("SELECT confirm_count, last_seen_run_id FROM phase_event").fetchall()
        for fase in fases:
            assert dict(fase) == {"confirm_count": 2, "last_seen_run_id": 2}
    finally:
        con.close()
