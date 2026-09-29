"""Fase vermelha T16: remediacao tombstone por ausencia (carencia de 2 runs).

Cobre o contrato futuro `marcar_ausentes` em licitamais.loader e a
integracao em runner.run_source, usando SQLite real e rede dublada.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import licitamais.loader as loader_mod
import licitamais.runner as runner_mod
from licitamais.runner import run_source
from licitamais.schema import init_schema
from licitamais.types import (
    Capabilities,
    FetchedPage,
    FetchRequest,
    NormalizedBatch,
    ParseResult,
    ProcessRecord,
)

AGORA = "2026-09-22T12:00:00Z"
PUBLICADO_RECENTE = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
CODIGO_FONTE = "fonte_teste"


def exigir_marcar_ausentes():
    """Resolve marcar_ausentes ou falha pelo defeito (ausencia no loader)."""
    fn = getattr(loader_mod, "marcar_ausentes", None)
    assert callable(fn), "loader precisa expor marcar_ausentes(con, source_id, run_id, carencia=2)"
    return fn


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES (?, 'api_json', 'https://fonte.test', 'teste-1', 1)",
        (CODIGO_FONTE,),
    )
    con.execute(
        "INSERT INTO source_probe (source_id, required_fields) SELECT id, '[]' FROM source WHERE code = ?",
        (CODIGO_FONTE,),
    )
    con.commit()
    return con


def fonte_id(con):
    linha = con.execute("SELECT id FROM source WHERE code = ?", (CODIGO_FONTE,)).fetchone()
    assert linha is not None
    return int(linha[0])


def criar_run(con, sid, status):
    cur = con.execute(
        'INSERT INTO source_run (source_id, "trigger", adapter_version, status, started_at)'
        " VALUES (?, 'manual', 'teste-1', ?, ?)",
        (sid, status, AGORA),
    )
    con.commit()
    return int(cur.lastrowid)


def criar_processo(con, sid, nativo, primeiro, visto):
    con.execute(
        "INSERT INTO process (source_id, source_native_id, title,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id, deleted_at)"
        " VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (sid, nativo, nativo, primeiro, visto, primeiro),
    )
    con.commit()


def ler_deleted(con, sid, nativo):
    linha = con.execute(
        "SELECT deleted_at FROM process WHERE source_id = ? AND source_native_id = ?",
        (sid, nativo),
    ).fetchone()
    assert linha is not None, f"processo {nativo} deveria existir"
    return linha[0]


def sem_espera(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(runner_mod, "sleep_rate_limited", lambda *a, **k: None)


def montar_fonte(sid):
    return SimpleNamespace(
        id=sid,
        source_id=sid,
        code=CODIGO_FONTE,
        source_code=CODIGO_FONTE,
        base_url="https://fonte.test",
        enabled=1,
        adapter_version="teste-1",
        adapter_version_atual="teste-1",
    )


def montar_cfg(adaptador):
    mapa = {CODIGO_FONTE: adaptador}
    return SimpleNamespace(
        adapter=adaptador,
        adaptador=adaptador,
        adapters=mapa,
        adapter_map=mapa,
        adapters_por_fonte=mapa,
        trigger="manual",
        min_delay_ms=0,
        jitter_ms=0,
        max_calls_per_run=100,
        max_calls=100,
        capture_only=False,
    )


class SessaoDuble:
    def refresh(self, *args, **kwargs):
        return self


class AdaptadorTombstone:
    """Adaptador sem rede: lotes por indice de run e falhas programadas."""

    def __init__(self, lotes, *, deletion_semantics="absence", falhas=None):
        self.lotes = dict(lotes or {})
        self.falhas = dict(falhas or {})
        self.deletion_semantics = deletion_semantics
        self.sessao = SessaoDuble()
        self.n_run = 0
        self.capabilities = Capabilities(
            strategy="full_diff",
            entities=("process",),
            hydration={"enabled": False},
            listing_order_stable=True,
            supports_conditional_get=False,
            deletion_semantics=deletion_semantics,
            rate={"min_delay_ms": 0, "jitter_ms": 0, "max_calls_per_run": 100, "max_concurrency": 1},
            auth="none",
            probes={},
        )

    def open(self, *args, **kwargs):
        self.n_run += 1
        if self.falhas.get(self.n_run) == "failed":
            raise RuntimeError("banco de origem caiu")
        return self.sessao

    def close(self, *args, **kwargs):
        return None

    def plan(self, *args, **kwargs):
        yield FetchRequest(
            endpoint="/api/processos",
            method="GET",
            params={"pagina": "1", "run": str(self.n_run)},
            body=None,
            headers_extra={"Accept": "application/json"},
            phase="discover",
            entity_hint="process",
            parent_native_id=None,
            cost_weight=1,
            cursor_out=None,
        )

    def fetch(self, sessao, req):
        modo = self.falhas.get(self.n_run)
        if modo == "suspect":
            raise ValueError("layout quebrou: html onde se esperava json")
        if modo == "partial":
            raise TimeoutError("tempo esgotado")
        registros = [{"id": nativo} for nativo, _hash in self.lotes.get(self.n_run, [])]
        corpo = json.dumps({"registros": registros}, ensure_ascii=False).encode("utf-8")
        return FetchedPage(
            request=req,
            status=200,
            headers={"content-type": "application/json"},
            body=corpo,
            fetched_at=AGORA,
            duration_ms=1,
        )

    def parse(self, pagina):
        processos = tuple(
            ProcessRecord(
                source_native_id=nativo,
                attrs={
                    "number": "1/2026",
                    "year": 2026,
                    "title": f"Processo {nativo}",
                    "record_hash": hash_registro,
                    "published_at_source": PUBLICADO_RECENTE,
                },
            )
            for nativo, hash_registro in self.lotes.get(self.n_run, [])
        )
        lote = NormalizedBatch(
            orgs=(),
            processes=processos,
            items=(),
            attachments=(),
            results=(),
            awards=(),
            contracts=(),
            phases=(),
        )
        return ParseResult(
            batch=lote,
            quarantine=(),
            next=(),
            cursor_out=None,
            signals={},
            fatal=None,
        )


def test_contrato_marcar_ausentes_existe_com_assinatura():
    fn = exigir_marcar_ausentes()
    params = list(inspect.signature(fn).parameters.values())
    nomes = [p.name for p in params]
    assert nomes == ["con", "source_id", "run_id", "carencia"], (
        f"marcar_ausentes precisa ter assinatura (con, source_id, run_id, carencia=2), achado {nomes!r}"
    )
    assert params[3].default == 2, "carencia padrao precisa ser 2"


def test_marcar_ausentes_carencia_2_runs_ok():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        r1 = criar_run(con, sid, "ok")
        r2 = criar_run(con, sid, "ok")
        r3 = criar_run(con, sid, "ok")
        criar_processo(con, sid, "alvo", r1, r1)
        criar_processo(con, sid, "recente", r1, r2)
        criar_processo(con, sid, "atual", r1, r3)
        fn = exigir_marcar_ausentes()

        assert fn(con, sid, r2) == 0, "com 2 runs ok o alvo visto no penultimo nao pode ser marcado"
        assert ler_deleted(con, sid, "alvo") is None

        assert fn(con, sid, r3) == 1, "ausente nos 2 ultimos runs ok deveria marcar 1 processo"
        assert ler_deleted(con, sid, "alvo") is not None, "alvo fora dos 2 ultimos runs ok precisa de deleted_at"
        assert ler_deleted(con, sid, "recente") is None
        assert ler_deleted(con, sid, "atual") is None
    finally:
        con.close()


def test_marcar_ausentes_ignora_runs_nao_ok():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        r1 = criar_run(con, sid, "ok")
        criar_run(con, sid, "partial")
        criar_run(con, sid, "suspect")
        criar_run(con, sid, "failed")
        criar_run(con, sid, "ok_zero")
        r6 = criar_run(con, sid, "ok")
        criar_processo(con, sid, "alvo", r1, r1)
        fn = exigir_marcar_ausentes()

        assert fn(con, sid, r6) == 0, (
            "partial/suspect/failed/ok_zero nao contam para a carencia:"
            " com so 2 runs ok o alvo visto no penultimo ok nao pode ser marcado"
        )
        assert ler_deleted(con, sid, "alvo") is None

        r7 = criar_run(con, sid, "ok")
        assert fn(con, sid, r7) == 1, "apos 3 runs ok o alvo deveria ser marcado"
        assert ler_deleted(con, sid, "alvo") is not None
    finally:
        con.close()


def test_run_ok_marca_apos_2_ausencias_e_nao_antes(monkeypatch):
    exigir_marcar_ausentes()
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        lotes = {
            1: [("alvo", "hash-alvo"), ("churn", "hash-c1")],
            2: [("churn", "hash-c2")],
            3: [("churn", "hash-c3")],
        }
        adaptador = AdaptadorTombstone(lotes, deletion_semantics="absence")
        fonte, cfg = montar_fonte(sid), montar_cfg(adaptador)

        r1 = run_source(con, fonte, cfg)
        assert r1.status == "ok", f"run 1 deveria ser ok, veio {r1.status}"
        r2 = run_source(con, fonte, cfg)
        assert r2.status == "ok", f"run 2 deveria ser ok, veio {r2.status}"
        assert ler_deleted(con, sid, "alvo") is None, "apos 1 ausencia ok nao pode marcar"

        r3 = run_source(con, fonte, cfg)
        assert r3.status == "ok", f"run 3 deveria ser ok, veio {r3.status}"
        assert ler_deleted(con, sid, "alvo") is not None, "apos 2 ausencias ok precisa marcar deleted_at"
        assert ler_deleted(con, sid, "churn") is None
    finally:
        con.close()


@pytest.mark.parametrize(
    ("modo", "status_esperado"),
    [("partial", "partial"), ("suspect", "suspect"), ("failed", "failed")],
)
def test_run_nao_ok_nunca_marca(monkeypatch, modo, status_esperado):
    exigir_marcar_ausentes()
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        lotes = {
            1: [("alvo", "hash-alvo"), ("churn", "hash-c1")],
            2: [("churn", "hash-c2")],
        }
        adaptador = AdaptadorTombstone(lotes, deletion_semantics="absence", falhas={2: modo})
        fonte, cfg = montar_fonte(sid), montar_cfg(adaptador)

        r1 = run_source(con, fonte, cfg)
        assert r1.status == "ok", f"run 1 deveria ser ok, veio {r1.status}"
        r2 = run_source(con, fonte, cfg)
        assert r2.status == status_esperado, f"run 2 deveria ser {status_esperado}, veio {r2.status}"
        assert ler_deleted(con, sid, "alvo") is None, f"run {status_esperado} nunca pode marcar deleted_at"
    finally:
        con.close()


def test_run_nao_ok_nao_conta_para_carencia(monkeypatch):
    exigir_marcar_ausentes()
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        lotes = {
            1: [("alvo", "hash-alvo"), ("churn", "hash-c1")],
            3: [("churn", "hash-c3")],
            4: [("churn", "hash-c4")],
        }
        adaptador = AdaptadorTombstone(lotes, deletion_semantics="absence", falhas={2: "failed"})
        fonte, cfg = montar_fonte(sid), montar_cfg(adaptador)

        r1 = run_source(con, fonte, cfg)
        assert r1.status == "ok"
        r2 = run_source(con, fonte, cfg)
        assert r2.status == "failed"
        assert ler_deleted(con, sid, "alvo") is None

        r3 = run_source(con, fonte, cfg)
        assert r3.status == "ok", f"run 3 deveria ser ok, veio {r3.status}"
        assert ler_deleted(con, sid, "alvo") is None, "failed no meio nao conta: 1a ausencia ok nao pode marcar"

        r4 = run_source(con, fonte, cfg)
        assert r4.status == "ok", f"run 4 deveria ser ok, veio {r4.status}"
        assert ler_deleted(con, sid, "alvo") is not None, "apos 2 ausencias em runs ok precisa marcar"
    finally:
        con.close()


def test_processo_marcado_que_reaparece_volta_a_null(monkeypatch):
    exigir_marcar_ausentes()
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        lotes = {
            1: [("alvo", "hash-alvo"), ("churn", "hash-c1")],
            2: [("churn", "hash-c2")],
            3: [("churn", "hash-c3")],
            # run 4 traz 1 processo, como os runs 2 e 3: com 2 a sonda de
            # sanidade (+-30%) derruba o run para suspect
            4: [("alvo", "hash-alvo")],
        }
        adaptador = AdaptadorTombstone(lotes, deletion_semantics="absence")
        fonte, cfg = montar_fonte(sid), montar_cfg(adaptador)

        assert run_source(con, fonte, cfg).status == "ok"
        assert run_source(con, fonte, cfg).status == "ok"
        r3 = run_source(con, fonte, cfg)
        assert r3.status == "ok"
        assert ler_deleted(con, sid, "alvo") is not None, "pre-condicao: alvo deveria estar marcado apos run 3"

        r4 = run_source(con, fonte, cfg)
        assert r4.status == "ok", f"run 4 deveria ser ok, veio {r4.status}"
        assert ler_deleted(con, sid, "alvo") is None, "reapareceu no run seguinte: deleted_at precisa voltar a NULL"
    finally:
        con.close()


@pytest.mark.parametrize("semantica", ["never", "explicit_flag"])
def test_semantica_diferente_de_absence_nunca_marca(monkeypatch, semantica):
    exigir_marcar_ausentes()
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        lotes = {
            1: [("alvo", "hash-alvo"), ("churn", "hash-c1")],
            2: [("churn", "hash-c2")],
            3: [("churn", "hash-c3")],
        }
        adaptador = AdaptadorTombstone(lotes, deletion_semantics=semantica)
        fonte, cfg = montar_fonte(sid), montar_cfg(adaptador)

        assert run_source(con, fonte, cfg).status == "ok"
        assert run_source(con, fonte, cfg).status == "ok"
        r3 = run_source(con, fonte, cfg)
        assert r3.status == "ok", f"run 3 deveria ser ok, veio {r3.status}"
        assert ler_deleted(con, sid, "alvo") is None, (
            f"fonte com deletion_semantics={semantica!r} nunca pode ter processo marcado"
        )
    finally:
        con.close()
