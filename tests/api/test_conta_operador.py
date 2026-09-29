import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi import HTTPException

from licitamais.api.deps import Conta
from licitamais.api.rotas.conta import NovoAlerta, criar


def test_criar_listar_apagar_alerta(cliente, db_path):
    r = cliente.post("/api/v2/alertas", json={"palavras": ["Manutenção de frota", "pneus"]})
    assert r.status_code == 201
    aid = r.json()["id"]
    assert cliente.get("/api/v2/alertas").json() == [
        {"id": aid, "palavras": ["manutenção de frota", "pneus"], "ativo": True, "telegram_vinculado": False}
    ]
    with sqlite3.connect(db_path) as con:
        filtro = json.loads(
            con.execute("SELECT filter_expr FROM alert_subscription WHERE id = ?", (aid,)).fetchone()[0]
        )
    assert filtro == {"keywords": ["manutenção de frota", "pneus"], "modalities": [], "entities": []}
    assert cliente.delete(f"/api/v2/alertas/{aid}").status_code == 204
    assert cliente.get("/api/v2/alertas").json() == []


def test_alerta_de_outra_conta_404(cliente, db_path):
    with sqlite3.connect(db_path) as con:
        con.execute("INSERT INTO account (id, email, created_at) VALUES (2, 'b@x.com', '2026-09-24')")
        con.execute(
            "INSERT INTO alert_subscription (id, kind, channel, filter_expr, enabled, account_id)"
            " VALUES (77, 'filtro', 'telegram', '{}', 1, 2)"
        )
        con.execute(
            "INSERT INTO alert_outbox (subscription_id, dedup_key, payload, status)"
            " VALUES (77, '77:1:new:r', '{}', 'pending')"
        )
    assert cliente.delete("/api/v2/alertas/77").status_code == 404
    with sqlite3.connect(db_path) as con:
        assert con.execute(
            "SELECT subscription_id, status FROM alert_outbox WHERE dedup_key = '77:1:new:r'"
        ).fetchone() == (77, "pending")


def test_apagar_alerta_descarta_pendencias_novas_e_antigas(cliente, db_path):
    aid = cliente.post("/api/v2/alertas", json={"palavras": ["pneus"]}).json()["id"]
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO alert_outbox (subscription_id, dedup_key, payload, status) VALUES (?, ?, '{}', 'pending')",
            (aid, f"{aid}:1:new:r"),
        )
        con.execute(
            "INSERT INTO alert_outbox (subscription_id, dedup_key, payload, status) VALUES (NULL, ?, '{}', 'pending')",
            (f"{aid}:2:new:r",),
        )
        con.execute(
            "INSERT INTO alert_outbox (subscription_id, dedup_key, payload, status) VALUES (?, ?, '{}', 'sent')",
            (aid, f"{aid}:3:new:r"),
        )
    assert cliente.delete(f"/api/v2/alertas/{aid}").status_code == 204
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM alert_outbox WHERE status = 'pending'").fetchone()[0] == 0
        assert con.execute("SELECT subscription_id FROM alert_outbox WHERE status = 'sent'").fetchone()[0] is None


def test_cota_de_alertas_resiste_a_criacao_concorrente(cliente, db_path):
    with sqlite3.connect(db_path) as con:
        for i in range(4):
            con.execute(
                "INSERT INTO alert_subscription (kind,channel,filter_expr,enabled,account_id)"
                " VALUES ('filtro','telegram',?,1,1)",
                (json.dumps({"keywords": [f"base{i}"]}),),
            )
    largada = Barrier(2)

    def criar_em_outra_conexao(palavra):
        con = sqlite3.connect(db_path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            largada.wait()
            try:
                criar(
                    NovoAlerta(palavras=[palavra]),
                    Conta(id=1, email="cliente@exemplo.com", operador=False, csrf=""),
                    con,
                )
                return 201
            except HTTPException as exc:
                return exc.status_code
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(criar_em_outra_conexao, ("novo-a", "novo-b")))
    assert sorted(resultados) == [201, 429]
    with sqlite3.connect(db_path) as con:
        assert (
            con.execute("SELECT COUNT(*) FROM alert_subscription WHERE account_id=1 AND enabled=1").fetchone()[0] == 5
        )


def test_alerta_invalido(cliente):
    for corpo in (
        {"palavras": []},
        {"palavras": ["a"]},
        {"palavras": ["x" * 41]},
        {"palavras": [f"p{i}x" for i in range(11)]},
    ):
        assert cliente.post("/api/v2/alertas", json=corpo).status_code == 422


def test_alerta_exige_csrf(cliente):
    del cliente.headers["X-CSRF"]
    assert cliente.post("/api/v2/alertas", json={"palavras": ["pneus"]}).status_code == 403


def test_codigo_telegram(cliente):
    d = cliente.post("/api/v2/conta/telegram").json()
    assert len(d["codigo"]) == 6 and d["codigo"].isdigit() and d["validade_minutos"] == 10


def test_saude_so_operador(cliente, monkeypatch):
    assert cliente.get("/api/v2/operador/saude").status_code == 403
    monkeypatch.setenv("LICITAMAIS_OPERADORES", "cliente@exemplo.com")
    d = cliente.get("/api/v2/operador/saude").json()
    assert {f["fonte"] for f in d["fontes"]} == {"brb", "senac"} and d["incidentes"] == []


def test_alerta_criado_depois_do_vinculo_herda_o_chat(cliente, db_path):
    # Vinculou o Telegram quando ainda nao tinha alerta: o primeiro alerta precisa sair com destino.
    with sqlite3.connect(db_path) as con:
        con.execute("INSERT INTO telegram_chat (chat_id, account_id, linked_at) VALUES ('555', 1, '2026-09-25')")
    r = cliente.post("/api/v2/alertas", json={"palavras": ["pneus"]})
    assert r.json()["telegram_vinculado"] is True
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT target FROM alert_subscription WHERE id = ?", (r.json()["id"],)).fetchone() == (
            "555",
        )
