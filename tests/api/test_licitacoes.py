import pytest

from licitamais.api.busca import consulta_fts


@pytest.mark.parametrize(
    "q,esperado",
    [
        ("manutenção frota", '"manutenção" "frota"*'),
        ("  ", None),
        ('pneu" OR 1=1 -- (', '"pneu" "or" "1" "1"*'),
        ("NEAR limpeza*", '"near" "limpeza"*'),
    ],
)
def test_consulta_fts_neutraliza_sintaxe(q, esperado):
    assert consulta_fts(q) == esperado


def test_sem_filtro_abertas_primeiro(cliente):
    r = cliente.get("/api/v2/licitacoes")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 4 and d["pagina"] == 1
    assert [i["situacao"] for i in d["itens"]][:2] == ["aberta", "aberta"]
    assert d["facetas"]["situacao"] == {"aberta": 2, "encerrada": 1, "cancelada": 1}
    assert d["facetas"]["fonte"] == {"brb": 2, "senac": 2}


def test_busca_sem_acento_e_prefixo(cliente):
    d = cliente.get("/api/v2/licitacoes", params={"q": "manutencao"}).json()
    assert {i["id"] for i in d["itens"]} == {1, 2}
    d = cliente.get("/api/v2/licitacoes", params={"q": "onib"}).json()
    assert [i["id"] for i in d["itens"]] == [1]


def test_filtro_situacao_e_fonte(cliente):
    d = cliente.get("/api/v2/licitacoes", params={"situacao": ["aberta"], "fonte": ["senac"]}).json()
    assert [i["id"] for i in d["itens"]] == [2]
    assert d["facetas"]["situacao"]["aberta"] == 1  # faceta de situacao ignora o proprio filtro? nao: respeita fonte
    assert d["facetas"]["fonte"] == {"brb": 1, "senac": 1}  # faceta de fonte respeita situacao, ignora fonte


def test_filtro_periodo_abertura(cliente):
    d = cliente.get("/api/v2/licitacoes", params={"abertura_de": "2099-09-15", "abertura_ate": "2099-12-31"}).json()
    assert [i["id"] for i in d["itens"]] == [1]


def test_pagina_alem_do_fim(cliente):
    d = cliente.get("/api/v2/licitacoes", params={"pagina": 99, "por_pagina": 2}).json()
    assert d["itens"] == [] and d["total"] == 4


def test_dias_para_abertura(cliente):
    item = cliente.get("/api/v2/licitacoes", params={"q": "onibus"}).json()["itens"][0]
    assert item["dias_para_abertura"] > 1000 and item["orgao"] is None and item["rotulo"] == "Edital Aberto"


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x" * 101},
        {"por_pagina": 51},
        {"pagina": 0},
        {"situacao": ["inventada"]},
        {"fonte": [f"f{i}" for i in range(11)]},
        {"abertura_de": "ontem"},
        {"ordem": "aleatoria"},
    ],
)
def test_parametros_invalidos_422(cliente, params):
    r = cliente.get("/api/v2/licitacoes", params=params)
    assert r.status_code == 422 and "erro" in r.json()


def test_exige_login(anonimo):
    assert anonimo.get("/api/v2/licitacoes").status_code == 401


def test_faceta_de_modalidade(cliente):
    """A interface nao tinha como filtrar modalidade; a API passa a contar."""
    d = cliente.get("/api/v2/licitacoes").json()
    assert d["facetas"]["modalidade"] == {"Pregão eletrônico": 3, "Concorrência": 1}
    d = cliente.get("/api/v2/licitacoes", params={"modalidade": "Concorrência"}).json()
    assert [i["id"] for i in d["itens"]] == [3]
    assert d["facetas"]["modalidade"]["Pregão eletrônico"] == 3  # a propria faceta ignora o proprio filtro
