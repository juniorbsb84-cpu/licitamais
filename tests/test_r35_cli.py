"""R35: CLI - exit code de partial/failed/suspect e --preco (historico de preco).

Exit code: partial SEM erro permanente sai 0 (hoje sai 1 e o Agendador marca
falha a cada run de backfill por causa do teto de chamadas); failed e suspect
sempre saem 1. --preco lista as views de preco (migracao 003) filtrando
objeto/descricao LIKE termo, com valor em formato BR e min/mediana/maximo.
"""

import re
import sqlite3

from licitamais.__main__ import main
from licitamais.runner import RunCounts, RunOutcome
from licitamais.schema import init_schema

TETO = "teto max_calls_per_run atingido (60 chamadas), coleta incompleta"
TRANSIENTE = "SSLError: HTTPSConnectionPool(host='x'): Max retries exceeded"
PERMANENTE = "HTTP 404 em /service/api/licitacoes/regional/AC"


def _cli_com_runs(tmp_path, monkeypatch, resultados):
    """resultados: [(codigo_fonte, status, error)] - um run_source stub por fonte."""
    for variavel in ("LICITAMAIS_TELEGRAM_TOKEN", "LICITAMAIS_TELEGRAM_OPERADOR"):
        monkeypatch.delenv(variavel, raising=False)
    db = tmp_path / "licitamais.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    init_schema(con)
    for codigo, _status, _erro in resultados:
        con.execute(
            "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
            " VALUES (?, 'api_json', 'https://fonte.test', 'v1')",
            (codigo,),
        )
    con.commit()
    con.close()
    fila = list(resultados)

    def run_source(con, source, cfg):
        _codigo, status, erro = fila.pop(0)
        return RunOutcome(source.id, source.id, source.code, status, RunCounts(), error=erro)

    monkeypatch.setattr("licitamais.runner.run_source", run_source)
    return main(["--db", str(db)])


def test_partial_por_teto_sai_zero(tmp_path, monkeypatch):
    # backfill: o Agendador marcava falha a cada run por causa do teto
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "partial", TETO)]) == 0


def test_partial_transitorio_sai_zero(tmp_path, monkeypatch):
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "partial", TRANSIENTE)]) == 0


def test_partial_sem_erro_sai_zero(tmp_path, monkeypatch):
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "partial", None)]) == 0


def test_partial_com_erro_permanente_sai_um(tmp_path, monkeypatch):
    # 404 em descoberta e defeito permanente (REVISAO 2026-09-23, C3)
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "partial", PERMANENTE)]) == 1


def test_failed_sai_um(tmp_path, monkeypatch):
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "failed", "handshake recusado")]) == 1


def test_suspect_sai_um(tmp_path, monkeypatch):
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "suspect", "sem sonda configurada")]) == 1


def test_ok_sai_zero(tmp_path, monkeypatch):
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "ok", None)]) == 0


def test_ok_zero_sai_zero(tmp_path, monkeypatch):
    assert _cli_com_runs(tmp_path, monkeypatch, [("brb", "ok_zero", None)]) == 0


def test_mistura_de_ok_com_partial_por_teto_sai_zero(tmp_path, monkeypatch):
    resultados = [("brb", "ok", None), ("sistema_industria", "partial", TETO)]
    assert _cli_com_runs(tmp_path, monkeypatch, resultados) == 0


def test_mistura_com_failed_sai_um(tmp_path, monkeypatch):
    resultados = [("brb", "ok", None), ("sistema_industria", "failed", "boom")]
    assert _cli_com_runs(tmp_path, monkeypatch, resultados) == 1


def _banco_preco(tmp_path):
    db = tmp_path / "preco.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
        " VALUES ('brb', 'api_json', 'https://brb.test', 'v1')"
    )
    con.execute(
        "INSERT INTO organization (id, cnpj, name_raw, name_norm, kind_hint)"
        " VALUES (1, '12345678000190', 'Fornecedor Um', 'FORNECEDOR UM', 'fornecedor')"
    )
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, number, year, object)"
        " VALUES (1, 1, 'p1', '001/2026', 2026, 'Limpeza e conservacao de escritorios')"
    )
    con.execute(
        "INSERT INTO contract (id, source_id, source_native_id, process_id,"
        " supplier_org_id, supplier_name_raw, value_cents, signed_at_source)"
        " VALUES (1, 1, 'c1', 1, 1, 'Fornecedor Um', 123456, '2026-01-15')"
    )
    con.execute(
        "INSERT INTO item (id, source_id, source_native_id, process_id,"
        " description, qty, unit) VALUES (10, 1, 'i10', 1, 'Papel A4', 10, 'un')"
    )
    con.execute(
        "INSERT INTO result (id, source_id, source_native_id, process_id, type,"
        " decided_at_source) VALUES (20, 1, 'r20', 1, 'adjudicacao', '2026-02-20')"
    )
    con.execute(
        "INSERT INTO award (id, result_id, process_id, item_id, supplier_org_id,"
        " supplier_name_raw, amount_cents, qty_awarded)"
        " VALUES (30, 20, 1, 10, 1, 'Fornecedor Um', 50000, 10)"
    )
    con.commit()
    con.close()
    return db


def _semear_contratos(db, objeto, valores, datas):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, number, year, object)"
        " VALUES (1, 1, 'p1', '001/2026', 2026, ?)",
        (objeto,),
    )
    for n, (valor, data) in enumerate(zip(valores, datas, strict=False), start=1):
        con.execute(
            "INSERT INTO contract (id, source_id, source_native_id, process_id,"
            " supplier_org_id, supplier_name_raw, value_cents, signed_at_source)"
            " VALUES (?, 1, ?, 1, 1, 'Fornecedor Um', ?, ?)",
            (n, f"c{n}", valor, data),
        )
    con.commit()
    con.close()


def test_preco_lista_contrato_filtrando_objeto(tmp_path, capsys):
    db = _banco_preco(tmp_path)
    # LIKE do SQLite ignora caixa em ASCII
    assert main(["--db", str(db), "--preco", "LiMpEzA"]) == 0
    saida = capsys.readouterr().out
    assert "2026-01-15" in saida
    assert "brb" in saida
    assert "Fornecedor Um" in saida
    assert "12345678000190" in saida
    assert "R$ 1.234,56" in saida
    assert "Papel A4" not in saida


def test_preco_lista_item_filtrando_descricao_com_cnpj_e_data(tmp_path, capsys):
    db = _banco_preco(tmp_path)
    assert main(["--db", str(db), "--preco", "papel"]) == 0
    saida = capsys.readouterr().out
    assert "2026-02-20" in saida  # decided_at_source via award
    assert "R$ 500,00" in saida
    assert "12345678000190" in saida  # organization via supplier_org_id
    assert "Limpeza" not in saida


def test_preco_sem_correspondencia_sai_zero(tmp_path, capsys):
    db = _banco_preco(tmp_path)
    assert main(["--db", str(db), "--preco", "telefonia"]) == 0
    saida = capsys.readouterr().out
    assert "0 ocorrencia" in saida
    assert "R$ 1.234,56" not in saida


def test_preco_estatisticas_min_mediana_maximo(tmp_path, capsys):
    db = tmp_path / "stats.db"
    con = sqlite3.connect(db)
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
        " VALUES ('brb', 'api_json', 'https://brb.test', 'v1')"
    )
    con.execute(
        "INSERT INTO organization (id, cnpj, name_raw, name_norm, kind_hint)"
        " VALUES (1, '12345678000190', 'Fornecedor Um', 'FORNECEDOR UM', 'fornecedor')"
    )
    con.commit()
    con.close()
    _semear_contratos(
        db,
        "Engenharia reversa de equipamentos",
        [100000, 300000, 200000],
        ["2026-03-01", "2026-01-01", "2026-02-01"],
    )
    assert main(["--db", str(db), "--preco", "reversa"]) == 0
    saida = capsys.readouterr().out
    assert "minimo R$ 1.000,00" in saida
    assert "mediana R$ 2.000,00" in saida
    assert "maximo R$ 3.000,00" in saida


def test_preco_mostra_no_maximo_50_linhas_e_estatistica_sobre_tudo(tmp_path, capsys):
    db = tmp_path / "limite.db"
    con = sqlite3.connect(db)
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
        " VALUES ('brb', 'api_json', 'https://brb.test', 'v1')"
    )
    con.execute(
        "INSERT INTO organization (id, cnpj, name_raw, name_norm, kind_hint)"
        " VALUES (1, '12345678000190', 'Fornecedor Um', 'FORNECEDOR UM', 'fornecedor')"
    )
    con.commit()
    con.close()
    # 50 recentes + 1 antiga (99.999,00) que cai fora das 50 exibidas
    _semear_contratos(
        db,
        "Materiais de escritorio",
        [100000] * 50 + [9999900],
        ["2026-01-01"] * 50 + ["2025-01-01"],
    )
    assert main(["--db", str(db), "--preco", "escritorio"]) == 0
    saida = capsys.readouterr().out
    linhas = [linha for linha in saida.splitlines() if re.match(r"^\d{4}-\d{2}-\d{2} \|", linha)]
    assert len(linhas) == 50, f"esperava 50 linhas de dados, vi {len(linhas)}"
    assert "51 ocorrencia" in saida
    assert "99.999,00" in saida, "maximo deve cobrir todas as 51, nao so as exibidas"
