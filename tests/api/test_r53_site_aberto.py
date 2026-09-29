"""r53: site aberto para leitura, limite de visitantes e fontes ocultas (padrão: tudo desligado)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from licitamais.api.limite_consultas import LimiteVisitantes

LEITURAS = [
    "/api/v2/resumo",
    "/api/v2/licitacoes",
    "/api/v2/licitacoes/1",
    "/api/v2/evidencias?ids=1",
    "/api/v2/precos?q=limpeza",
]


def test_leitura_publica_so_com_site_aberto(anonimo, monkeypatch):
    for url in LEITURAS:
        assert anonimo.get(url).status_code == 401, url
    monkeypatch.setenv("LICITAMAIS_ABERTO", "1")
    for url in LEITURAS:
        assert anonimo.get(url).status_code == 200, url


def test_site_aberto_mantem_conta_e_operador_fechados(anonimo, monkeypatch):
    monkeypatch.setenv("LICITAMAIS_ABERTO", "1")
    assert anonimo.get("/api/v2/me").status_code == 401
    assert anonimo.get("/api/v2/alertas").status_code == 401
    assert anonimo.get("/api/v2/operador/saude").status_code == 401
    assert anonimo.post("/api/v2/alertas", json={"palavras": ["x"]}).status_code in (401, 403)


def test_visitante_limitado_por_ip():
    limite = LimiteVisitantes(por_ip=30, total=600)
    for _ in range(30):
        limite.verificar("1.1.1.1")
    with pytest.raises(HTTPException) as erro:
        limite.verificar("1.1.1.1")
    assert erro.value.status_code == 429
    limite.verificar("2.2.2.2")


def test_teto_global_de_visitantes():
    limite = LimiteVisitantes(por_ip=30, total=5)
    for i in range(5):
        limite.verificar(f"10.0.0.{i}")
    with pytest.raises(HTTPException):
        limite.verificar("10.0.0.99")


def test_visitante_estoura_na_api_e_conta_nao(anonimo, cliente, monkeypatch):
    monkeypatch.setenv("LICITAMAIS_ABERTO", "1")
    anonimo.app.state.limite_visitantes = LimiteVisitantes(por_ip=2, total=600)
    assert anonimo.get("/api/v2/licitacoes").status_code == 200
    assert anonimo.get("/api/v2/licitacoes").status_code == 200
    assert anonimo.get("/api/v2/licitacoes").status_code == 429
    cliente.app.state.limite_visitantes = LimiteVisitantes(por_ip=0, total=0)
    assert cliente.get("/api/v2/licitacoes").status_code == 200


def test_ip_da_cloudflare_so_quando_configurado(anonimo, monkeypatch):
    monkeypatch.setenv("LICITAMAIS_ABERTO", "1")
    anonimo.app.state.limite_visitantes = LimiteVisitantes(por_ip=1, total=600)
    assert anonimo.get("/api/v2/licitacoes", headers={"CF-Connecting-IP": "9.9.9.1"}).status_code == 200
    # sem confiar na Cloudflare, o cabecalho e ignorado: mesmo IP do cliente de teste
    assert anonimo.get("/api/v2/licitacoes", headers={"CF-Connecting-IP": "9.9.9.2"}).status_code == 429
    monkeypatch.setenv("LICITAMAIS_CONFIA_CLOUDFLARE", "1")
    assert anonimo.get("/api/v2/licitacoes", headers={"CF-Connecting-IP": "9.9.9.3"}).status_code == 200


def test_fonte_oculta_some_de_lista_resumo_detalhe_e_precos(cliente, monkeypatch):
    antes = cliente.get("/api/v2/licitacoes").json()
    assert antes["total"] == 4 and "senac" in antes["facetas"]["fonte"]
    monkeypatch.setenv("LICITAMAIS_FONTES_OCULTAS", "senac")
    depois = cliente.get("/api/v2/licitacoes").json()
    assert depois["total"] == 2 and "senac" not in depois["facetas"]["fonte"]
    assert all(i["fonte"] != "senac" for i in depois["itens"])
    assert cliente.get("/api/v2/resumo").json()["total"] == 2
    assert cliente.get("/api/v2/licitacoes/3").status_code == 404
    assert cliente.get("/api/v2/precos", params={"q": "limpeza"}).json()["n"] == 0
    assert [e["id"] for e in cliente.get("/api/v2/evidencias", params={"ids": [1, 3]}).json()] == [1]
