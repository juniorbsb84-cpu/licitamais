"""Fase vermelha do TDD para T13: runner gera alertas ao fim de run verde.

Remediacao: nada no pipeline aciona match_subscription/enqueue_alert hoje,
entao um run verde nunca gera alerta. Estes testes provam o comportamento
esperado usando SQLite real (init_schema) e adaptador sintetico sem rede.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from types import SimpleNamespace

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
NATIVE_ID = "edital-1"
PALAVRA = "notebook"


class SessaoDuble:
    def refresh(self, *args, **kwargs):
        return self


class AdaptadorSintetico:
    """Adaptador sem rede: devolve sempre um processo com a keyword."""

    def __init__(self, objeto="aquisicao de notebook para a sede", record_hash="hash-v1"):
        self.objeto = objeto
        self.record_hash = record_hash
        self.sessao = SessaoDuble()
        self.capabilities = Capabilities(
            strategy="full_diff",
            entities=("process",),
            hydration={"enabled": False},
            listing_order_stable=True,
            supports_conditional_get=False,
            deletion_semantics="absence",
            rate={"min_delay_ms": 0, "jitter_ms": 0, "max_calls_per_run": 100, "max_concurrency": 1},
            auth="none",
            probes={},
        )

    def open(self, *args, **kwargs):
        return self.sessao

    def close(self, *args, **kwargs):
        return None

    def plan(self, *args, **kwargs):
        yield FetchRequest(
            endpoint="/api/processos",
            method="GET",
            params={"pagina": "1"},
            body=None,
            headers_extra={"Accept": "application/json"},
            phase="discover",
            entity_hint="process",
            parent_native_id=None,
            cost_weight=1,
            cursor_out=None,
        )

    def fetch(self, sessao, req):
        return FetchedPage(
            request=req,
            status=200,
            headers={"content-type": "application/json"},
            body=b'{"registros": [{"id": "edital-1"}]}',
            fetched_at=AGORA,
            duration_ms=1,
        )

    def parse(self, pagina):
        processo = ProcessRecord(
            source_native_id=NATIVE_ID,
            attrs={
                "number": "1/2026",
                "year": 2026,
                "title": "Pregao de material de escritorio",
                "object": self.objeto,
                "record_hash": self.record_hash,
                "published_at_source": PUBLICADO_RECENTE,
            },
        )
        lote = NormalizedBatch(
            orgs=(),
            processes=(processo,),
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


class AdaptadorQuebraContrato(AdaptadorSintetico):
    def fetch(self, sessao, req):
        raise ValueError("layout quebrou: html onde se esperava json")


class AdaptadorFalhaAbertura(AdaptadorSintetico):
    def open(self, *args, **kwargs):
        raise RuntimeError("banco de origem caiu")


class AdaptadorParcialComNovo(AdaptadorSintetico):
    """Duas paginas: pagina 1 persiste processo novo com keyword, pagina 2 falha TRANSIENT."""

    def plan(self, *args, **kwargs):
        for pagina in ("1", "2"):
            yield FetchRequest(
                endpoint="/api/processos",
                method="GET",
                params={"pagina": pagina},
                body=None,
                headers_extra={"Accept": "application/json"},
                phase="discover",
                entity_hint="process",
                parent_native_id=None,
                cost_weight=1,
                cursor_out=None,
            )

    def fetch(self, sessao, req):
        if str(req.params.get("pagina")) == "2":
            raise TimeoutError("tempo esgotado")
        return super().fetch(sessao, req)

    def parse(self, pagina):
        if str(pagina.request.params.get("pagina")) == "2":
            lote = NormalizedBatch(
                orgs=(),
                processes=(),
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
        return super().parse(pagina)


def sem_espera(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    try:
        import licitamais.runner as _runner

        monkeypatch.setattr(_runner.time, "sleep", lambda s: None)
        monkeypatch.setattr(_runner, "sleep_rate_limited", lambda *a, **k: None)
    except Exception:
        pass


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES ('fonte_teste', 'api_json', 'https://fonte.test', 'teste-1', 1)"
    )
    # R15: fonte sem source_probe agora e suspect; o fixture declara a sonda
    con.execute(
        "INSERT INTO source_probe (source_id, required_fields) SELECT id, '[]' FROM source WHERE code = 'fonte_teste'"
    )
    con.commit()
    return con


def fonte_id(con):
    linha = con.execute("SELECT id FROM source WHERE code = ?", (CODIGO_FONTE,)).fetchone()
    assert linha is not None
    return int(linha[0])


def criar_assinatura(con, keywords=(PALAVRA,), enabled=1):
    filtro = json.dumps(
        {"keywords": list(keywords), "modalities": [], "entities": []},
        ensure_ascii=False,
    )
    cur = con.execute(
        "INSERT INTO alert_subscription (kind, channel, target, filter_expr, enabled, created_at, attrs)"
        " VALUES ('filtro', 'telegram', 'usuario-teste', ?, ?, ?, ?)",
        (filtro, int(enabled), AGORA, filtro),
    )
    con.commit()
    return int(cur.lastrowid)


def montar_fonte(source_id):
    return SimpleNamespace(
        id=source_id,
        source_id=source_id,
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


def outbox_pendente(con):
    return con.execute("SELECT id, dedup_key, payload, status FROM alert_outbox ORDER BY id").fetchall()


def processo(con, source_id):
    return con.execute(
        "SELECT id, first_seen_run_id, last_changed_run_id FROM process WHERE source_id = ? AND source_native_id = ?",
        (source_id, NATIVE_ID),
    ).fetchone()


def test_contrato_alertas_exposto_em_alerts_user():
    mod = importlib.import_module("licitamais.alerts.user")
    assert hasattr(mod, "subscriptions_ativas"), "alerts.user precisa expor subscriptions_ativas"
    assert hasattr(mod, "gerar_alertas_do_run"), "alerts.user precisa expor gerar_alertas_do_run"


def test_run_ok_com_keyword_enfileira_um_alerta_pending():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        sub_id = criar_assinatura(con)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorSintetico()))

        assert resultado.status == "ok", f"run deveria ser ok, veio {resultado.status}"
        linhas = outbox_pendente(con)
        assert len(linhas) == 1, "run ok com processo casado deveria enfileirar 1 alerta"
        assert linhas[0]["status"] == "pending"
        carga = json.loads(linhas[0]["payload"])
        proc = processo(con, sid)
        assert proc is not None
        assert carga["user_id"] == sub_id
        assert int(carga["process_id"]) == int(proc["id"])
        assert carga["event_kind"] == "new"
        assert str(carga["event_ref"]) == str(resultado.run_id)
        assert linhas[0]["dedup_key"] == f"{sub_id}:{proc['id']}:new:{resultado.run_id}"
    finally:
        con.close()


def test_replay_do_mesmo_payload_nao_duplica_alerta():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con)
        adaptador = AdaptadorSintetico()
        cfg = montar_cfg(adaptador)
        fonte = montar_fonte(sid)
        run1 = run_source(con, fonte, cfg)
        assert run1.status == "ok"
        assert len(outbox_pendente(con)) == 1

        run2 = run_source(con, fonte, cfg)
        assert run2.status in ("ok", "ok_zero")
        linhas = outbox_pendente(con)
        assert len(linhas) == 1, "replay sem mudanca nao pode criar alerta novo"
        proc = processo(con, sid)
        assert int(proc["last_changed_run_id"]) == int(run1.run_id)
        assert int(proc["last_changed_run_id"]) != int(run2.run_id)
    finally:
        con.close()


def test_processo_alterado_gera_alerta_changed_com_dedup_diferente():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        sub_id = criar_assinatura(con)
        adaptador = AdaptadorSintetico()
        cfg = montar_cfg(adaptador)
        fonte = montar_fonte(sid)
        run1 = run_source(con, fonte, cfg)
        assert len(outbox_pendente(con)) == 1
        run_source(con, fonte, cfg)
        assert len(outbox_pendente(con)) == 1

        adaptador.objeto = "aquisicao de notebook e monitor para a sede"
        adaptador.record_hash = "hash-v2"
        run3 = run_source(con, fonte, cfg)
        assert run3.status == "ok"
        linhas = outbox_pendente(con)
        assert len(linhas) == 2, "processo alterado deveria gerar um segundo alerta"
        proc = processo(con, sid)
        assert int(proc["first_seen_run_id"]) == int(run1.run_id)
        assert int(proc["last_changed_run_id"]) == int(run3.run_id)
        primeira, segunda = linhas[0], linhas[1]
        assert primeira["dedup_key"] != segunda["dedup_key"]
        carga = json.loads(segunda["payload"])
        assert carga["event_kind"] == "changed"
        assert str(carga["event_ref"]) == str(run3.run_id)
        assert segunda["dedup_key"] == f"{sub_id}:{proc['id']}:changed:{run3.run_id}"
    finally:
        con.close()


def test_run_suspect_nao_enfileira_alerta():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorQuebraContrato()))

        assert resultado.status == "suspect"
        assert outbox_pendente(con) == [], "run suspect nao pode enfileirar alerta"
    finally:
        con.close()


def test_run_failed_nao_enfileira_alerta():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorFalhaAbertura()))

        assert resultado.status == "failed"
        assert outbox_pendente(con) == [], "run failed nao pode enfileirar alerta"
    finally:
        con.close()


def test_assinatura_desabilitada_nunca_gera_alerta():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con, enabled=0)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorSintetico()))

        assert resultado.status == "ok"
        assert processo(con, sid) is not None, "processo deveria ter sido persistido"
        assert outbox_pendente(con) == [], "assinatura com enabled=0 nunca gera alerta"
    finally:
        con.close()


def test_run_partial_com_novo_persistido_nao_perde_alerta(monkeypatch):
    """REGRESSAO: run com novo>0 e 1 erro TRANSIENT vira PARTIAL e o alerta se perde.

    A pagina 1 persiste um processo novo casado com a assinatura; a pagina 2
    falha TRANSIENT (apos retries) e o run fecha partial. O processo novo ja
    esta persistido com last_changed_run_id = run parcial. No run seguinte
    (payload identico, fonte saudavel) o processo vira unchanged, logo nenhum
    run posterior enfileira o alerta: o edital novo nunca gera alerta.
    O comportamento esperado e que o alerta do processo novo ja esteja na
    fila ao fim do run parcial (ou, no minimo, seja recuperado no run
    seguinte), nunca perdido em silencio.
    """
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con)
        fonte = montar_fonte(sid)
        adaptador = AdaptadorParcialComNovo()
        resultado = run_source(con, fonte, montar_cfg(adaptador))

        assert resultado.status == "partial", f"run deveria ser partial, veio {resultado.status}"
        assert resultado.counts.new == 1, "pagina 1 deveria ter persistido 1 processo novo"
        proc = processo(con, sid)
        assert proc is not None, "processo novo deveria estar persistido mesmo no run partial"
        assert int(proc["last_changed_run_id"]) == int(resultado.run_id)

        linhas = outbox_pendente(con)
        assert len(linhas) == 1, (
            "processo novo persistido no run partial nao pode perder o alerta: esperava 1 linha pending em alert_outbox"
        )
        carga = json.loads(linhas[0]["payload"])
        assert carga["event_kind"] == "new"
        assert str(carga["event_ref"]) == str(resultado.run_id)

        saudavel = AdaptadorSintetico(objeto=adaptador.objeto, record_hash=adaptador.record_hash)
        run2 = run_source(con, fonte, montar_cfg(saudavel))
        assert run2.status in ("ok", "ok_zero")
        proc2 = processo(con, sid)
        assert int(proc2["last_changed_run_id"]) == int(resultado.run_id), (
            "no run seguinte o processo vira unchanged: se o alerta nao saiu no run parcial, ele se perde para sempre"
        )
        assert len(outbox_pendente(con)) == 1
    finally:
        con.close()
