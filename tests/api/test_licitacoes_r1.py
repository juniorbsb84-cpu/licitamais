"""Revisao 1 (C2/C4): q + ordem=prazo/abertura, escala sem limite de variaveis, relevancia por bm25."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import SESSAO, semear

from licitamais import init_schema
from licitamais.api import create_app
from licitamais.contas import auth


@pytest.fixture()
def banco_grande(tmp_path):
    caminho = str(tmp_path / "grande.db")
    con = sqlite3.connect(caminho)
    init_schema(con)
    semear(con)
    base = con.execute("SELECT MAX(id) FROM process").fetchone()[0] + 1
    con.executemany(
        "INSERT INTO process (id, source_id, source_native_id, org_id, number, object, modality_raw,"
        " opening_at_source, phase_current_label, phase_current_group, record_hash,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
        " VALUES (?, 1, ?, NULL, ?, ?, 'Pregão eletrônico', '2030-01-01', 'Edital Aberto', 'aberta', 'h', 1, 1, 1)",
        [(base + i, f"g{i}", f"{base + i:06d}/2030", f"servico {i}") for i in range(33000)],
    )
    con.commit()
    con.close()
    return caminho


@pytest.fixture()
def cliente_grande(banco_grande):
    c = TestClient(create_app(banco_grande))
    c.cookies.set("licitamais_session", SESSAO)
    c.headers["X-CSRF"] = auth.csrf(SESSAO)
    return c


@pytest.mark.parametrize("ordem", ["prazo", "abertura"])
def test_busca_com_ordem_nao_prazo_nao_da_500(cliente, ordem):
    r = cliente.get("/api/v2/licitacoes", params={"q": "limpeza", "ordem": ordem})
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_busca_33_mil_sem_limite_de_variaveis(cliente_grande):
    r = cliente_grande.get("/api/v2/licitacoes", params={"q": "servico"})
    assert r.status_code == 200
    assert r.json()["total"] == 33000


def _cliente_fts(tmp_path, objetos):
    caminho = str(tmp_path / "fts.db")
    con = sqlite3.connect(caminho)
    init_schema(con)
    semear(con)
    con.execute("DELETE FROM process WHERE id > 0")
    for i, obj in enumerate(objetos, 1):
        con.execute(
            "INSERT INTO process (id, source_id, source_native_id, org_id, number, object, modality_raw,"
            " opening_at_source, phase_current_label, phase_current_group, record_hash,"
            " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
            " VALUES (?, 1, ?, NULL, ?, ?, 'Pregão eletrônico', '2099-10-06', 'Edital Aberto', 'aberta', 'h', 1, 1, 1)",
            (i, f"f{i}", f"{i:03d}/2026", obj),
        )
    con.commit()
    con.close()
    c = TestClient(create_app(caminho))
    c.cookies.set("licitamais_session", SESSAO)
    c.headers["X-CSRF"] = auth.csrf(SESSAO)
    return c


def test_relevancia_bm25_antes_de_texto_longo(tmp_path):
    c = _cliente_fts(tmp_path, ["frota de carros e motos e caminhoes", "frota frota frota"])
    d = c.get("/api/v2/licitacoes", params={"q": "frota"}).json()
    assert [i["id"] for i in d["itens"]] == [2, 1]
