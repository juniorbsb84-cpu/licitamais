from pathlib import Path


def test_marketing_index_html_exists():
    html_path = Path("marketing/index.html")
    assert html_path.exists(), "marketing/index.html deve existir"
    content = html_path.read_text(encoding="utf-8")
    assert len(content) > 500, "Arquivo deve ter conteúdo substancial"


def test_marketing_index_sections_and_content():
    content = Path("marketing/index.html").read_text(encoding="utf-8")

    # Valida presença das seções obrigatórias
    # r50 (25/09): a secao "problema" virou o caso real de disputa, com ancora propria
    assert 'id="disputa"' in content
    assert "como funciona" in content.lower()
    assert "telegram" in content.lower()
    assert "histórico de preço" in content.lower() or "historico de preco" in content.lower()
    assert "fontes" in content.lower()
    assert "planos" in content.lower()
    assert "mailto:" in content

    # Valida entidades do público alvo
    assert "Sistema S" in content
    assert "SENAI" in content
    assert "SESI" in content
    assert "SENAC" in content
    assert "BRB" in content
    # r50: IGES fora da oferta ate parecer juridico (termos da fonte: uso pessoal e nao comercial)
    assert "IGES" not in content

    # Valida proposta de valor central (fora do PNCP)
    assert "PNCP" in content


def test_marketing_real_numbers_from_db():
    content = Path("marketing/index.html").read_text(encoding="utf-8")

    # Numeros do banco real em 25/09/2026 (r50): processos, contratos com valor, soma, fornecedores
    assert "118.558" in content
    assert "14.585" in content
    assert "R$ 14,6 bi" in content
    assert "10.505" in content
    # disputa real 000035/2026: vencedor R$ 208.000 e maior proposta R$ 720.000
    assert "R$ 208.000" in content and "R$ 720.000" in content


def test_marketing_theme_and_styling():
    content = Path("marketing/index.html").read_text(encoding="utf-8")

    # Tema escuro e CSS inline
    assert "<style>" in content
    assert "</style>" in content
    # Checa uso de cores de fundo escuras
    assert "#0" in content or "#1" in content
    # Viewport responsivo
    assert "viewport" in content
    # Sem scripts externos inseguros ou frameworks pesados
    assert "<script src=" not in content or "google" in content
