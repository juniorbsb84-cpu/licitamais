"""R47: falha na entrega de alertas e no fechamento do ciclo nao pode sumir sem log.
Origem: auditoria Jev (docs/auditoria_jev_acertos.md), casos confirmados no scheduler."""

import logging
from datetime import timedelta

import licitamais.scheduler as modulo_scheduler
from licitamais.scheduler import entregar_alertas
from tests.test_r15_entrega_alertas import RemetenteOk, agora_brt, criar_processo, enfileirar, ler_status, nova_conexao


def test_erro_ao_entregar_alertas_de_um_usuario_fica_no_log(monkeypatch, caplog):
    con = nova_conexao()
    try:
        proc = criar_processo(con, (agora_brt(12) + timedelta(days=30)).isoformat(), "edital-log-1")
        alerta = enfileirar(con, 7, proc)

        def quebra(*a, **k):
            raise RuntimeError("banco travado")

        monkeypatch.setattr("licitamais.alerts.digest.deliver_outbox", quebra)
        with caplog.at_level(logging.ERROR):
            assert entregar_alertas(con, RemetenteOk(), agora_brt(12)) == 0
        assert ler_status(con, alerta)[0] == "pending"
        assert "banco travado" in caplog.text
    finally:
        con.close()


def test_erro_no_aviso_ao_operador_fica_no_log(monkeypatch, caplog):
    con = nova_conexao()
    try:

        def quebra(*a, **k):
            raise RuntimeError("operador fora")

        monkeypatch.setattr(modulo_scheduler, "notificar_operador", quebra)
        cfg = modulo_scheduler.SchedulerConfig(trigger="cron", adapters={})
        cfg.sender = RemetenteOk()
        with caplog.at_level(logging.ERROR):
            modulo_scheduler.run_scheduler(con, cfg)
        assert "operador fora" in caplog.text
    finally:
        con.close()
