"""r49: 'hoje' e o dia em Brasilia (UTC-3), nao em UTC. Entre 21h e 24h BRT o UTC ja virou o dia
e o que fecha hoje sumia das abertas (visto em 24/09/2026 as 22h)."""

from datetime import UTC, datetime

from licitamais.situacao import hoje_brasil, situacao
from tests.test_r48_situacao_sql import _processo, con  # noqa: F401  (fixture)


def test_hoje_brasil_vira_o_dia_as_3h_utc():
    assert hoje_brasil(datetime(2026, 9, 25, 1, 0, tzinfo=UTC)) == "2026-09-24"
    assert hoje_brasil(datetime(2026, 9, 25, 3, 0, tzinfo=UTC)) == "2026-09-25"


def test_view_usa_o_dia_de_brasilia(con):  # noqa: F811
    hoje = hoje_brasil()
    _processo(con, 1, "Em processo", hoje)
    assert con.execute("SELECT situacao FROM v_licitacao WHERE id = 1").fetchone()[0] == "aberta"
    assert situacao("Em processo", hoje)["grupo"] == "aberta"
    sql_hoje = con.execute("SELECT date('now', '-3 hours')").fetchone()[0]
    assert sql_hoje == hoje
