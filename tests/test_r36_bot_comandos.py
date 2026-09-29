"""R36: cadastro self-service pelo bot do Telegram (long polling getUpdates).

Sem webhook. Tudo dublado: nenhum teste toca a rede (requests.get/post
interceptados via monkeypatch).
"""

import json
import sqlite3

import pytest
import requests

import licitamais.alerts.bot_comandos as bot
from licitamais.contas import auth
from licitamais.schema import init_schema


class _Resp:
    def __init__(self, corpo, status=200):
        self._corpo = corpo
        self.status_code = status

    def json(self):
        return self._corpo


class HttpDublado:
    def __init__(self, lotes):
        self.lotes = list(lotes)
        self.posts = []
        self.get_params = []

    def get(self, url, params=None, timeout=None):
        self.get_params.append((url, dict(params or {})))
        lote = self.lotes.pop(0) if self.lotes else []
        return _Resp({"ok": True, "result": lote})

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, dict(json or {})))
        return _Resp({"ok": True, "result": {"message_id": 1}})


@pytest.fixture
def http(monkeypatch):
    dublado = HttpDublado([])
    monkeypatch.setattr(requests, "get", dublado.get)
    monkeypatch.setattr(requests, "post", dublado.post)
    return dublado


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    return con


def update(uid, chat_id, text):
    return {"update_id": uid, "message": {"message_id": 1, "chat": {"id": chat_id}, "text": text}}


def cliente():
    return bot.BotClient("TOKEN")


def processar(con, http, textos, chat=123, primeiro_id=1, offset=None):
    http.lotes.append([update(primeiro_id + i, chat, t) for i, t in enumerate(textos)])
    return bot.processar_lote(con, cliente(), offset)


def assinatura(con, chat="123"):
    return con.execute(
        "SELECT kind, channel, target, filter_expr, enabled, attrs"
        " FROM alert_subscription WHERE channel = 'telegram' AND target = ?",
        (chat,),
    ).fetchone()


def semear_preco(con, n=12):
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual) VALUES (1,'brb','api_json','x','v1')"
    )
    con.execute(
        'INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at)'
        " VALUES (1,1,'manual','v1','ok','2026-09-23')"
    )
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, number, year, object)"
        " VALUES (1,1,'p1','001',2026,'limpeza predial')"
    )
    con.commit()


def semear_contratos(con, n=12):
    for i in range(1, n + 1):
        nm = f"C{i:03d}"
        forn = f"Empresa {i:02d}"
        con.execute(
            "INSERT INTO contract (source_id, source_native_id, process_id, number, supplier_name_raw, value_cents) VALUES (1, ?, 1, ?, ?, ?)",
            (nm, nm, forn, i * 100000),
        )
    con.commit()


def test_start(http):
    con = nova_conexao()
    prox = processar(con, http, ["/start"])
    assert prox == 2
    _, carga = http.posts[0]
    assert "/palavras" in carga["text"]
    assert "/preco" in carga["text"]


def test_palavras_cria(http):
    con = nova_conexao()
    processar(con, http, ["/palavras limpeza, obra"])
    lin = assinatura(con)
    assert lin is not None
    assert (lin["kind"], lin["channel"], lin["target"]) == ("bot_filtro", "telegram", "123")
    assert json.loads(lin["filter_expr"]) == {"keywords": ["limpeza", "obra"], "modalities": [], "entities": []}
    assert lin["enabled"] == 1


def test_palavras_atualiza(http):
    con = nova_conexao()
    processar(con, http, ["/palavras a"], primeiro_id=1)
    processar(con, http, ["/palavras b, c"], primeiro_id=2)
    tot = con.execute("SELECT COUNT(*) FROM alert_subscription WHERE target = " + chr(39) + "123" + chr(39)).fetchone()[
        0
    ]
    assert tot == 1
    lin = assinatura(con)
    assert json.loads(lin["filter_expr"])["keywords"] == ["b", "c"]
    assert lin["enabled"] == 1


def test_vincular_associa_alerta_do_bot_a_conta_e_interface(http):
    con = nova_conexao()
    con.execute("INSERT INTO account (id,email,created_at) VALUES (1,'bot@exemplo.com','2026-09-25')")
    con.commit()
    processar(con, http, ["/palavras limpeza"], primeiro_id=1)
    assert con.execute("SELECT account_id FROM alert_subscription WHERE target='123'").fetchone()[0] is None
    codigo = auth.codigo_telegram(con, 1)
    processar(con, http, [f"/vincular {codigo}"], primeiro_id=2)
    assert con.execute("SELECT account_id FROM telegram_chat WHERE chat_id='123'").fetchone()[0] == 1
    assert con.execute("SELECT account_id FROM alert_subscription WHERE target='123'").fetchone()[0] == 1
    processar(con, http, ["/palavras obra"], primeiro_id=3)
    assert con.execute("SELECT COUNT(*) FROM alert_subscription WHERE target='123'").fetchone()[0] == 1


def test_vincular_sem_alerta_ainda_permite_criar_alerta_da_conta(http):
    con = nova_conexao()
    con.execute("INSERT INTO account (id,email,created_at) VALUES (1,'bot@exemplo.com','2026-09-25')")
    codigo = auth.codigo_telegram(con, 1)
    processar(con, http, [f"/vincular {codigo}", "/palavras pneus"], primeiro_id=1)
    assert con.execute("SELECT account_id FROM alert_subscription WHERE target='123'").fetchone()[0] == 1


def test_parar_pausa_todos_alertas_do_chat(http):
    con = nova_conexao()
    processar(con, http, ["/palavras limpeza"], primeiro_id=1)
    con.execute(
        "INSERT INTO alert_subscription (kind,channel,target,filter_expr,enabled)"
        " VALUES ('filtro','telegram','123','{}',1)"
    )
    con.commit()
    processar(con, http, ["/minhas", "/parar"], primeiro_id=2)
    assert "Seus alertas" in http.posts[-2][1]["text"]
    assert con.execute("SELECT COUNT(*) FROM alert_subscription WHERE target='123' AND enabled=1").fetchone()[0] == 0
    processar(con, http, ["/retomar"], primeiro_id=4)
    assert con.execute("SELECT COUNT(*) FROM alert_subscription WHERE target='123' AND enabled=1").fetchone()[0] == 2


def test_revincular_chat_remove_destino_da_conta_anterior(http):
    con = nova_conexao()
    con.execute("INSERT INTO account (id,email,created_at) VALUES (1,'a@exemplo.com','2026-09-25')")
    con.execute("INSERT INTO account (id,email,created_at) VALUES (2,'b@exemplo.com','2026-09-25')")
    con.execute(
        "INSERT INTO alert_subscription (id,kind,channel,enabled,account_id) VALUES (10,'filtro','telegram',1,1)"
    )
    con.execute(
        "INSERT INTO alert_subscription (id,kind,channel,enabled,account_id) VALUES (20,'filtro','telegram',1,2)"
    )
    codigo_a = auth.codigo_telegram(con, 1)
    processar(con, http, [f"/vincular {codigo_a}"], primeiro_id=1)
    codigo_b = auth.codigo_telegram(con, 2)
    processar(con, http, [f"/vincular {codigo_b}"], primeiro_id=2)
    assert con.execute("SELECT target FROM alert_subscription WHERE id=10").fetchone()[0] is None
    assert con.execute("SELECT target FROM alert_subscription WHERE id=20").fetchone()[0] == "123"
    assert con.execute("SELECT account_id FROM telegram_chat WHERE chat_id='123'").fetchone()[0] == 2


def test_vinculo_transfere_bot_antigo_sem_exceder_cota(http):
    con = nova_conexao()
    con.execute("INSERT INTO account (id,email,created_at) VALUES (1,'a@exemplo.com','2026-09-25')")
    for i in range(5):
        con.execute(
            "INSERT INTO alert_subscription (kind,channel,enabled,account_id,filter_expr)"
            " VALUES ('filtro','telegram',1,1,?)",
            (f'{{"keywords":["termo{i}"]}}',),
        )
    con.commit()
    processar(con, http, ["/palavras bot"], primeiro_id=1)
    codigo = auth.codigo_telegram(con, 1)
    processar(con, http, [f"/vincular {codigo}"], primeiro_id=2)
    assert con.execute("SELECT COUNT(*) FROM alert_subscription WHERE account_id=1 AND enabled=1").fetchone()[0] == 5
    assert tuple(
        con.execute("SELECT account_id,enabled FROM alert_subscription WHERE kind='bot_filtro'").fetchone()
    ) == (1, 0)


def test_palavras_vazia(http):
    con = nova_conexao()
    processar(con, http, ["/palavras"])
    assert assinatura(con) is None
    assert "/palavras" in http.posts[0][1]["text"]


def test_minhas(http):
    con = nova_conexao()
    processar(con, http, ["/palavras limpeza, obra"], primeiro_id=1)
    processar(con, http, ["/minhas"], primeiro_id=2)
    txt = http.posts[-1][1]["text"]
    assert "limpeza" in txt and "obra" in txt


def test_minhas_vazia(http):
    con = nova_conexao()
    processar(con, http, ["/minhas"])
    assert "ainda nao" in http.posts[0][1]["text"].lower()


def test_parar(http):
    con = nova_conexao()
    processar(con, http, ["/palavras limpeza"], primeiro_id=1)
    processar(con, http, ["/parar"], primeiro_id=2)
    assert assinatura(con)["enabled"] == 0
    processar(con, http, ["/minhas"], primeiro_id=3)
    assert "pausad" in http.posts[-1][1]["text"].lower()


def test_preco_top10(http):
    con = nova_conexao()
    semear_preco(con)
    semear_contratos(con)
    processar(con, http, ["/preco limpeza"])
    txt = http.posts[0][1]["text"]
    assert "Empresa 12" in txt
    assert "Empresa 03" in txt
    assert "Empresa 02" not in txt
    assert "Empresa 01" not in txt


def test_preco_vazio(http):
    con = nova_conexao()
    semear_preco(con)
    semear_contratos(con)
    processar(con, http, ["/preco asfalto"])
    assert "nada encontrado" in http.posts[0][1]["text"].lower()


def test_preco_curinga_e_texto_longo_nao_varrem_tudo(http):
    con = nova_conexao()
    semear_preco(con)
    semear_contratos(con)
    processar(con, http, ["/preco %", "/preco " + "a" * 101])
    assert "nada encontrado" in http.posts[0][1]["text"].lower()
    assert "muito longo" in http.posts[1][1]["text"].lower()


def test_offset(http):
    con = nova_conexao()
    prox = processar(con, http, ["/start", "/minhas"], primeiro_id=7, offset=7)
    assert prox == 9
    url, params = http.get_params[0]
    assert "getUpdates" in url
    assert params.get("offset") == 7


def test_sem_texto(http):
    con = nova_conexao()
    http.lotes.append([{"update_id": 4}, {"update_id": 5, "message": {"message_id": 9, "chat": {"id": 123}}}])
    prox = bot.processar_lote(con, cliente(), None)
    assert prox == 6
    assert http.posts == []


def test_desconhecido(http):
    con = nova_conexao()
    processar(con, http, ["/xyz"])
    assert "/palavras" in http.posts[0][1]["text"]


def test_vazio(http):
    con = nova_conexao()
    assert bot.processar_lote(con, cliente(), 9) == 9
    assert http.posts == []


def test_off_arquivo(tmp_path):
    alvo = str(tmp_path / "off")
    assert bot.carregar_offset(alvo) is None
    bot.salvar_offset(alvo, 41)
    assert bot.carregar_offset(alvo) == 41


def test_main_once(tmp_path, http, monkeypatch):
    db = tmp_path / "t.db"
    sqlite3.connect(str(db)).close()
    http.lotes.append([update(1, 99, "/palavras limpeza")])
    monkeypatch.setenv("LICITAMAIS_TELEGRAM_TOKEN", "TOKEN")
    rc = bot.main(["--db", str(db), "--once", "--offset-file", str(tmp_path / "off")])
    assert rc == 0
    c2 = sqlite3.connect(str(db))
    c2.row_factory = sqlite3.Row
    lin = c2.execute("SELECT target, filter_expr FROM alert_subscription").fetchone()
    c2.close()
    assert lin["target"] == "99"
    assert json.loads(lin["filter_expr"])["keywords"] == ["limpeza"]
    assert http.posts and http.posts[0][1]["chat_id"] == 99
