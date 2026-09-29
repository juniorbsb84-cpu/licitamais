"""Cadastro self-service pelo bot do Telegram (long polling getUpdates).

Sem webhook: o processo consulta getUpdates com offset persistido em arquivo
e responde via sendMessage. Entrada: python -m licitamais.alerts.bot_comandos
--db X (token em LICITAMAIS_TELEGRAM_TOKEN).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime

import requests

from licitamais.contas.auth import vincular
from licitamais.schema import init_schema

BASE_URL_PADRAO = "https://api.telegram.org"
LIMITE_PRECO = 10

TEXTO_COMANDOS = (
    "/palavras limpeza, obra - criar ou trocar sua assinatura\n"
    "/minhas - ver o que voce acompanha\n"
    "/parar - pausar os avisos\n"
    "/retomar - reativar seus avisos pausados\n"
    "/preco limpeza - top 10 contratos passados com esse termo\n"
    "/vincular 123456 - ligar este chat a sua conta\n"
    "/start - ver esta ajuda"
)

TEXTO_START = (
    "Sou o bot do LicitamAIs: aviso neste chat quando sair licitacao "
    "do regime privado (Sistema S e outras) com as palavras que voce escolher.\n\n" + TEXTO_COMANDOS
)

TEXTO_AJUDA = "Nao entendi. Tente um destes:\n" + TEXTO_COMANDOS


class BotClient:
    def __init__(self, token: str, base_url: str = BASE_URL_PADRAO) -> None:
        self.token = token
        self.base_url = (base_url or BASE_URL_PADRAO).rstrip("/")

    def _url(self, metodo: str) -> str:
        return f"{self.base_url}/bot{self.token}/{metodo}"

    def get_updates(self, offset=None, timeout: int = 30) -> list:
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            resposta = requests.get(self._url("getUpdates"), params=params, timeout=timeout + 10)
        except Exception:
            return []
        try:
            corpo = resposta.json()
        except Exception:
            return []
        if not isinstance(corpo, dict) or not corpo.get("ok"):
            return []
        resultado = corpo.get("result")
        return resultado if isinstance(resultado, list) else []

    def send_message(self, chat_id, texto: str) -> bool:
        try:
            resposta = requests.post(
                self._url("sendMessage"),
                json={"chat_id": chat_id, "text": texto},
                timeout=15,
            )
        except Exception:
            return False
        try:
            corpo = resposta.json()
        except Exception:
            return False
        if isinstance(corpo, dict) and "ok" in corpo:
            return bool(corpo["ok"])
        return False


def carregar_offset(caminho: str):
    try:
        with open(caminho, encoding="utf-8") as fh:
            texto = fh.read().strip()
    except OSError:
        return None
    try:
        return int(texto)
    except ValueError:
        return None


def salvar_offset(caminho: str, offset: int) -> None:
    diretorio = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(diretorio, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write(str(int(offset)))


def _reais(valor) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "R$ ?"
    texto = f"{numero:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _palavras_do_filtro(filtro_expr) -> list:
    try:
        dados = json.loads(filtro_expr or "{}")
    except Exception:
        return []
    if not isinstance(dados, dict):
        return []
    palavras = dados.get("keywords") or []
    if not isinstance(palavras, (list, tuple)):
        return []
    return [str(x) for x in palavras if str(x).strip()]


def _cmd_palavras(con, chat, args):
    palavras = [x.strip() for x in args.split(",") if x.strip()]
    if not palavras:
        return "Use assim: /palavras limpeza, vigilancia, obra"
    filtro = json.dumps({"keywords": palavras, "modalities": [], "entities": []}, ensure_ascii=False)
    agora = datetime.now(UTC).isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        vinculo = con.execute("SELECT account_id FROM telegram_chat WHERE chat_id = ?", (chat,)).fetchone()
        account_id = int(vinculo[0]) if vinculo else None
        if account_id is None:
            donos = con.execute(
                "SELECT DISTINCT account_id FROM alert_subscription WHERE target=? AND account_id IS NOT NULL", (chat,)
            ).fetchall()
            if len(donos) == 1:
                account_id = int(donos[0][0])  # vinculos anteriores a migracao 009
        ex = con.execute(
            "SELECT id,enabled FROM alert_subscription WHERE channel='telegram' AND target=? AND kind='bot_filtro' ORDER BY id LIMIT 1",
            (chat,),
        ).fetchone()
        if ex is None:
            ex = con.execute(
                "SELECT id,enabled FROM alert_subscription WHERE channel='telegram' AND target=? AND account_id IS NULL ORDER BY id LIMIT 1",
                (chat,),
            ).fetchone()
        if account_id is not None:
            ativos = con.execute(
                "SELECT COUNT(*) FROM alert_subscription WHERE account_id=? AND enabled=1", (account_id,)
            ).fetchone()[0]
            if ativos >= 5 and (ex is None or not ex[1]):
                con.rollback()
                return "Limite de 5 alertas ativos. Apague um alerta no site antes de criar outro."
        if ex is None:
            con.execute(
                "INSERT INTO alert_subscription (kind, channel, target, filter_expr, enabled, created_at, attrs, account_id)"
                " VALUES ('bot_filtro', 'telegram', ?, ?, 1, ?, ?, ?)",
                (chat, filtro, agora, filtro, account_id),
            )
        else:
            con.execute(
                "UPDATE alert_subscription SET filter_expr=?,attrs=?,enabled=1,kind='bot_filtro',account_id=? WHERE id=?",
                (filtro, filtro, account_id, ex[0]),
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return "Pronto! Vou avisar neste chat sobre: " + ", ".join(palavras) + "."


def _cmd_minhas(con, chat):
    linhas = con.execute(
        "SELECT filter_expr,enabled FROM alert_subscription WHERE channel='telegram' AND target=? ORDER BY id", (chat,)
    ).fetchall()
    if not linhas:
        return "Voce ainda nao tem assinatura. Use /palavras limpeza, obra para comecar."
    descricoes = []
    for filtro, habilitado in linhas:
        pals = _palavras_do_filtro(filtro)
        desc = ", ".join(pals) if pals else "(sem palavras-chave: vale tudo)"
        descricoes.append(("Ativo: " if habilitado else "Pausado: ") + desc)
    return "Seus alertas:\n" + "\n".join(descricoes)


def _cmd_parar(con, chat):
    if (
        con.execute("SELECT 1 FROM alert_subscription WHERE channel='telegram' AND target=?", (chat,)).fetchone()
        is None
    ):
        return "Voce nao tem assinatura ativa. Use /palavras ... para criar uma."
    con.execute("UPDATE alert_subscription SET enabled=0 WHERE channel='telegram' AND target=?", (chat,))
    con.commit()
    return "Alertas pausados. Use /retomar para reativar ou /palavras para mudar o alerta do bot."


def _cmd_retomar(con, chat):
    con.execute("BEGIN IMMEDIATE")
    try:
        linhas = con.execute(
            "SELECT id,account_id,enabled FROM alert_subscription WHERE channel='telegram' AND target=? ORDER BY id",
            (chat,),
        ).fetchall()
        if not linhas:
            con.rollback()
            return "Voce ainda nao tem assinatura. Use /palavras para criar uma."
        contas = {int(r[1]) for r in linhas if r[1] is not None}
        ativos = {
            aid: con.execute(
                "SELECT COUNT(*) FROM alert_subscription WHERE account_id=? AND enabled=1", (aid,)
            ).fetchone()[0]
            for aid in contas
        }
        retomados = 0
        for sub_id, account_id, enabled in linhas:
            if enabled:
                continue
            if account_id is not None and ativos[int(account_id)] >= 5:
                continue
            con.execute("UPDATE alert_subscription SET enabled=1 WHERE id=?", (sub_id,))
            retomados += 1
            if account_id is not None:
                ativos[int(account_id)] += 1
        con.commit()
        return (
            f"{retomados} alerta(s) reativado(s)."
            if retomados
            else "Nenhum alerta reativado; confira o limite no site."
        )
    except Exception:
        con.rollback()
        raise


def _cmd_preco(con, args):
    termo = args.strip()
    if not termo:
        return "Use assim: /preco limpeza"
    if len(termo) > 100:
        return "Termo muito longo. Use ate 100 caracteres."
    patt = "%" + termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sel = "SELECT fonte, processo_numero, objeto, fornecedor, contrato_numero, valor FROM v_preco_contrato WHERE objeto LIKE ? ESCAPE '\\' ORDER BY valor DESC LIMIT ?"
    lins = con.execute(sel, (patt, LIMITE_PRECO)).fetchall()
    if not lins:
        return "Nada encontrado para " + chr(39) + termo + chr(39) + " no historico de precos."
    saidas = ["Top " + str(len(lins)) + " para " + chr(39) + termo + chr(39) + ":"]
    n = 0
    addon = ", contrato "
    spa = " ("
    fim = ")"
    for li in lins:
        n = n + 1
        forn = li[3] or "fornecedor nao informado"
        ctr = (addon + str(li[4])) if li[4] else ""
        fnt = (spa + str(li[0]) + fim) if li[0] else ""
        saidas.append(str(n) + ". " + _reais(li[5]) + " - " + str(forn) + ctr + fnt)
    return chr(10).join(saidas)


def _responder(con, chat, texto):
    partes = texto.split(None, 1)
    cmd = partes[0].lower().split("@")[0]
    args = partes[1].strip() if len(partes) > 1 else ""
    if cmd == "/start":
        return TEXTO_START
    if cmd == "/palavras":
        return _cmd_palavras(con, chat, args)
    if cmd == "/minhas":
        return _cmd_minhas(con, chat)
    if cmd == "/parar":
        return _cmd_parar(con, chat)
    if cmd == "/retomar":
        return _cmd_retomar(con, chat)
    if cmd == "/preco":
        return _cmd_preco(con, args)
    if cmd == "/vincular":
        return (
            "Chat vinculado a sua conta."
            if vincular(con, args, chat)
            else "Codigo invalido ou expirado. Gere outro em /conta."
        )
    return TEXTO_AJUDA


def tratar_update(con, client, update):
    try:
        uid = int(update.get("update_id"))
    except (TypeError, ValueError, AttributeError):
        return None
    msg = update.get("message") or {}
    if not isinstance(msg, dict):
        return uid
    ch = msg.get("chat") or {}
    cid = ch.get("id") if isinstance(ch, dict) else None
    txt = msg.get("text")
    if cid is None or not isinstance(txt, str) or not txt.strip():
        return uid
    texto = _responder(con, str(cid), txt.strip())
    if texto:
        try:
            client.send_message(cid, texto)
        except Exception:
            pass
    return uid


def processar_lote(con, client, offset=None):
    updates = client.get_updates(offset=offset)
    if not updates:
        return offset
    prox = offset
    for up in updates:
        if not isinstance(up, dict):
            continue
        try:
            uid = int(up.get("update_id"))
        except (TypeError, ValueError):
            continue
        try:
            tratar_update(con, client, up)
        except Exception as exc:
            print("bot: falha no update " + str(uid) + ": " + str(exc), file=sys.stderr)
        if prox is None or uid + 1 > prox:
            prox = uid + 1
    return prox


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bot Telegram self-service (long polling).")
    ap.add_argument("--db", default=os.environ.get("LICITAMAIS_DB", ""))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--offset-file", default=os.environ.get("LICITAMAIS_TELEGRAM_OFFSET", ""))
    ap.add_argument("--base-url", default=BASE_URL_PADRAO)
    args = ap.parse_args(argv)
    if not args.db:
        ap.error("informe --db ou LICITAMAIS_DB")
    tok = os.environ.get("LICITAMAIS_TELEGRAM_TOKEN", "")
    if not tok:
        print("defina LICITAMAIS_TELEGRAM_TOKEN", file=sys.stderr)
        return 2
    off_file = args.offset_file or os.path.join(os.path.dirname(os.path.abspath(args.db)), "telegram_offset")
    con = sqlite3.connect(args.db)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)
        cli = BotClient(tok, args.base_url)
        if args.once:
            prox = processar_lote(con, cli, carregar_offset(off_file))
            if prox is not None:
                salvar_offset(off_file, prox)
            return 0
        off = carregar_offset(off_file)
        while True:
            try:
                prox = processar_lote(con, cli, off)
            except Exception as exc:
                print("bot: falha no lote: " + str(exc), file=sys.stderr)
                time.sleep(5)
                continue
            if prox != off:
                off = prox
                if off is not None:
                    salvar_offset(off_file, off)
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
