"""T23: organizacao sem CNPJ nao duplica a cada run; nome casa sem caixa/acento/espaco."""

import sqlite3

from licitamais.loader import upsert_organization
from licitamais.schema import init_schema
from licitamais.types import OrgRecord


def _con():
    con = sqlite3.connect(":memory:")
    init_schema(con)
    return con


def test_sem_cnpj_em_dois_runs_gera_uma_linha():
    con = _con()
    org = OrgRecord("o1", {"name_raw": "Serviço Social da Indústria"})
    a = upsert_organization(con, 1, org)
    b = upsert_organization(con, 2, org)
    assert a == b
    assert con.execute("SELECT COUNT(*) FROM organization").fetchone()[0] == 1


def test_nome_casa_sem_caixa_acento_ou_espaco():
    con = _con()
    a = upsert_organization(con, 1, OrgRecord("o1", {"name_raw": "Serviço  Social da Indústria"}))
    b = upsert_organization(con, 2, OrgRecord("o2", {"name_raw": "SERVICO SOCIAL DA INDUSTRIA"}))
    assert a == b


def test_com_cnpj_continua_pelo_cnpj():
    con = _con()
    a = upsert_organization(con, 1, OrgRecord("o1", {"name_raw": "X", "cnpj": "33.641.358/0001-52"}))
    b = upsert_organization(con, 2, OrgRecord("o2", {"name_raw": "Outro nome", "cnpj": "33641358000152"}))
    assert a == b
