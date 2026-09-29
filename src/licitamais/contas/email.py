"""Envio do link de acesso via SMTP STARTTLS."""

import logging
import os
import smtplib
from email.message import EmailMessage


def enviar_link(destino: str, token: str) -> None:
    link = os.environ.get("LICITAMAIS_BASE_URL", "http://localhost:8090").rstrip("/") + "/entrar?t=" + token
    host = os.environ.get("LICITAMAIS_SMTP_HOST")
    if not host:
        # r44/A4: o link so aparece no log em modo dev explicito; fora disso o token nunca vai para log
        if os.environ.get("LICITAMAIS_DEV") == "1":
            logging.warning("Link de login (desenvolvimento): %s", link)
        else:
            logging.error("SMTP nao configurado: link de login NAO enviado para %s", destino)
        return
    msg = EmailMessage()
    msg["From"] = os.environ["LICITAMAIS_SMTP_FROM"]
    msg["To"] = destino
    msg["Subject"] = "Acesse o LicitamAIs"
    msg.set_content("Use este link para entrar (validade de 15 minutos): " + link)
    with smtplib.SMTP(host, int(os.environ.get("LICITAMAIS_SMTP_PORT", "587")), timeout=15) as smtp:
        smtp.starttls()
        smtp.login(os.environ["LICITAMAIS_SMTP_USER"], os.environ["LICITAMAIS_SMTP_PASS"])
        smtp.send_message(msg)
