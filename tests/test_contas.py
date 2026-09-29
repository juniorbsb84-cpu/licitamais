"""Contas (auth/email) sem depender de servidor web: forca bruta no /vincular e token fora do log."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from licitamais.contas import auth, email
from licitamais.schema import init_schema


def test_vincular_bloqueia_forca_bruta_por_chat(tmp_path):
    """Codigo de 6 digitos sem limite de tentativa: chat atacante chutaria codigos e ligaria
    o proprio Telegram a conta de outro cliente. Max 5 erros por chat por hora."""
    con = sqlite3.connect(tmp_path / "db.sqlite")
    con.row_factory = sqlite3.Row
    init_schema(con)
    con.execute("INSERT INTO account(email,created_at) VALUES('a@x.com','2026-01-01')")
    conta = con.execute("SELECT id FROM account").fetchone()[0]
    certo = auth.codigo_telegram(con, conta)
    errado = f"{(int(certo) + 1) % 1000000:06d}"
    for _ in range(5):
        assert auth.vincular(con, errado, "999") is False
    assert auth.vincular(con, certo, "999") is False  # bloqueado mesmo com o codigo certo
    assert auth.vincular(con, certo, "111") is True  # outro chat nao e afetado


def test_codigo_telegram_usa_chave_fora_do_banco_e_outra_conexao_consegue_vincular(tmp_path):
    caminho = tmp_path / "contas.db"
    con = sqlite3.connect(caminho)
    init_schema(con)
    con.execute("INSERT INTO account(email,created_at) VALUES('b@x.com','2026-01-01')")
    con.commit()
    codigo = auth.codigo_telegram(con, 1)
    salvo = con.execute("SELECT code_hash FROM telegram_link").fetchone()[0]
    assert salvo != auth.hash_token(codigo)
    segredo = tmp_path / "contas.telegram-link.key"
    assert segredo.is_file() and segredo.stat().st_size == 32
    con.close()
    outra = sqlite3.connect(caminho)
    try:
        assert auth.vincular(outra, codigo, "chat-1") is True
    finally:
        outra.close()


def test_codigo_legado_ainda_funciona_ate_expirar(tmp_path):
    con = sqlite3.connect(tmp_path / "legado.db")
    init_schema(con)
    con.execute("INSERT INTO account(email,created_at) VALUES('c@x.com','2026-01-01')")
    codigo = "123456"
    con.execute(
        "INSERT INTO telegram_link(code_hash,account_id,expires_at) VALUES(?,?,?)",
        (auth.hash_token(codigo), 1, (datetime.now(UTC) + timedelta(minutes=5)).isoformat()),
    )
    con.commit()
    try:
        assert auth.vincular(con, codigo, "chat-legado") is True
    finally:
        con.close()


def test_codigo_telegram_colisao_e_gerada_novamente(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "colisao.db")
    init_schema(con)
    con.execute("INSERT INTO account(id,email,created_at) VALUES(1,'a@x.com','2026-01-01')")
    con.execute("INSERT INTO account(id,email,created_at) VALUES(2,'b@x.com','2026-01-01')")
    con.commit()
    numeros = iter((111111, 111111, 222222))
    monkeypatch.setattr(auth.secrets, "randbelow", lambda _: next(numeros))
    try:
        assert auth.codigo_telegram(con, 1) == "111111"
        assert auth.codigo_telegram(con, 2) == "222222"
    finally:
        con.close()


def test_a4_sem_smtp_e_sem_modo_dev_nao_loga_token(monkeypatch, caplog):
    monkeypatch.delenv("LICITAMAIS_SMTP_HOST", raising=False)
    monkeypatch.delenv("LICITAMAIS_DEV", raising=False)
    with caplog.at_level(logging.DEBUG):
        email.enviar_link("a@x.com", "TOKENSECRETO123")
    assert "TOKENSECRETO123" not in caplog.text
    monkeypatch.setenv("LICITAMAIS_DEV", "1")
    with caplog.at_level(logging.DEBUG):
        email.enviar_link("a@x.com", "TOKENSECRETO123")
    assert "TOKENSECRETO123" in caplog.text
