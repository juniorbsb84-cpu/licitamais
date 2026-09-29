"""Ordem 'prazo': abertas com data mais proxima primeiro; sem data nunca na frente."""

import sqlite3


def test_prazo_poe_data_antes_de_sem_data(cliente, db_path):
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO process (id, source_id, source_native_id, object, opening_at_source, phase_current_label,"
            " phase_current_group, record_hash, first_seen_run_id, last_seen_run_id, last_changed_run_id)"
            " VALUES (9, 1, 'p9', 'Dispensa sem data', NULL, 'Edital Aberto', 'aberta', 'h', 1, 1, 1),"
            " (10, 1, 'p10', 'Pregao proximo', '2099-01-02', 'Em processo', 'andamento', 'h', 1, 1, 1)"
        )
    itens = cliente.get("/api/v2/licitacoes").json()["itens"]
    ids = [i["id"] for i in itens]
    assert ids[:3] == [10, 2, 1], ids  # 2099-01-02, 2099-09-01, 2099-10-06
    assert next(i for i in itens if i["id"] == 9)["situacao"] == "desconhecida"
