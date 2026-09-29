from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from licitamais.evidencia import termos_chave
from licitamais.situacao import hoje_brasil


def _processo(db_path, pid, objeto, abertura, grupo="aberta"):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, number, object, modality_raw, opening_at_source,"
        " phase_current_label, phase_current_group, record_hash, first_seen_run_id, last_seen_run_id,"
        " last_changed_run_id) VALUES (?, 2, ?, ?, ?, 'Pregão eletrônico', ?, 'Edital Aberto', ?, 'h', 1, 1, 1)",
        (pid, f"p{pid}", f"{pid:03d}/2026", objeto, abertura, grupo),
    )
    con.commit()
    con.close()


def test_termos_chave_ignora_palavras_genericas():
    assert termos_chave("Contratação de empresa para limpeza hospitalar das unidades", 3) == ["hospitalar", "limpeza"]


def test_evidencia_usa_compras_parecidas_do_historico(cliente, db_path):
    _processo(db_path, 5, "Limpeza hospitalar das unidades do DF", "2099-11-01")
    d = cliente.get("/api/v2/evidencias", params={"ids": [5, 4]}).json()
    por_id = {e["id"]: e for e in d}
    assert por_id[5]["parecidas"] == 1
    assert por_id[5]["valor_mediano"] == 50000.0
    assert por_id[5]["desde"] == 2020
    assert por_id[4]["parecidas"] == 0 and por_id[4]["valor_mediano"] is None


def test_evidencias_exige_sessao_e_limita_ids(anonimo, cliente):
    assert anonimo.get("/api/v2/evidencias", params={"ids": [1]}).status_code == 401
    assert cliente.get("/api/v2/evidencias", params={"ids": list(range(51))}).status_code == 422


def test_resumo_traz_prazos_dos_proximos_14_dias(cliente, db_path):
    hoje = date.fromisoformat(hoje_brasil())
    _processo(db_path, 6, "Pintura", (hoje + timedelta(days=2)).isoformat())
    _processo(db_path, 7, "Pintura externa", (hoje + timedelta(days=2)).isoformat() + "T10:00:00")
    _processo(db_path, 8, "Fora da janela", (hoje + timedelta(days=20)).isoformat())
    _processo(db_path, 9, "Encerrada no prazo", (hoje + timedelta(days=3)).isoformat(), grupo="encerrada")
    prazos = cliente.get("/api/v2/resumo").json()["prazos"]
    assert len(prazos) == 14 and prazos[0]["data"] == hoje.isoformat()
    assert prazos[2] == {"data": (hoje + timedelta(days=2)).isoformat(), "qtd": 2}
    assert sum(p["qtd"] for p in prazos) == 2
