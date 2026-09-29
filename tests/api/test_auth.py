from tests.api.conftest import SESSAO

from licitamais.contas import auth


def test_me_sem_sessao_401_json(anonimo):
    r = anonimo.get("/api/v2/me")
    assert r.status_code == 401
    assert r.json() == {"erro": "autenticação necessária"}


def test_me_com_sessao(cliente, monkeypatch):
    monkeypatch.delenv("LICITAMAIS_OPERADORES", raising=False)
    r = cliente.get("/api/v2/me")
    assert r.status_code == 200
    assert r.json() == {"id": 1, "email": "cliente@exemplo.com", "operador": False, "csrf": auth.csrf(SESSAO)}


def test_me_operador(cliente, monkeypatch):
    monkeypatch.setenv("LICITAMAIS_OPERADORES", "Cliente@Exemplo.com")
    assert cliente.get("/api/v2/me").json()["operador"] is True


def test_pedir_link_sempre_202_sem_revelar_conta(anonimo, monkeypatch):
    enviados = []
    monkeypatch.setattr("licitamais.api.rotas.auth.enviar_link", lambda e, t: enviados.append((e, t)))
    r = anonimo.post("/api/v2/auth/link", json={"email": "novo@exemplo.com"})
    assert r.status_code == 202 and enviados and enviados[0][0] == "novo@exemplo.com"
    assert anonimo.post("/api/v2/auth/link", json={"email": "invalido"}).status_code == 202


def test_entrar_cria_cookie_e_redireciona(anonimo, monkeypatch):
    enviados = []
    monkeypatch.setattr("licitamais.api.rotas.auth.enviar_link", lambda e, t: enviados.append(t))
    anonimo.post("/api/v2/auth/link", json={"email": "cliente@exemplo.com"})
    pagina = anonimo.get(f"/api/v2/auth/entrar?t={enviados[0]}", follow_redirects=False)
    assert pagina.status_code == 200 and "Confirmar acesso" in pagina.text
    assert "set-cookie" not in pagina.headers
    r = anonimo.post(f"/api/v2/auth/entrar?t={enviados[0]}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert "licitamais_session=" in cookie and "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    assert (
        anonimo.post(f"/api/v2/auth/entrar?t={enviados[0]}", follow_redirects=False).headers["location"]
        == "/entrar?erro=link"
    )


def test_sair_exige_csrf(cliente):
    del cliente.headers["X-CSRF"]
    assert cliente.post("/api/v2/auth/sair").status_code == 403
    cliente.headers["X-CSRF"] = auth.csrf("outra-sessao")
    assert cliente.post("/api/v2/auth/sair").status_code == 403


def test_sair_revoga_sessao(cliente):
    assert cliente.post("/api/v2/auth/sair").status_code == 204
    assert cliente.get("/api/v2/me").status_code == 401


def test_cabecalhos_de_seguranca(anonimo):
    r = anonimo.get("/api/v2/me")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_corpo_grande_413(anonimo):
    r = anonimo.post("/api/v2/auth/link", content=b"x" * 20000, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_rota_api_inexistente_404_json(cliente):
    r = cliente.get("/api/v2/nao-existe")
    assert r.status_code == 404 and r.json() == {"erro": "não encontrado"}
