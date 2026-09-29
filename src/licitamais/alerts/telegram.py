"""Envio de mensagens ao operador via Telegram Bot API."""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

BASE_URL_PADRAO = "https://api.telegram.org"


class TelegramSender:
    """Envia texto ao chat do operador via Bot API."""

    def __init__(self, token: str, chat_id: str, base_url: str = "https://api.telegram.org") -> None:
        self.token = token
        self.chat_id = chat_id
        self.base_url = (base_url or "https://api.telegram.org").rstrip("/")

    def send(self, text: str) -> bool:
        url = f"{self.base_url}/bot{self.token}/sendMessage"
        carga = {"chat_id": self.chat_id, "text": str(text), "parse_mode": "HTML", "disable_web_page_preview": False}
        try:
            resposta = requests.post(url, json=carga, timeout=15)
        except Exception as exc:
            # so o tipo do erro: a mensagem do requests traz a URL com o token do bot
            logger.warning("falha na requisicao telegram para chat_id %s: %s", self.chat_id, type(exc).__name__)
            return False
        status = getattr(resposta, "status_code", None)
        if isinstance(status, int) and not (200 <= status < 300):
            logger.warning(
                "telegram respondeu status %s para chat_id %s: %r",
                status,
                self.chat_id,
                getattr(resposta, "text", "")[:200],
            )
            return False
        try:
            corpo = resposta.json()
        except Exception:
            logger.warning("falha ao decodificar json da resposta telegram para chat_id %s", self.chat_id)
            return False
        if isinstance(corpo, dict) and "ok" in corpo:
            try:
                return bool(corpo["ok"])
            except Exception:
                logger.warning("falha ao avaliar corpo['ok'] telegram para chat_id %s: %r", self.chat_id, corpo)
                return False
        if isinstance(status, int):
            return 200 <= status < 300
        return False


def load_operator_config():
    """Le token e chat do operador das variaveis de ambiente."""
    return {
        "token": os.environ.get("TELEGRAM_OPERATOR_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_OPERATOR_CHAT_ID", ""),
        "base_url": BASE_URL_PADRAO,
    }
