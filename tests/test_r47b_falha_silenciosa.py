"""r47b: falha no envio ao Telegram vai ao log, sem vazar o token do bot."""

import logging

import requests

from licitamais.alerts import telegram
from licitamais.alerts.telegram import TelegramSender


def test_falha_de_rede_no_telegram_loga_sem_token(monkeypatch, caplog):
    def falha(url, **_):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(telegram.requests, "post", falha)
    with caplog.at_level(logging.WARNING, logger="licitamais.alerts.telegram"):
        assert TelegramSender("123:SEGREDO", "42").send("oi") is False
    assert "chat_id 42" in caplog.text
    assert "SEGREDO" not in caplog.text
