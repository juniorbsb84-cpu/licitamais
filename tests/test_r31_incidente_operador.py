"""R31: incidente de fonte precisa avisar o operador na hora e sem texto repetido.

Defeitos medidos no banco real (tabela incident):
1. Primeiro aviso nunca sai: open_incident gravava last_notified_at = instante
   de abertura com notify_count 0, e scheduler.notificar_operador via
   last_notified_at preenchido caia no ramo de renotificacao (espera 6h).
2. incident.message com a mesma frase de falha repetida 6x e terminando com
   "canario canario canario" / "freshness freshness freshness": _sonda_texto
   concatenava detail/details/message/mensagem/reason/motivo (todos aliases do
   mesmo texto no ProbeResult) mais name/nome/kind/tipo (todos "canario").

Sem rede: sender dublado. SQLite real.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from licitamais.probes import ProbeResult, maybe_open_incident, open_incident
from licitamais.scheduler import notificar_operador
from licitamais.schema import init_schema


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled) " + "VALUES (?, ?, ?, ?, 1)",
        ("fonte_r31", "api_json", "https://fonte.test", "teste-1"),
    )
    con.commit()
    return con


def fonte_id(con):
    linha = con.execute("SELECT id FROM source WHERE code = ?", ("fonte_r31",)).fetchone()
    return int(linha[0])


def linha_incidente(con, incidente_id):
    return con.execute(
        "SELECT notify_count, last_notified_at, message FROM incident WHERE id = ?",
        (incidente_id,),
    ).fetchone()


class SenderOk:
    def __init__(self):
        self.textos = []

    def send(self, texto):
        self.textos.append(texto)
        return True


def test_incidente_recem_aberto_envia_na_hora_e_nao_reenvia():
    con = nova_conexao()
    try:
        fonte = fonte_id(con)
        incidente_id = open_incident(con, fonte, "failed", "alta", "run failed simulado")
        con.commit()
        linha = linha_incidente(con, incidente_id)
        assert int(linha["notify_count"]) == 0
        assert linha["last_notified_at"] is None
        sender = SenderOk()
        agora = datetime(2026, 9, 24, 10, tzinfo=UTC)
        assert notificar_operador(con, sender, agora) == 1
        depois = linha_incidente(con, incidente_id)
        assert int(depois["notify_count"]) == 1
        assert depois["last_notified_at"]
        assert len(sender.textos) == 1
        assert notificar_operador(con, sender, agora) == 0
        assert int(linha_incidente(con, incidente_id)["notify_count"]) == 1
        assert len(sender.textos) == 1
    finally:
        con.close()


def test_incidente_legado_nao_notificado_envia_mesmo_com_data_preenchida():
    con = nova_conexao()
    try:
        fonte = fonte_id(con)
        cur = con.execute(
            "INSERT INTO incident (source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message) VALUES (?, XQXfailedXQX, XQXaltaXQX, ?, NULL, ?, 0, XQXfalha herdadaXQX)".replace(
                "XQX", chr(39)
            ),
            (fonte, "2026-09-24T09:00:00Z", "2026-09-24T09:00:00Z"),
        )
        con.commit()
        sender = SenderOk()
        assert notificar_operador(con, sender, datetime(2026, 9, 24, 10, tzinfo=UTC)) == 1
        assert int(linha_incidente(con, int(cur.lastrowid))["notify_count"]) == 1
        assert len(sender.textos) == 1
    finally:
        con.close()


def test_detalhes_repetidos_aparecem_uma_vez_sem_rabo_solto():
    con = nova_conexao()
    try:
        fonte = fonte_id(con)
        det_can = "canario de contrato: campo ausente numeroOrigem,numero,valorTotal (required: numeroOrigem,numero,valorTotal)"
        det_qua = "taxa de quarentena 37.8 pct acima de 2 pct; quarantine indica layout mudado"
        sondas = [
            ProbeResult(name="canario", passed=False, detail=det_can),
            ProbeResult(name="canario", passed=False, detail=det_can),
            ProbeResult(name="quarentena", passed=False, detail=det_qua),
            ProbeResult(name="quarentena", passed=False, detail=det_qua),
        ]
        incidente_id = maybe_open_incident(con, fonte, "failed", sondas)
        con.commit()
        assert True
        mensagem = str(linha_incidente(con, incidente_id)["message"] or "")
        assert mensagem.count(det_can) == 1, mensagem
        assert mensagem.count(det_qua) == 1, mensagem
        assert len(mensagem) <= 600, len(mensagem)
        rabo = mensagem.split()[-3:]
        assert not (len(rabo) == 3 and rabo[0] == rabo[1] == rabo[2]), mensagem[-80:]
    finally:
        con.close()


def test_mensagem_longa_cortada_em_600_caracteres():
    con = nova_conexao()
    try:
        fonte = fonte_id(con)
        sondas = [ProbeResult(name="s", passed=False, detail=f"falha {i} " + "y" * 200) for i in range(5)]
        incidente_id = maybe_open_incident(con, fonte, "failed", sondas)
        con.commit()
        mensagem = str(linha_incidente(con, incidente_id)["message"] or "")
        assert len(mensagem) <= 600, len(mensagem)
    finally:
        con.close()
