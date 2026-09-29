import argparse
import os
import pathlib
import sqlite3
import statistics
import sys
from collections.abc import Sequence

import licitamais.scheduler as scheduler
from licitamais.fontes import registrar_fontes
from licitamais.loader import backfill_situacao_orgao
from licitamais.schema import init_schema

_SQL_PRECO = (
    "SELECT data, fonte, fornecedor, cnpj, valor FROM ("
    " SELECT COALESCE(assinado_em, vigencia_inicio) AS data,"
    "        fonte, fornecedor, fornecedor_cnpj AS cnpj, valor"
    " FROM v_preco_contrato WHERE objeto LIKE ?"
    " UNION ALL"
    " SELECT COALESCE(r.decided_at_source, a.first_seen_at) AS data,"
    "        i.fonte, i.fornecedor, o.cnpj AS cnpj, i.valor_total AS valor"
    " FROM v_preco_item i"
    " JOIN award a ON a.id = i.award_id"
    " LEFT JOIN result r ON r.id = a.result_id"
    " LEFT JOIN organization o ON o.id = a.supplier_org_id"
    " WHERE i.item LIKE ?"
    ") ORDER BY data DESC"
)


def _reais(valor) -> str:
    if valor is None:
        return "-"
    texto = f"{float(valor):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _preco_texto(con: sqlite3.Connection, termo: str) -> str:
    padrao = f"%{termo}%"
    linhas = con.execute(_SQL_PRECO, (padrao, padrao)).fetchall()
    total = len(linhas)
    cabecalho = f"Historico de preco para '{termo}': {total} ocorrencia(s)"
    if total == 0:
        return cabecalho
    valores = [float(linha["valor"]) for linha in linhas if linha["valor"] is not None]
    cabecalho += f" (mostrando {min(total, 50)})"
    saida = [cabecalho, "data | fonte | fornecedor | CNPJ | valor"]
    for linha in linhas[:50]:
        data = linha["data"]
        data = str(data)[:10] if data else "-"
        saida.append(
            f"{data} | {linha['fonte']} | {linha['fornecedor'] or '-'}"
            f" | {linha['cnpj'] or '-'} | {_reais(linha['valor'])}"
        )
    mediana = statistics.median(valores)
    saida.append(f"minimo {_reais(min(valores))} | mediana {_reais(mediana)} | maximo {_reais(max(valores))}")
    return chr(10).join(saida)


def _erro_permanente(erro) -> bool:
    # So o 404 de descoberta alcanca partial com defeito permanente (REVISAO C3);
    # teto, rate limit e timeout sao operacionais e somem no proximo run.
    if not erro:
        return False
    return "HTTP 404" in str(erro)


def _codigo_saida(resultados: Sequence) -> int:
    for registro in resultados:
        if registro.status in ("ok", "ok_zero"):
            continue
        if registro.status == "partial" and not _erro_permanente(getattr(registro, "error", None)):
            continue
        return 1
    return 0


def _preencher_situacao(con: sqlite3.Connection) -> None:
    """Processos gravados antes da 006 ganham grupo de situacao e orgao. Idempotente: 0,1 s quando ja feito."""
    ultimo = con.execute("SELECT MAX(id) FROM source_run").fetchone()[0]
    if ultimo is not None:
        backfill_situacao_orgao(con, int(ultimo))
        con.commit()


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str)
    parser.add_argument("--trigger", type=str, default="manual")
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--relatorio", action="store_true", help="relatorio do ultimo run por fonte")
    parser.add_argument("--assinar", metavar="CHAT_ID", help="cria assinatura Telegram para o chat")
    parser.add_argument("--palavras", default="", help="palavras-chave da assinatura, separadas por virgula")
    parser.add_argument("--preco", metavar="TERMO", help="historico de preco: objeto/descricao LIKE o termo")
    parser.add_argument("--db", default=os.environ.get("LICITAMAIS_DB", ""))
    parser.add_argument("--backup-dir", default=os.environ.get("LICITAMAIS_BACKUP_DIR", ""))
    parser.add_argument("--external-dest", default=os.environ.get("LICITAMAIS_BACKUP_EXTERNO", ""))
    args = parser.parse_args(argv)
    if not args.db:
        parser.error("informe --db ou LICITAMAIS_DB apontando para banco existente")
    if args.init:
        con = sqlite3.connect(args.db)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            init_schema(con)
            registrar_fontes(con)
            con.commit()
        finally:
            con.close()
        return 0
    if not pathlib.Path(args.db).is_file():
        parser.error("informe --db ou LICITAMAIS_DB apontando para banco existente")
    if args.preco is not None:
        with sqlite3.connect(args.db) as con:
            con.row_factory = sqlite3.Row
            init_schema(con)  # migracoes novas (views de preco) em banco existente
            texto = _preco_texto(con, args.preco)
        print(texto)
        return 0
    # Telegram por ambiente: token do bot e chat do operador
    token = os.environ.get("LICITAMAIS_TELEGRAM_TOKEN", "")
    chat_operador = os.environ.get("LICITAMAIS_TELEGRAM_OPERADOR", "")
    sender_operador = None
    sender_usuario = None
    if token:
        from licitamais.alerts.telegram import TelegramSender

        if chat_operador:
            sender_operador = TelegramSender(token, chat_operador)
        sender_usuario = lambda chat: TelegramSender(token, chat)  # noqa: E731
    if args.relatorio or args.assinar:
        with sqlite3.connect(args.db) as con:
            init_schema(con)
            if args.assinar:
                import json
                from datetime import UTC, datetime

                palavras = [p.strip() for p in args.palavras.split(",") if p.strip()]
                filtro = json.dumps({"keywords": palavras, "modalities": [], "entities": []}, ensure_ascii=False)
                con.execute(
                    "INSERT INTO alert_subscription (kind, channel, target, filter_expr, enabled, created_at, attrs)"
                    " VALUES ('filtro', 'telegram', ?, ?, 1, ?, ?)",
                    (args.assinar, filtro, datetime.now(UTC).isoformat(), filtro),
                )
                con.commit()
                return 0
            texto = scheduler.relatorio_diario(con)
        print(texto)
        if sender_operador is not None and not sender_operador.send(texto):
            print("falha ao enviar relatorio ao operador", file=sys.stderr)
            return 1
        return 0
    cfg = scheduler.SchedulerConfig(
        trigger=args.trigger,
        capture_only=args.capture_only,
        source_code=args.source,
        db_path=args.db,
        backup_dir=args.backup_dir,
        external_dest=args.external_dest,
        sender=sender_operador,
        sender_usuario=sender_usuario,
    )
    with sqlite3.connect(args.db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)  # migracoes novas (ex.: views de preco) em banco existente
        _preencher_situacao(con)
        resultados = scheduler.run_scheduler(con, cfg)
    if not resultados:
        print("nenhuma fonte executada: verifique fontes habilitadas e --source", file=sys.stderr)
        return 1
    return _codigo_saida(resultados)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
