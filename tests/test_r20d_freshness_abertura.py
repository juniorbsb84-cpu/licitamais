"""R20d: fonte sem data de publicacao (BRB) usa a abertura como sinal de atividade."""

import sqlite3
from datetime import UTC, datetime, timedelta

from licitamais.probes import check_freshness
from licitamais.schema import init_schema


def _con(abertura):
    con = sqlite3.connect(":memory:")
    init_schema(con)
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual)"
        " VALUES (1, 'brb', 'api_json', 'https://x.test', 'v1')"
    )
    con.execute("INSERT INTO process (source_id, source_native_id, opening_at_source) VALUES (1, 'p1', ?)", (abertura,))
    return con


def test_sem_publicacao_mas_abertura_recente_passa():
    recente = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")
    assert check_freshness(_con(recente), 1, 30).passed


def test_sem_publicacao_e_abertura_antiga_falha():
    antiga = (datetime.now(UTC) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    assert not check_freshness(_con(antiga), 1, 30).passed


def test_canario_aceita_chave_com_caixa_diferente():
    from licitamais.probes import _valor_campo

    assert _valor_campo({"Data": 1, "Id": "x"}, "id") == (True, "x")
