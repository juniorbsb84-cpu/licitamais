"""Gera um token de login valido para o e2e (mesmo e-mail do fluxo)."""

from __future__ import annotations

import sqlite3
import sys

sys.path.insert(0, "src")

from licitamais.contas import auth

con = sqlite3.connect("tmp_e2e.db")
con.row_factory = sqlite3.Row
token = auth.pedir_link(con, "cliente@exemplo.com", "127.0.0.1")
con.close()
sys.stdout.write(token or "")
