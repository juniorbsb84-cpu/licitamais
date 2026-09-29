"""Tokens de acesso e sessoes persistidos apenas como hashes."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SEGREDO_MEMORIA = secrets.token_bytes(32)


def agora():
    return datetime.now(UTC)


def hash_token(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()


def _segredo_codigo(con) -> bytes:
    banco = con.execute("PRAGMA database_list").fetchone()[2]
    if not banco:  # testes com SQLite em memoria
        return _SEGREDO_MEMORIA
    caminho = Path(banco).with_suffix(".telegram-link.key")
    try:
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as arquivo:
            arquivo.write(secrets.token_bytes(32))
            arquivo.flush()
            os.fsync(arquivo.fileno())
    for _ in range(20):
        segredo = caminho.read_bytes()
        if len(segredo) == 32:
            return segredo
        time.sleep(0.01)  # outro processo acabou de criar e ainda grava
    raise RuntimeError("segredo de vinculo Telegram invalido; nao gerar codigo fraco")


def _hash_codigo(con, codigo: str) -> str:
    return hmac.new(_segredo_codigo(con), codigo.encode(), hashlib.sha256).hexdigest()


def pedir_link(con, email: str, ip: str) -> str | None:
    email = email.strip().lower()
    if not email or "@" not in email or len(email) > 254:
        return None
    instante = agora()
    desde = (instante - timedelta(hours=1)).isoformat()
    con.execute("BEGIN IMMEDIATE")  # r44/A6: contagem e insercao atomicas (limite sem corrida)
    if (
        con.execute("SELECT COUNT(*) FROM login_token WHERE email=? AND created_at>=?", (email, desde)).fetchone()[0]
        >= 5
    ):
        con.rollback()
        return None
    if con.execute("SELECT COUNT(*) FROM login_token WHERE ip=? AND created_at>=?", (ip, desde)).fetchone()[0] >= 20:
        con.rollback()
        return None
    token = secrets.token_urlsafe(32)
    conta = con.execute("SELECT id FROM account WHERE email=?", (email,)).fetchone()
    con.execute(
        "INSERT INTO login_token(account_id,email,token_hash,expires_at,created_at,ip) VALUES(?,?,?,?,?,?)",
        (
            conta[0] if conta else None,
            email,
            hash_token(token),
            (instante + timedelta(minutes=15)).isoformat(),
            instante.isoformat(),
            ip,
        ),
    )
    con.commit()
    return token


def entrar(con, token: str) -> str | None:
    if not token:
        return None
    instante = agora().isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        linha = con.execute(
            "SELECT id,account_id,email FROM login_token WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
            (hash_token(token), instante),
        ).fetchone()
        if linha is None:
            con.rollback()
            return None
        conta_id = linha["account_id"]
        if conta_id is None:
            con.execute(
                "INSERT INTO account(email,created_at,last_login_at) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET last_login_at=excluded.last_login_at",
                (linha["email"], instante, instante),
            )
            conta_id = con.execute("SELECT id FROM account WHERE email=?", (linha["email"],)).fetchone()[0]
        else:
            con.execute("UPDATE account SET last_login_at=? WHERE id=?", (instante, conta_id))
        con.execute("UPDATE login_token SET used_at=?,account_id=? WHERE id=?", (instante, conta_id, linha["id"]))
        sessao = secrets.token_urlsafe(32)
        con.execute(
            "INSERT INTO session(account_id,session_hash,expires_at,created_at) VALUES(?,?,?,?)",
            (conta_id, hash_token(sessao), (agora() + timedelta(days=30)).isoformat(), instante),
        )
        con.commit()
        return sessao
    except Exception:
        con.rollback()
        raise


def conta_da_sessao(con, sessao: str):
    if not sessao:
        return None
    return con.execute(
        "SELECT account.id,account.email,session.session_hash FROM session JOIN account ON account.id=session.account_id WHERE session.session_hash=? AND session.expires_at>?",
        (hash_token(sessao), agora().isoformat()),
    ).fetchone()


def csrf(sessao: str) -> str:
    return hash_token("csrf:" + sessao)


def codigo_telegram(con, account_id: int) -> str:
    con.execute("DELETE FROM telegram_link WHERE account_id=?", (account_id,))
    validade = (agora() + timedelta(minutes=10)).isoformat()
    for _ in range(10):
        codigo = f"{secrets.randbelow(1000000):06d}"
        cur = con.execute(
            "INSERT OR IGNORE INTO telegram_link(code_hash,account_id,expires_at) VALUES(?,?,?)",
            (_hash_codigo(con, codigo), account_id, validade),
        )
        if cur.rowcount:
            con.commit()
            return codigo
    con.rollback()
    raise RuntimeError("nao foi possivel gerar codigo Telegram unico")


def vincular(con, codigo: str, chat: str) -> bool:
    if len(codigo) != 6 or not codigo.isdigit():
        return False
    con.execute("BEGIN IMMEDIATE")
    try:
        # forca bruta: 5 erros por chat por hora bloqueiam, mesmo com o codigo certo
        uma_hora = (agora() - timedelta(hours=1)).isoformat()
        erros = con.execute(
            "SELECT COUNT(*) FROM telegram_link_falha WHERE chat=? AND at>?", (chat, uma_hora)
        ).fetchone()[0]
        if erros >= 5:
            con.rollback()
            return False
        novo_hash = _hash_codigo(con, codigo)
        linha = con.execute(
            "SELECT account_id,code_hash FROM telegram_link WHERE code_hash IN (?,?) AND used_at IS NULL AND expires_at>?",
            (novo_hash, hash_token(codigo), agora().isoformat()),
        ).fetchone()
        if linha is None:
            con.execute("INSERT INTO telegram_link_falha(chat,at) VALUES(?,?)", (chat, agora().isoformat()))
            con.commit()
            return False
        account_id = int(linha[0])
        instante = agora().isoformat()
        con.execute("UPDATE telegram_link SET used_at=? WHERE code_hash=?", (instante, linha[1]))
        # Um chat pertence a uma conta por vez; revincular nao pode continuar entregando
        # alertas da conta anterior no mesmo destino.
        con.execute(
            "UPDATE alert_subscription SET target=NULL WHERE target=? AND account_id IS NOT NULL AND account_id<>?",
            (chat, account_id),
        )
        con.execute("DELETE FROM telegram_chat WHERE chat_id=? OR account_id=?", (chat, account_id))
        con.execute(
            "INSERT INTO telegram_chat(chat_id,account_id,linked_at) VALUES(?,?,?)", (chat, account_id, instante)
        )
        con.execute(
            "UPDATE alert_subscription SET target=? WHERE account_id=? AND channel='telegram'", (chat, account_id)
        )
        ativos = con.execute(
            "SELECT COUNT(*) FROM alert_subscription WHERE account_id=? AND enabled=1", (account_id,)
        ).fetchone()[0]
        for sub_id, habilitado in con.execute(
            "SELECT id,enabled FROM alert_subscription WHERE target=? AND channel='telegram' AND account_id IS NULL ORDER BY id",
            (chat,),
        ).fetchall():
            novo_estado = int(bool(habilitado) and ativos < 5)
            con.execute(
                "UPDATE alert_subscription SET account_id=?,kind='bot_filtro',enabled=? WHERE id=?",
                (account_id, novo_estado, sub_id),
            )
            ativos += novo_estado
        con.commit()
        return True
    except Exception:
        con.rollback()
        raise
