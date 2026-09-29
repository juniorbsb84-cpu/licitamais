def test_detalhe_completo(cliente):
    r = cliente.get("/api/v2/licitacoes/3")
    assert r.status_code == 200
    d = r.json()
    assert d["objeto"] == "Limpeza hospitalar" and d["situacao"] == "encerrada" and d["orgao"] == "SENAC DF"
    assert d["contratos"] == [
        {
            "numero": "450",
            "fornecedor": "RIVERA MOVEIS LTDA",
            "cnpj": "12345678000199",
            "valor": 50000.0,
            "assinatura": "2020-02-02",
            "vigencia_inicio": "2020-02-02",
            "vigencia_fim": None,
        }
    ]
    assert [f["rotulo"] for f in d["fases"]] == ["Em processo", "Finalizada"]


def test_detalhe_sem_dados_listas_vazias(cliente):
    d = cliente.get("/api/v2/licitacoes/1").json()
    assert d["contratos"] == d["vencedores"] == d["itens"] == d["arquivos"] == []


def test_detalhe_inexistente(cliente):
    assert cliente.get("/api/v2/licitacoes/999").status_code == 404
    assert cliente.get("/api/v2/licitacoes/abc").status_code == 422


def test_arquivo_sem_https_nao_vira_link(cliente, db_path):
    import sqlite3

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO attachment (source_id, source_ref, process_id, kind, url_download, filename,"
            " first_seen_run_id, last_seen_run_id, last_changed_run_id) VALUES"
            " (1, 'a1', 1, 'edital', 'javascript:alert(1)', 'edital.pdf', 1, 1, 1),"
            " (1, 'a2', 1, 'edital', 'https://x.test/e.pdf', 'anexo.pdf', 1, 1, 1)"
        )
    d = cliente.get("/api/v2/licitacoes/1").json()
    assert d["arquivos"] == [{"nome": "edital.pdf", "url": None}, {"nome": "anexo.pdf", "url": "https://x.test/e.pdf"}]
