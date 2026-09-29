"""Fase vermelha do TDD para registro de fontes e sondas (T18)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3

import pytest

import licitamais.adapters.brb as modulo_brb
import licitamais.adapters.sistema_industria as modulo_si
from licitamais.__main__ import main
from licitamais.fontes import _FONTES
from licitamais.schema import init_schema

# T18 nasceu com 2 fontes; M6 acrescentou senac e iges. O contrato e "todas as oficiais".
N = len(_FONTES)
CODIGOS = sorted(f[0] for f in _FONTES)

MODULO_FONTES = "licitamais.fontes.__init__"

BASE_URL_ESPERADA = {
    "brb": "https://pdd.brb.com.br/PLC",
    "sistema_industria": "https://compras.sistemaindustria.com.br/compras",
}


def obter_registrar_fontes():
    """Devolve registrar_fontes ou falha pela ausencia do codigo de producao."""
    spec = importlib.util.find_spec(MODULO_FONTES)
    assert spec is not None, "O modulo de producao licitamais/fontes/__init__.py ainda nao existe."
    modulo = importlib.import_module(MODULO_FONTES)
    func = getattr(modulo, "registrar_fontes", None)
    assert callable(func), "A funcao de producao registrar_fontes ainda nao existe em licitamais/fontes/__init__.py."
    return func


def versao_esperada(codigo):
    if codigo == "brb":
        return modulo_brb.BRBAdapter.adapter_version
    return modulo_si.SistemaIndustriaAdapter.adapter_version


def campos_obrigatorios(codigo):
    if codigo == "brb":
        modulo = modulo_brb
    else:
        modulo = modulo_si
    for nome in (
        "CAMPOS_SONDA",
        "_CAMPOS_OBRIGATORIOS",
        "CAMPOS_OBRIGATORIOS",
    ):  # CAMPOS_SONDA: payload real (2026-09-23)
        valor = getattr(modulo, nome, None)
        if valor:
            return list(valor)
    return ["id", "numero"]


@pytest.fixture()
def con():
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    yield conexao
    conexao.close()


def linha_fonte(conexao, codigo):
    linha = conexao.execute(
        "SELECT code, transport, base_url, adapter_version_atual, enabled FROM source WHERE code = ?",
        (codigo,),
    ).fetchone()
    assert linha is not None, f"fonte '{codigo}' nao foi criada por registrar_fontes"
    return linha


def linha_probe(conexao, codigo):
    fonte_id = conexao.execute("SELECT id FROM source WHERE code = ?", (codigo,)).fetchone()
    assert fonte_id is not None
    linhas = conexao.execute(
        "SELECT min_rows_pct, required_fields, max_staleness_days, max_quarantine_pct"
        " FROM source_probe WHERE source_id = ?",
        (int(fonte_id[0]),),
    ).fetchall()
    assert len(linhas) == 1, f"fonte '{codigo}' deveria ter exatamente 1 linha em source_probe"
    return linhas[0]


def conferir_fonte(conexao, codigo):
    linha = linha_fonte(conexao, codigo)
    assert linha["transport"] == "api_json"
    assert linha["base_url"] == BASE_URL_ESPERADA[codigo]
    assert linha["adapter_version_atual"] == versao_esperada(codigo)
    assert int(linha["enabled"]) == 1


def conferir_probe(conexao, codigo):
    sonda = linha_probe(conexao, codigo)
    assert abs(float(sonda["min_rows_pct"]) - 0.85) < 1e-9
    assert int(sonda["max_staleness_days"]) == 30
    assert abs(float(sonda["max_quarantine_pct"]) - 0.02) < 1e-9
    try:
        campos = json.loads(str(sonda["required_fields"]))
    except (TypeError, ValueError):
        assert False, f"required_fields da fonte '{codigo}' nao e JSON valido"
    assert isinstance(campos, list) and len(campos) > 0, (
        f"required_fields da fonte '{codigo}' deveria ser lista nao vazia"
    )
    assert all(isinstance(campo, str) and campo.strip() for campo in campos)
    for esperado in campos_obrigatorios(codigo):
        assert esperado in campos, f"required_fields da fonte '{codigo}' deveria conter '{esperado}'"


def test_registrar_fontes_cria_duas_fontes_habilitadas(con):
    registrar_fontes = obter_registrar_fontes()
    criadas = registrar_fontes(con)
    assert criadas == N
    total = con.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    assert total == N
    conferir_fonte(con, "brb")
    conferir_fonte(con, "sistema_industria")


def test_registrar_fontes_cria_uma_probe_por_fonte(con):
    registrar_fontes = obter_registrar_fontes()
    assert registrar_fontes(con) == N
    conferir_probe(con, "brb")
    conferir_probe(con, "sistema_industria")


def test_registrar_fontes_idempotente_preserva_ajuste_manual(con):
    registrar_fontes = obter_registrar_fontes()
    assert registrar_fontes(con) == N
    con.commit()
    con.execute("UPDATE source SET enabled = 0 WHERE code = 'brb'")
    con.commit()
    segunda = registrar_fontes(con)
    assert segunda == 0
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == N
    assert con.execute("SELECT COUNT(*) FROM source_probe").fetchone()[0] == N
    enabled_brb = con.execute("SELECT enabled FROM source WHERE code = 'brb'").fetchone()[0]
    assert int(enabled_brb) == 0, "segunda chamada nao deveria reabilitar fonte ajustada manualmente"
    enabled_si = con.execute("SELECT enabled FROM source WHERE code = 'sistema_industria'").fetchone()[0]
    assert int(enabled_si) == 1


def test_cli_init_cria_banco_com_fontes_sem_rede(tmp_path, monkeypatch):

    def _proibe_rede(*args, **kwargs):
        raise AssertionError("rede nao deve ser usada no --init")

    monkeypatch.setattr("requests.sessions.Session.request", _proibe_rede)
    try:
        import licitamais.scheduler as scheduler

        if hasattr(scheduler, "run_scheduler"):
            monkeypatch.setattr(
                scheduler,
                "run_scheduler",
                lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("nao deve coletar no --init")),
            )
    except ImportError:
        pass

    db = tmp_path / "licitamais.db"
    assert not db.exists()
    try:
        codigo = main(["--db", str(db), "--init"])
    except SystemExit as exc:
        assert False, f"CLI --init saiu com SystemExit({exc.code}); esperado retorno 0"
    assert codigo == 0
    assert db.is_file()
    conexao = sqlite3.connect(str(db))
    try:
        conexao.row_factory = sqlite3.Row
        codigos = sorted(linha[0] for linha in conexao.execute("SELECT code FROM source").fetchall())
        assert codigos == CODIGOS
        conferir_fonte(conexao, "brb")
        conferir_fonte(conexao, "sistema_industria")
        conferir_probe(conexao, "brb")
        conferir_probe(conexao, "sistema_industria")
        runs = conexao.execute("SELECT COUNT(*) FROM source_run").fetchone()[0]
        assert runs == 0, "--init nao deveria coletar nenhum run"
    finally:
        conexao.close()


def test_registrar_fontes_recria_probe_ausente_sem_duplicar_fonte(con):
    registrar_fontes = obter_registrar_fontes()
    assert registrar_fontes(con) == N
    con.commit()
    con.execute("UPDATE source SET enabled = 0 WHERE code = 'brb'")
    con.execute("DELETE FROM source_probe WHERE source_id = (SELECT id FROM source WHERE code = 'brb')")
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == N
    assert con.execute("SELECT COUNT(*) FROM source_probe").fetchone()[0] == N - 1
    recriadas = registrar_fontes(con)
    con.commit()
    assert recriadas == 1, "fonte existente sem probe deveria ter a sonda recriada (retorno esperado 1)"
    assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == N, "reparo da sonda nao deve duplicar fonte"
    assert con.execute("SELECT COUNT(*) FROM source_probe").fetchone()[0] == N
    conferir_probe(con, "brb")
    conferir_probe(con, "sistema_industria")
    enabled_brb = con.execute("SELECT enabled FROM source WHERE code = 'brb'").fetchone()[0]
    assert int(enabled_brb) == 0, "reparo da sonda nao deve sobrescrever enabled ajustado manualmente"
