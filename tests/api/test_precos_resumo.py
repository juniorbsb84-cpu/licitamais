from licitamais.precos import estatistica


def test_estatistica_tukey():
    d = estatistica(
        [("2025-01-01", 10), ("2025-02-01", 20), ("2026-01-01", 30), ("2026-02-01", 70), ("2026-03-01", 100)]
    )
    assert (d["n"], d["min"], d["q1"], d["mediana"], d["q3"], d["max"]) == (5, 10, 15, 30, 85, 100)
    assert d["serie_ano"] == [{"ano": "2025", "n": 2, "mediana": 15}, {"ano": "2026", "n": 3, "mediana": 70}]


def test_estatistica_vazia():
    assert estatistica([])["n"] == 0 and estatistica([])["mediana"] is None


def test_precos_por_fts(cliente):
    d = cliente.get("/api/v2/precos", params={"q": "limpeza"}).json()
    assert d["n"] == 1 and d["mediana"] == 50000.0
    assert d["pontos"][0]["processo_id"] == 3
    assert d["fornecedores"] == [{"fornecedor": "RIVERA MOVEIS LTDA", "cnpj": "12345678000199", "vitorias": 1}]


def test_precos_termo_sem_resultado(cliente):
    assert cliente.get("/api/v2/precos", params={"q": "zzz"}).json()["n"] == 0


def test_resumo(cliente):
    d = cliente.get("/api/v2/resumo").json()
    assert d["abertas"] == 2 and d["total"] == 4 and d["fontes"] == 2
    assert d["fechando_7_dias"] == 0 and d["ultima_coleta"] == "2026-09-24T10:06:00Z"
