from licitamais.api import create_app


def test_openapi_tem_as_rotas(db_path):
    rotas = set(create_app(db_path).openapi()["paths"])
    assert {
        "/api/v2/me",
        "/api/v2/licitacoes",
        "/api/v2/licitacoes/{pid}",
        "/api/v2/precos",
        "/api/v2/resumo",
        "/api/v2/alertas",
        "/api/v2/alertas/{aid}",
        "/api/v2/conta/telegram",
        "/api/v2/operador/saude",
        "/api/v2/auth/link",
        "/api/v2/auth/entrar",
        "/api/v2/auth/sair",
    } <= rotas
