"""Fase vermelha do TDD para saude por fonte e incidentes (T9)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from licitamais.schema import init_schema

MODULO_HEALTH = "licitamais.health"
MODULO_PROBES = "licitamais.probes"
MODULOS = (MODULO_HEALTH, MODULO_PROBES)
AGORA = "2026-09-22T12:00:00Z"


class SondaGenerica(dict):
    """Duble flexivel para ProbeResult ainda inexistente."""

    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError as exc:
            raise AttributeError(nome) from exc

    def __setattr__(self, nome, valor):
        self[nome] = valor


def carregar_modulo(nome):
    """Falha pelo ausencia do modulo, sem abortar a coleta."""
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


def obter_classe_probe():
    for nome_modulo in MODULOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        for candidato in ("ProbeResult", "Probe", "Sonda"):
            if hasattr(modulo, candidato):
                return getattr(modulo, candidato)
    return None


def criar_sonda(passou, nome="canario"):
    cls = obter_classe_probe()
    if cls is not None:
        tentativas = [
            {"name": nome, "passed": passou},
            {"nome": nome, "passou": passou},
            {"kind": nome, "passed": passou},
            {"name": nome, "ok": passou},
            {"kind": nome, "ok": passou},
        ]
        for kwargs in tentativas:
            try:
                return cls(**kwargs)
            except Exception:
                continue
        try:
            return cls(nome, passou)
        except Exception:
            pass
    return SondaGenerica(
        name=nome,
        nome=nome,
        kind=nome,
        tipo=nome,
        passed=passou,
        passou=passou,
        ok=passou,
        success=passou,
        detail="",
        message="",
        mensagem="",
    )


def probe_passou(resultado):
    if isinstance(resultado, dict):
        for chave in ("passed", "passou", "ok", "success"):
            if chave in resultado:
                valor = resultado[chave]
                if isinstance(valor, bool):
                    return valor
                if isinstance(valor, int):
                    return bool(valor)
                if isinstance(valor, str):
                    return valor.lower() in ("1", "true", "pass", "passed", "ok", "sim")
        if "status" in resultado:
            texto = str(resultado["status"]).lower()
            if texto in ("pass", "passed", "ok", "ok_zero", "sucesso"):
                return True
            if texto in ("fail", "failed", "suspect", "failed_hard", "error"):
                return False
        return False
    for attr in ("passed", "passou", "ok", "success"):
        if hasattr(resultado, attr):
            valor = getattr(resultado, attr)
            if callable(valor):
                continue
            if isinstance(valor, bool):
                return valor
            if isinstance(valor, int):
                return bool(valor)
            if isinstance(valor, str):
                return valor.lower() in ("1", "true", "pass", "passed", "ok")
    if hasattr(resultado, "status"):
        try:
            texto = str(resultado.status).lower()
            if "pass" in texto or texto == "ok":
                return True
            if "fail" in texto or "suspect" in texto or "error" in texto:
                return False
        except Exception:
            pass
    return False


def probe_texto(resultado):
    partes = []
    if isinstance(resultado, dict):
        for valor in resultado.values():
            if isinstance(valor, str):
                partes.append(valor)
            elif valor is not None:
                partes.append(str(valor))
        return " ".join(partes)
    for attr in (
        "detail",
        "details",
        "message",
        "mensagem",
        "reason",
        "motivo",
        "name",
        "nome",
        "kind",
        "tipo",
        "status",
        "severity",
        "error",
        "erro",
    ):
        if hasattr(resultado, attr):
            try:
                valor = getattr(resultado, attr)
            except Exception:
                continue
            if callable(valor):
                continue
            if isinstance(valor, str):
                partes.append(valor)
            elif valor is not None:
                partes.append(str(valor))
    try:
        dados = vars(resultado)
        for valor in dados.values():
            if isinstance(valor, str) and valor not in partes:
                partes.append(valor)
    except Exception:
        pass
    partes.append(str(resultado))
    return " ".join(partes)


@pytest.fixture()
def con():
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    conexao.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES ('fonte_teste', 'api_json', 'https://fonte.test', 'teste-1', 1)"
    )
    fonte_id = conexao.execute("SELECT id FROM source WHERE code = 'fonte_teste'").fetchone()[0]
    conexao.execute(
        "INSERT INTO source_probe"
        " (source_id, min_rows_pct, required_fields, max_staleness_days, max_quarantine_pct)"
        " VALUES (?, ?, ?, ?, ?)",
        (int(fonte_id), 0.85, '["id", "numero", "ano"]', 30, 0.02),
    )
    conexao.commit()
    yield conexao
    conexao.close()


def id_fonte(con, code="fonte_teste"):
    linha = con.execute("SELECT id FROM source WHERE code = ?", (code,)).fetchone()
    assert linha is not None
    return int(linha[0])


def criar_run(con, source_id, status, fetched=0, quarantined=0):
    cursor = con.execute(
        "INSERT INTO source_run"
        ' (source_id, "trigger", adapter_version, status, started_at, finished_at,'
        " fetched_count, new_count, changed_count, unchanged_count, quarantined_count)"
        " VALUES (?, 'manual', 'teste-1', ?, ?, ?, ?, 0, 0, 0, ?)",
        (source_id, status, AGORA, AGORA, fetched, quarantined),
    )
    return int(cursor.lastrowid)


def definir_cursor(con, source_id, chave, valor, run_id):
    con.execute(
        "INSERT INTO sync_cursor (source_id, cursor_key, value, updated_run_id, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(source_id, cursor_key) DO UPDATE SET"
        " value = excluded.value,"
        " updated_run_id = excluded.updated_run_id,"
        " updated_at = excluded.updated_at",
        (source_id, chave, valor, run_id, AGORA),
    )


def semear_base(con, source_id, total, run_id):
    con.execute("UPDATE source_run SET fetched_count = ? WHERE id = ?", (total, run_id))
    for chave in ("RowsCount", "last_rows", "rows_count"):
        definir_cursor(con, source_id, chave, str(total), run_id)


def semear_total(con, source_id, total, run_id):
    semear_base(con, source_id, total, run_id)
    con.execute(
        "DELETE FROM process WHERE source_id = ? AND source_native_id LIKE 'seed-%'",
        (source_id,),
    )
    for i in range(total):
        con.execute(
            "INSERT INTO process"
            " (source_id, source_native_id, first_seen_run_id, last_seen_run_id, last_changed_run_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (source_id, f"seed-{total}-{i}", run_id, run_id, run_id),
        )


def inserir_processo(con, source_id, run_id, published_at, native_id="proc-1"):
    con.execute(
        "INSERT INTO process"
        " (source_id, source_native_id, published_at_source,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, native_id, published_at, run_id, run_id, run_id),
    )


def inserir_quarentena(con, source_id, run_id, total=1):
    for i in range(total):
        con.execute(
            "INSERT INTO parse_quarantine"
            " (source_id, source_run_id, native_ref, error, raw_excerpt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, run_id, f"ref-{run_id}-{i}", "campo obrigatorio ausente", "{}", AGORA),
        )


def test_modulos_de_producao_saude_existem():
    for nome in MODULOS:
        spec = importlib.util.find_spec(nome)
        assert spec is not None, f"O modulo de producao {nome} ainda nao existe."


def test_piso_relativo_293_para_240_falha_com_mensagem(con):
    check = obter_funcao("check_min_rows_pct")
    fonte = id_fonte(con)
    run_ant = criar_run(con, fonte, "ok", fetched=293)
    semear_total(con, fonte, 293, run_ant)
    con.commit()
    res = check(con, fonte, 240)
    assert not probe_passou(res), "piso de 85pct deveria falhar para 240 contra 293"
    texto = probe_texto(res).lower()
    assert "85" in texto or "piso" in texto or "293" in texto or "240" in texto or "relativo" in texto or "%" in texto


def test_piso_relativo_crescimento_nao_vira_alarme(con):
    check = obter_funcao("check_min_rows_pct")
    fonte = id_fonte(con)
    base = 200
    run_atual = criar_run(con, fonte, "ok", fetched=base)
    semear_base(con, fonte, base, run_atual)
    con.commit()
    for _ in range(14):
        atual = base + 5
        res = check(con, fonte, atual)
        assert probe_passou(res), f"crescimento {base} -> {atual} nao deveria alarmar"
        novo_run = criar_run(con, fonte, "ok", fetched=atual)
        semear_base(con, fonte, atual, novo_run)
        con.commit()
        base = atual


def test_quarentena_acima_2pct_marca_suspect_mesmo_http_200(con):
    check_q = obter_funcao("check_quarantine_rate")
    talvez_abrir = obter_funcao("maybe_open_incident")
    fonte = id_fonte(con)
    run_id = criar_run(con, fonte, "ok", fetched=100, quarantined=3)
    inserir_quarentena(con, fonte, run_id, total=3)
    con.commit()
    res = check_q(con, run_id, fonte)
    assert not probe_passou(res), "quarentena de 3pct deveria falhar"
    texto = probe_texto(res).lower()
    assert "quarentena" in texto or "quarantine" in texto or "2" in texto or "%" in texto
    inc = talvez_abrir(con, fonte, "suspect", [res])
    assert isinstance(inc, int) and inc > 0
    con.commit()
    run_ok = criar_run(con, fonte, "ok", fetched=100, quarantined=1)
    inserir_quarentena(con, fonte, run_ok, total=1)
    con.commit()
    res_ok = check_q(con, run_ok, fonte)
    assert probe_passou(res_ok), "quarentena de 1pct deveria passar"


def test_canario_contrato_exige_campos_obrigatorios():
    check = obter_funcao("check_contract_canary")
    bruto_ok = json.dumps(
        {"id": "1", "numero": "10/2026", "ano": 2026, "fase": {"fase": "aberto"}},
        ensure_ascii=False,
    ).encode("utf-8")
    assert probe_passou(check(bruto_ok, "application/json", ["id", "numero", "ano", "fase.fase"]))
    bruto_sem = json.dumps({"id": "1", "numero": "10/2026"}, ensure_ascii=False).encode("utf-8")
    res_falta = check(bruto_sem, "application/json", ["id", "numero", "ano"])
    assert not probe_passou(res_falta)
    texto = probe_texto(res_falta).lower()
    assert (
        "ano" in texto
        or "campo" in texto
        or "obrigatorio" in texto
        or "required" in texto
        or "canario" in texto
        or "contrato" in texto
    )
    assert not probe_passou(check(b"<html>nao e json</html>", "application/json", ["id"]))


def test_freshness_recente_passa_antiga_falha(con):
    check = obter_funcao("check_freshness")
    fonte = id_fonte(con)
    agora = datetime.now(UTC)
    run_id = criar_run(con, fonte, "ok")
    inserir_processo(con, fonte, run_id, agora.isoformat(), native_id="proc-recente")
    con.commit()
    assert probe_passou(check(con, fonte, 30))
    con.execute("DELETE FROM process WHERE source_id = ?", (fonte,))
    antiga = (agora - timedelta(days=60)).isoformat()
    inserir_processo(con, fonte, run_id, antiga, native_id="proc-antigo")
    con.commit()
    res = check(con, fonte, 30)
    assert not probe_passou(res)


def test_sanity_parse_dentro_e_fora_30pct(con):
    check = obter_funcao("check_parse_sanity")
    fonte = id_fonte(con)
    run_ant = criar_run(con, fonte, "ok", fetched=100)
    semear_total(con, fonte, 100, run_ant)
    con.commit()
    assert probe_passou(check(con, fonte, 110))
    assert not probe_passou(check(con, fonte, 200))
    assert not probe_passou(check(con, fonte, 50))


def test_evaluate_probes_todas_passam_e_omissao_falha(con):
    avaliar = obter_funcao("evaluate_probes")
    fonte = id_fonte(con)
    run_ant = criar_run(con, fonte, "ok", fetched=10)
    semear_base(con, fonte, 10, run_ant)
    run_id = criar_run(con, fonte, "ok", fetched=10, quarantined=0)
    semear_base(con, fonte, 10, run_id)
    agora = datetime.now(UTC).isoformat()
    for i in range(10):
        inserir_processo(con, fonte, run_id, agora, native_id=f"proc-{i}")
    con.commit()
    bruto = json.dumps({"id": "1", "numero": "10/2026", "ano": 2026}, ensure_ascii=False).encode("utf-8")
    resultados = avaliar(con, run_id, fonte, bruto, "application/json", ["id", "numero", "ano"])
    assert isinstance(resultados, list) and len(resultados) >= 3
    assert all(probe_passou(r) for r in resultados)
    omissos = avaliar(con, run_id, fonte, None, "application/json", ["id", "numero", "ano"])
    assert isinstance(omissos, list) and len(omissos) >= 1
    assert any(not probe_passou(r) for r in omissos)


def test_primeira_falha_abre_incidente_run_verde_fecha_e_zera(con):
    abrir = obter_funcao("open_incident")
    fechar = obter_funcao("close_incident")
    talvez = obter_funcao("maybe_open_incident")
    fonte = id_fonte(con)
    direta = abrir(con, fonte, "failed", "alta", "falha dura simulada")
    assert isinstance(direta, int) and direta > 0
    con.commit()
    con.execute("DELETE FROM incident WHERE source_id = ?", (fonte,))
    con.commit()
    criar_run(con, fonte, "failed")
    sonda_falha = criar_sonda(False, "piso")
    con.commit()
    inc = talvez(con, fonte, "failed", [sonda_falha])
    assert isinstance(inc, int) and inc > 0
    linha = con.execute("SELECT closed_at FROM incident WHERE id = ?", (inc,)).fetchone()
    assert linha is not None and linha["closed_at"] is None
    con.commit()
    criar_run(con, fonte, "ok")
    sonda_ok = criar_sonda(True, "piso")
    con.commit()
    nada = talvez(con, fonte, "ok", [sonda_ok])
    assert nada is None
    retorno_fechar = fechar(con, fonte, "failed")
    assert isinstance(retorno_fechar, int)
    con.commit()
    fechada = con.execute("SELECT closed_at, notify_count FROM incident WHERE id = ?", (inc,)).fetchone()
    assert fechada is not None and fechada["closed_at"] is not None
    assert int(fechada["notify_count"]) == 0


def test_tres_falhas_duras_marcam_degraded(con):
    talvez = obter_funcao("maybe_open_incident")
    marcar = obter_funcao("mark_degraded")
    limpar = obter_funcao("clear_degraded")
    esta = obter_funcao("is_degraded")
    fonte = id_fonte(con)
    assert not esta(con, fonte)
    for _ in range(3):
        criar_run(con, fonte, "failed")
        con.commit()
        talvez(con, fonte, "failed", [criar_sonda(False, "piso")])
        con.commit()
    assert esta(con, fonte)
    limpar(con, fonte)
    con.commit()
    assert not esta(con, fonte)
    marcar(con, fonte)
    con.commit()
    assert esta(con, fonte)
    limpar(con, fonte)
    con.commit()
    assert not esta(con, fonte)


def test_ok_zero_so_com_todas_sondas_passando(con):
    talvez = obter_funcao("maybe_open_incident")
    fechar = obter_funcao("close_incident")
    fonte = id_fonte(con)
    inc_omisso = talvez(con, fonte, "ok_zero", [])
    assert inc_omisso is not None, "ok_zero por omissao deveria abrir incidente"
    con.commit()
    for kind in ("failed", "suspect", "degraded"):
        try:
            fechar(con, fonte, kind)
        except Exception:
            pass
    con.execute("DELETE FROM incident WHERE source_id = ? AND closed_at IS NULL", (fonte,))
    con.commit()
    ok1 = criar_sonda(True, "canario")
    ok2 = criar_sonda(True, "piso")
    assert talvez(con, fonte, "ok_zero", [ok1, ok2]) is None
    con.commit()
    con.execute("DELETE FROM incident WHERE source_id = ?", (fonte,))
    con.commit()
    falha = criar_sonda(False, "piso")
    assert talvez(con, fonte, "ok_zero", [ok1, falha]) is not None


def test_degraded_rotula_ausencia_para_usuario(con):
    marcar = obter_funcao("mark_degraded")
    estado = obter_funcao("get_user_facing_status")
    fonte = id_fonte(con)
    run_ok = criar_run(con, fonte, "ok")
    agora = datetime.now(UTC).isoformat()
    inserir_processo(con, fonte, run_ok, agora, native_id="proc-valido")
    con.commit()
    marcar(con, fonte)
    con.commit()
    texto = estado(con, fonte)
    assert isinstance(texto, str) and texto.strip()
    baixo = texto.lower()
    assert "temporariamente indisponivel" in baixo
    assert "ultima coleta valida" in baixo
    assert any(ch.isdigit() for ch in texto)
