import pytest
from fastapi.testclient import TestClient

from licitamais.api import create_app


def test_spa_fallback_e_estaticos(tmp_path):
    raiz = tmp_path / "dist"
    (raiz / "assets").mkdir(parents=True)
    (raiz / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (raiz / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = create_app(str(tmp_path / "banco.db"), str(raiz))
    c = TestClient(app)
    assert c.get("/licitacao/3").status_code == 200
    assert "spa" in c.get("/licitacao/3").text
    assert c.get("/assets/app.js").status_code == 200
    assert "pyproject" not in c.get("/../pyproject.toml").text
    assert c.get("/%2e%2e/pyproject.toml").status_code in (200, 404)
    assert "pyproject" not in c.get("/%2e%2e/pyproject.toml").text
    r = c.get("/api/v2/nao-existe")
    assert r.status_code == 404
    assert "erro" in r.json()


@pytest.mark.parametrize(
    "url",
    [
        "/assets/%2e%2e/%2e%2e/segredo.txt",
        "/assets/..%2f..%2fsegredo.txt",
        "/assets/%2e%2e%5c%2e%2e%5csegredo.txt",
        "/assets/../../segredo.txt",
        "/%2e%2e/segredo.txt",
        "/assets/%2e%2e/index.html",
    ],
)
def test_assets_nao_saem_da_pasta(tmp_path, url):
    """/assets/<..> servia arquivo fora do dist, inclusive o banco."""
    (tmp_path / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text("INDEX")
    (tmp_path / "segredo.txt").write_text("SEGREDO")
    cliente = TestClient(create_app(str(tmp_path / "x.db"), str(tmp_path / "dist")))
    r = cliente.get(url)
    assert "SEGREDO" not in r.text
    assert r.status_code in (200, 404) and (r.status_code == 404 or r.text == "INDEX")
