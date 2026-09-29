def test_buscas_pesadas_sao_limitadas_por_conta(cliente):
    cliente.app.state.limite_consultas.max_consultas = 2
    assert cliente.get("/api/v2/licitacoes").status_code == 200
    assert cliente.get("/api/v2/precos", params={"q": "pneus"}).status_code == 200
    resposta = cliente.get("/api/v2/licitacoes")
    assert resposta.status_code == 429
    assert "Muitas consultas" in resposta.json()["erro"]
