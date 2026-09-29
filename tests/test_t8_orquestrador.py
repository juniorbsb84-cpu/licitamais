"""Fase vermelha do TDD para o orquestrador-runner (T8)."""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import time
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from licitamais.schema import init_schema
from licitamais.types import (
    Capabilities,
    FetchedPage,
    FetchFailure,
    FetchRequest,
    NormalizedBatch,
    ParseResult,
    ProcessRecord,
)

MODULO_RUNNER = "licitamais.runner"
MODULO_ERRORS = "licitamais.errors"
MODULO_RATE = "licitamais.rate_limit"
MODULOS = (MODULO_RUNNER, MODULO_ERRORS, MODULO_RATE)

AGORA = "2026-09-22T12:00:00Z"
PUBLICADO_RECENTE = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
STATUS_VALIDOS = ("ok", "ok_zero", "suspect", "partial", "failed")


class Registro(dict):
    """Duble flexivel para tipos de producao ainda inexistentes."""

    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError as exc:
            raise AttributeError(nome) from exc

    def __setattr__(self, nome, valor):
        self[nome] = valor


def carregar_modulo(nome):
    """Falha pela ausencia do modulo, sem abortar a coleta."""
    spec = importlib.util.find_spec(nome)
    assert spec is not None, f"O modulo de producao {nome} ainda nao existe."
    return importlib.import_module(nome)


def obter_funcao(nome):
    for nome_modulo in MODULOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        if hasattr(modulo, nome):
            return getattr(modulo, nome)
    assert False, f"A funcao de producao {nome} ainda nao existe em {MODULOS}."


def modulo_de(nome):
    for nome_modulo in MODULOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        if hasattr(modulo, nome):
            return modulo
    assert False, f"A funcao de producao {nome} ainda nao existe em {MODULOS}."


def nome_classe(valor):
    """Normaliza enum/str para o nome minusculo da classe ou do status."""
    if hasattr(valor, "value") and not isinstance(valor, (str, int, float, bool)):
        valor = valor.value
    texto = str(valor)
    if "." in texto:
        texto = texto.split(".")[-1]
    return texto.lower().replace("-", "_").replace(" ", "_")


def normalizar_status(valor):
    return nome_classe(valor)


def resolver_classe_erro(nome):
    for nome_modulo in MODULOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        cls = getattr(modulo, "ErrorClass", None)
        if cls is None:
            continue
        for candidato in (nome, nome.upper()):
            if hasattr(cls, candidato):
                return getattr(cls, candidato)
        for candidato in (nome, nome.upper()):
            try:
                return cls(candidato)
            except Exception:
                pass
    return nome


def neutralizar_espera(monkeypatch, chamadas_rate=None):
    """Impede espera real: grava pausas de time.sleep e de sleep_rate_limited."""
    pausas = []
    monkeypatch.setattr(time, "sleep", lambda s: pausas.append(float(s)))
    for nome_modulo in MODULOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        if not hasattr(modulo, "sleep_rate_limited"):
            continue

        def gravadora(min_delay_ms, jitter_ms, _pausas=pausas, _chamadas=chamadas_rate):
            _pausas.append((float(min_delay_ms) + float(jitter_ms) / 2.0) / 1000.0)
            if _chamadas is not None:
                _chamadas.append((min_delay_ms, jitter_ms))
            return None

        monkeypatch.setattr(modulo, "sleep_rate_limited", gravadora)
    return pausas


def id_fonte(con, code="fonte_teste"):
    linha = con.execute("SELECT id FROM source WHERE code = ?", (code,)).fetchone()
    assert linha is not None
    return int(linha[0])


def criar_fonte(con, code="fonte_teste"):
    linha = con.execute(
        "SELECT id, code, base_url, adapter_version_atual FROM source WHERE code = ?",
        (code,),
    ).fetchone()
    assert linha is not None
    fonte_id = int(linha["id"])
    return Registro(
        id=fonte_id,
        source_id=fonte_id,
        code=str(linha["code"]),
        source_code=str(linha["code"]),
        base_url=str(linha["base_url"]),
        enabled=1,
        adapter_version=str(linha["adapter_version_atual"]),
        adapter_version_atual=str(linha["adapter_version_atual"]),
    )


def criar_cfg(
    adaptador,
    *,
    min_delay_ms=0,
    jitter_ms=0,
    max_calls=100,
    trigger="manual",
    adapters=None,
):
    mapa = (
        adapters
        if adapters is not None
        else {
            "fonte_teste": adaptador,
            "fonte_dois": adaptador,
        }
    )
    return Registro(
        adapter=adaptador,
        adaptador=adaptador,
        adapters=mapa,
        adapter_map=mapa,
        adapters_por_fonte=mapa,
        trigger=trigger,
        min_delay_ms=min_delay_ms,
        jitter_ms=jitter_ms,
        max_calls_per_run=max_calls,
        max_calls=max_calls,
        max_concurrency=1,
        capture_only=False,
    )


def criar_request(endpoint="/api/processos", params=None, phase="discover", cursor_out=None):
    return FetchRequest(
        endpoint=endpoint,
        method="GET",
        params=params if params is not None else {"pagina": "1"},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase=phase,
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=cursor_out,
    )


def criar_pagina(req, corpo=b'{"registros": []}'):
    return FetchedPage(
        request=req,
        status=200,
        headers={"content-type": "application/json"},
        body=corpo,
        fetched_at=AGORA,
        duration_ms=1,
    )


def criar_falha(req, mensagem, codigo):
    return FetchFailure(
        request=req,
        error=mensagem,
        fetched_at=AGORA,
        duration_ms=1,
        status=codigo,
    )


def lote_vazio():
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


def resultado_vazio():
    return ParseResult(
        batch=lote_vazio(),
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={},
        fatal=None,
    )


def resultado_com_processo(native_id, titulo="Titulo de teste", cursor=None):
    processo = ProcessRecord(
        source_native_id=native_id,
        attrs={
            "number": "1/2026",
            "year": 2026,
            "title": titulo,
            "record_hash": f"hash-{native_id}",
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
        cursor_out=cursor,
        signals={"rows": 1},
        fatal=None,
    )


def criar_counts(modulo, *, novo=0, mudado=0, inalterado=0, quarentena=0, buscado=0):
    for nome_cls in ("RunCounts", "Counts"):
        cls = getattr(modulo, nome_cls, None)
        if cls is None:
            continue
        try:
            return cls(
                fetched=buscado,
                new=novo,
                changed=mudado,
                unchanged=inalterado,
                quarantined=quarentena,
            )
        except Exception:
            pass
    return Registro(
        fetched=buscado,
        fetched_count=buscado,
        new=novo,
        new_count=novo,
        changed=mudado,
        changed_count=mudado,
        unchanged=inalterado,
        unchanged_count=inalterado,
        quarantined=quarentena,
        quarantined_count=quarentena,
        fatal=None,
        errors=(),
        contract_errors=0,
        has_contract_error=False,
    )


def criar_probe(modulo, nome="canario", passou=True):
    for nome_cls in ("ProbeResult", "Probe"):
        cls = getattr(modulo, nome_cls, None)
        if cls is None:
            continue
        for kwargs in (
            {"name": nome, "passed": passou},
            {"name": nome, "passed": passou, "kind": nome},
            {"kind": nome, "passed": passou},
        ):
            try:
                return cls(**kwargs)
            except Exception:
                continue
    return Registro(
        name=nome,
        nome=nome,
        kind=nome,
        tipo=nome,
        passed=passou,
        passou=passou,
        ok=passou,
        detail="",
    )


def extrair_status(resultado):
    if hasattr(resultado, "status"):
        return resultado.status
    if isinstance(resultado, Mapping):
        for chave in ("status", "estado"):
            if chave in resultado:
                return resultado[chave]
    if isinstance(resultado, (list, tuple)) and resultado:
        return extrair_status(resultado[0])
    return resultado


class SessaoDuble:
    def __init__(self):
        self.refresh_chamadas = 0
        self.token = "token-inicial"

    def refresh(self, *args, **kwargs):
        self.refresh_chamadas += 1
        self.token = f"token-{self.refresh_chamadas}"
        return self


class AdaptadorDuble:
    """Duble do protocolo Adapter: rede sempre dublada, sem socket real."""

    def __init__(
        self,
        roteiro=None,
        *,
        fetch_cb=None,
        parse_cb=None,
        min_delay_ms=0,
        jitter_ms=0,
        max_calls=100,
    ):
        self.source_code = "fonte_teste"
        self.adapter_version = "teste-1"
        self.sessao = SessaoDuble()
        self.capabilities = Capabilities(
            strategy="full_diff",
            entities=("process",),
            hydration={"enabled": False},
            listing_order_stable=True,
            supports_conditional_get=False,
            deletion_semantics="absence",
            rate={
                "min_delay_ms": min_delay_ms,
                "jitter_ms": jitter_ms,
                "max_calls_per_run": max_calls,
                "max_concurrency": 1,
            },
            auth="none",
            probes={},
        )
        self._roteiro = list(roteiro or [])
        self._fetch_cb = fetch_cb
        self._parse_cb = parse_cb
        self.chamadas_fetch = []
        self.chamadas_parse = []
        self.chamadas_plan = 0
        self.aberturas = 0
        self.fechamentos = 0

    def open(self, *args, **kwargs):
        self.aberturas += 1
        return self.sessao

    def close(self, *args, **kwargs):
        self.fechamentos += 1
        return None

    def plan(self, *args, **kwargs):
        self.chamadas_plan += 1
        if self.chamadas_plan == 1:
            yield from self._roteiro

    def fetch(self, sessao, req):
        self.chamadas_fetch.append(req)
        if self._fetch_cb is not None:
            return self._fetch_cb(sessao, req, len(self.chamadas_fetch))
        return criar_pagina(req)

    def parse(self, raw):
        self.chamadas_parse.append(raw)
        if self._parse_cb is not None:
            return self._parse_cb(raw, len(self.chamadas_parse))
        return resultado_vazio()

    def reautenticacoes(self):
        return self.sessao.refresh_chamadas + max(0, self.aberturas - 1)


@pytest.fixture()
def con():
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    conexao.execute(
        """
        INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)
        VALUES ('fonte_teste', 'api_json', 'https://fonte.test', 'teste-1', 1)
        """
    )
    # R15: fonte sem source_probe agora e suspect; o fixture declara a sonda
    conexao.execute(
        "INSERT INTO source_probe (source_id, required_fields) SELECT id, '[]' FROM source WHERE code = 'fonte_teste'"
    )
    conexao.commit()
    yield conexao
    conexao.close()


def test_modulos_de_producao_do_orquestrador_existem():
    for nome in MODULOS:
        spec = importlib.util.find_spec(nome)
        assert spec is not None, f"O modulo de producao {nome} ainda nao existe."


def test_open_close_source_run_registra_e_fecha_linha(con):
    abrir = obter_funcao("open_source_run")
    fechar = obter_funcao("close_source_run")
    fonte_id = id_fonte(con)

    run_id = abrir(con, fonte_id, "manual", "teste-1")
    assert isinstance(run_id, int) and run_id > 0
    linha = con.execute("SELECT source_id, adapter_version FROM source_run WHERE id = ?", (run_id,)).fetchone()
    assert linha is not None
    assert int(linha["source_id"]) == fonte_id
    assert str(linha["adapter_version"]) == "teste-1"

    fechar(con, run_id, "ok", None)
    fechada = con.execute("SELECT status, error FROM source_run WHERE id = ?", (run_id,)).fetchone()
    assert fechada["status"] == "ok"

    run_falha = abrir(con, fonte_id, "manual", "teste-1")
    fechar(con, run_falha, "failed", "auth persistiu")
    linha_falha = con.execute("SELECT status, error FROM source_run WHERE id = ?", (run_falha,)).fetchone()
    assert linha_falha["status"] == "failed"
    assert linha_falha["error"] is not None


def test_classify_error_taxonomia_fechada():
    classificar = obter_funcao("classify_error")
    assert nome_classe(classificar(TimeoutError("tempo esgotado"), None, {})) == "transient"
    assert nome_classe(classificar(ConnectionError("conexao resetada"), None, {})) == "transient"
    assert nome_classe(classificar(None, 502, {})) == "transient"
    assert nome_classe(classificar(None, 503, {})) == "transient"
    assert nome_classe(classificar(None, 429, {})) == "rate_limited"
    assert nome_classe(classificar(None, 403, {"Retry-After": "120"})) == "rate_limited"
    assert nome_classe(classificar(None, 401, {})) == "auth"
    assert nome_classe(classificar(None, 403, {})) == "auth"
    assert nome_classe(classificar(None, 404, {})) == "not_found"
    assert nome_classe(classificar(ValueError("campo canario ausente"), 200, {})) == "contract"
    assert nome_classe(classificar(RuntimeError("bug interno"), None, {})) == "internal"


def test_apply_retry_policy_transient_3x_e_contract_zero():
    politica = obter_funcao("apply_retry_policy")

    for tentativa in (0, 1, 2):
        deve, atraso = politica(resolver_classe_erro("transient"), tentativa)
        assert deve, f"transient deveria ter retry na tentativa {tentativa}"
        assert float(atraso) > 0
    deve, _ = politica(resolver_classe_erro("transient"), 3)
    assert not deve, "transient nao deveria ter retry na tentativa 3"

    deve, _ = politica(resolver_classe_erro("rate_limited"), 0)
    assert deve, "rate_limited deveria ter retry"

    for nome in ("contract", "auth", "not_found", "parse_record", "internal"):
        deve, _ = politica(resolver_classe_erro(nome), 0)
        assert not deve, f"a classe {nome} nao deveria ter retry"


def test_decide_status_ok_so_com_novidade():
    decidir = obter_funcao("decide_status")
    modulo = modulo_de("decide_status")
    sondas_ok = [
        criar_probe(modulo, "canario", True),
        criar_probe(modulo, "piso", True),
        criar_probe(modulo, "freshness", True),
    ]

    com_novidade = criar_counts(modulo, novo=2, mudado=0, inalterado=1, buscado=3)
    assert normalizar_status(decidir(com_novidade, sondas_ok)) == "ok"

    so_mudanca = criar_counts(modulo, novo=0, mudado=1, inalterado=1, buscado=2)
    assert normalizar_status(decidir(so_mudanca, sondas_ok)) == "ok"


def test_decide_status_ok_zero_so_com_todas_as_sondas_passando():
    decidir = obter_funcao("decide_status")
    modulo = modulo_de("decide_status")
    sem_novidade = criar_counts(modulo, novo=0, mudado=0, inalterado=5, buscado=5)

    sondas_ok = [criar_probe(modulo, "canario", True), criar_probe(modulo, "piso", True)]
    assert normalizar_status(decidir(sem_novidade, sondas_ok)) == "ok_zero"

    sondas_falha = [criar_probe(modulo, "canario", True), criar_probe(modulo, "piso", False)]
    status = normalizar_status(decidir(sem_novidade, sondas_falha))
    assert status != "ok_zero"
    assert status in ("suspect", "partial", "failed")


def test_decide_status_nunca_ok_zero_por_omissao():
    decidir = obter_funcao("decide_status")
    modulo = modulo_de("decide_status")
    sem_nada = criar_counts(modulo, novo=0, mudado=0, inalterado=0, buscado=0)

    status = normalizar_status(decidir(sem_nada, []))
    assert status != "ok_zero"
    assert status in ("suspect", "partial", "failed")


def test_decide_status_suspect_para_contract_com_2xx_layout_errado():
    decidir = obter_funcao("decide_status")
    modulo = modulo_de("decide_status")
    buscado_sem_novidade = criar_counts(modulo, novo=0, mudado=0, inalterado=0, buscado=1)
    sondas_contrato = [criar_probe(modulo, "canario_contrato", False)]

    assert normalizar_status(decidir(buscado_sem_novidade, sondas_contrato)) == "suspect"


def test_sleep_rate_limited_respeita_min_delay_e_jitter(monkeypatch):
    dormir = obter_funcao("sleep_rate_limited")
    pausas = []
    monkeypatch.setattr(time, "sleep", lambda s: pausas.append(float(s)))

    dormir(50, 20)

    assert len(pausas) >= 1
    for pausa in pausas:
        assert 0.05 <= pausa <= 0.071


def test_run_completa_cada_request_em_transacao_propria(con, monkeypatch):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)
    req1 = criar_request(params={"pagina": "1"}, cursor_out={"pagina": "1"})
    req2 = criar_request(params={"pagina": "2"}, cursor_out={"pagina": "2"})

    def parse_cb(raw, n):
        pagina = str(raw.request.params.get("pagina", n))
        return resultado_com_processo(f"proc-{pagina}", cursor={"pagina": pagina})

    adaptador = AdaptadorDuble([req1, req2], parse_cb=parse_cb)
    resultado = executar(con, criar_fonte(con), criar_cfg(adaptador))

    assert normalizar_status(extrair_status(resultado)) == "ok"

    runs = con.execute("SELECT id, status FROM source_run").fetchall()
    assert len(runs) == 1
    run_id = int(runs[0]["id"])
    assert runs[0]["status"] == "ok"

    assert con.execute("SELECT COUNT(*) FROM raw_capture").fetchone()[0] == 2
    distintos = con.execute("SELECT DISTINCT source_run_id FROM raw_capture").fetchall()
    assert [int(linha[0]) for linha in distintos] == [run_id]
    assert con.execute("SELECT COUNT(*) FROM payload_store").fetchone()[0] >= 1
    assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 2

    cursores = con.execute("SELECT updated_run_id FROM sync_cursor").fetchall()
    assert len(cursores) >= 1
    assert all(int(linha["updated_run_id"]) == run_id for linha in cursores)

    assert not con.in_transaction


def test_run_checkpoint_persiste_request_concluido_quando_segundo_falha(con, monkeypatch):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)
    req1 = criar_request(params={"pagina": "1"})
    req2 = criar_request(params={"pagina": "2"})

    def fetch_cb(sessao, req, n):
        if req.params.get("pagina") == "2":
            raise TimeoutError("tempo esgotado")
        return criar_pagina(req)

    def parse_cb(raw, n):
        pagina = str(raw.request.params.get("pagina", n))
        return resultado_com_processo(f"proc-{pagina}")

    adaptador = AdaptadorDuble([req1, req2], fetch_cb=fetch_cb, parse_cb=parse_cb)
    resultado = executar(con, criar_fonte(con), criar_cfg(adaptador))

    status = normalizar_status(extrair_status(resultado))
    assert status in ("partial", "failed")
    assert status != "ok_zero"

    assert con.execute("SELECT COUNT(*) FROM raw_capture").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 1
    linha = con.execute("SELECT status FROM source_run").fetchone()
    assert linha["status"] in ("partial", "failed")
    assert not con.in_transaction


def test_run_cache_por_request_key_nao_bate_rede(con, monkeypatch):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)
    req = criar_request(params={"pagina": "1"})
    duplicada = criar_request(params={"pagina": "1"})
    assert req.key == duplicada.key
    req2 = criar_request(params={"pagina": "2"})

    def parse_cb(raw, n):
        pagina = str(raw.request.params.get("pagina", n))
        return resultado_com_processo(f"proc-{pagina}")

    adaptador = AdaptadorDuble([req, duplicada, req2], parse_cb=parse_cb)
    executar(con, criar_fonte(con), criar_cfg(adaptador))

    assert len(adaptador.chamadas_fetch) == 2
    capturas = con.execute("SELECT COUNT(*) FROM raw_capture").fetchone()[0]
    assert 2 <= capturas <= 3
    assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 2


def test_run_erro_transient_tem_retry_3x_com_backoff(con, monkeypatch):
    executar = obter_funcao("run_source")
    pausas = neutralizar_espera(monkeypatch)

    def fetch_cb(sessao, req, n):
        raise TimeoutError("tempo esgotado")

    adaptador = AdaptadorDuble([criar_request()], fetch_cb=fetch_cb)
    resultado = executar(con, criar_fonte(con), criar_cfg(adaptador))

    assert len(adaptador.chamadas_fetch) == 4
    assert len(pausas) >= 3
    status = normalizar_status(extrair_status(resultado))
    assert status in ("partial", "failed")
    assert status != "ok_zero"


def test_run_erro_contract_tem_zero_retry_e_vira_suspect(con, monkeypatch):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)

    def fetch_cb(sessao, req, n):
        raise ValueError("HTML onde se esperava JSON")

    adaptador = AdaptadorDuble([criar_request()], fetch_cb=fetch_cb)
    resultado = executar(con, criar_fonte(con), criar_cfg(adaptador))

    assert len(adaptador.chamadas_fetch) == 1
    assert normalizar_status(extrair_status(resultado)) == "suspect"
    assert con.execute("SELECT status FROM source_run").fetchone()["status"] == "suspect"


@pytest.mark.parametrize("codigo", [401, 403])
def test_run_auth_faz_um_refresh_e_um_retry_exato(con, monkeypatch, codigo):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)

    def fetch_cb(sessao, req, n):
        if n == 1:
            return criar_falha(req, "auth recusada", codigo)
        return criar_pagina(req)

    adaptador = AdaptadorDuble(
        [criar_request()],
        fetch_cb=fetch_cb,
        parse_cb=lambda raw, n: resultado_com_processo("proc-1"),
    )
    resultado = executar(con, criar_fonte(con), criar_cfg(adaptador))

    assert len(adaptador.chamadas_fetch) == 2
    assert adaptador.chamadas_fetch[0].key == adaptador.chamadas_fetch[1].key
    assert adaptador.reautenticacoes() == 1
    assert normalizar_status(extrair_status(resultado)) == "ok"


@pytest.mark.parametrize("codigo", [401, 403])
def test_run_auth_persistente_termina_failed_nunca_ok_zero(con, monkeypatch, codigo):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)

    def fetch_cb(sessao, req, n):
        return criar_falha(req, "auth recusada", codigo)

    adaptador = AdaptadorDuble([criar_request()], fetch_cb=fetch_cb)
    resultado = executar(con, criar_fonte(con), criar_cfg(adaptador))

    assert len(adaptador.chamadas_fetch) == 2
    assert adaptador.reautenticacoes() == 1
    status = normalizar_status(extrair_status(resultado))
    assert status == "failed"
    assert status != "ok_zero"
    assert con.execute("SELECT status FROM source_run").fetchone()["status"] == "failed"


def test_run_aplica_rate_limit_entre_chamadas_sucessivas(con, monkeypatch):
    executar = obter_funcao("run_source")
    chamadas = []
    pausas = neutralizar_espera(monkeypatch, chamadas_rate=chamadas)
    req1 = criar_request(params={"pagina": "1"})
    req2 = criar_request(params={"pagina": "2"})

    def parse_cb(raw, n):
        pagina = str(raw.request.params.get("pagina", n))
        return resultado_com_processo(f"proc-{pagina}")

    adaptador = AdaptadorDuble([req1, req2], parse_cb=parse_cb, min_delay_ms=50, jitter_ms=10)
    cfg = criar_cfg(adaptador, min_delay_ms=50, jitter_ms=10)
    executar(con, criar_fonte(con), cfg)

    assert len(adaptador.chamadas_fetch) == 2
    assert chamadas or pausas
    if chamadas:
        assert any(a == 50 and b == 10 for a, b in chamadas)
    else:
        assert any(0.05 <= pausa <= 0.071 for pausa in pausas)


def test_run_respeita_teto_de_chamadas_por_run(con, monkeypatch):
    executar = obter_funcao("run_source")
    neutralizar_espera(monkeypatch)
    roteiro = [criar_request(params={"pagina": str(i)}) for i in range(1, 6)]

    def parse_cb(raw, n):
        pagina = str(raw.request.params.get("pagina", n))
        return resultado_com_processo(f"proc-{pagina}")

    adaptador = AdaptadorDuble(roteiro, parse_cb=parse_cb, max_calls=2)
    cfg = criar_cfg(adaptador, max_calls=2)
    executar(con, criar_fonte(con), cfg)

    assert len(adaptador.chamadas_fetch) <= 2


def test_run_all_roda_todas_as_fontes_sequencialmente(con, monkeypatch):
    executar_todas = obter_funcao("run_all")
    neutralizar_espera(monkeypatch)
    con.execute(
        """
        INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)
        VALUES ('fonte_dois', 'api_json', 'https://fonte2.test', 'teste-1', 1)
        """
    )
    con.commit()

    def parse_cb(raw, n):
        return resultado_com_processo(f"proc-{raw.request.params.get('pagina', n)}")

    adaptador_um = AdaptadorDuble([criar_request()], parse_cb=parse_cb)
    adaptador_dois = AdaptadorDuble([criar_request()], parse_cb=parse_cb)
    cfg = criar_cfg(
        adaptador_um,
        adapters={"fonte_teste": adaptador_um, "fonte_dois": adaptador_dois},
    )

    resultados = executar_todas(con, cfg)

    assert isinstance(resultados, list) and len(resultados) == 2
    for resultado in resultados:
        assert normalizar_status(extrair_status(resultado)) in STATUS_VALIDOS
    assert con.execute("SELECT COUNT(*) FROM source_run").fetchone()[0] == 2
