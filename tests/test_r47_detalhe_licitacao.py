"""r47: regra de situacao da licitacao (rotulo da fonte + data de abertura)."""

from __future__ import annotations

import pytest

from licitamais.situacao import situacao


@pytest.mark.parametrize(
    "rotulo,abertura,grupo",
    [
        # 24/09: rotulo "aberto" sem data nao confirma nada (227 dispensas SEST/SENAT de 2023-2024 nunca atualizadas)
        ("Edital Aberto", None, "desconhecida"),
        ("Aguardando abertura", None, "desconhecida"),
        ("Em Registro de Proposta", None, "desconhecida"),
        ("Licitação homologada", None, "encerrada"),
        ("Edital Encerrado", "2030-01-01", "encerrada"),
        ("Finalizado", None, "encerrada"),
        ("Deserto", None, "encerrada"),
        ("Processo Licitatório Cancelado", None, "cancelada"),
        ("Licitação revogada", None, "cancelada"),
        ("Anulado", None, "cancelada"),
        ("Licitação suspensa", None, "suspensa"),
        ("Análise de proposta(s) comercial(is)", None, "andamento"),
        ("Em processo", None, "andamento"),
        ("Em processo", "2026-10-06", "aberta"),
        ("Em processo", "2026-09-01", "andamento"),
        ("Edital Aberto", "2024-03-01", "andamento"),
        ("Edital Aberto", "2026-09-24", "aberta"),
        (None, "2026-10-06", "aberta"),
        (None, "2026-09-01", "encerrada"),
        (None, None, "desconhecida"),
        ("TERMOS ADITIVOS", "2026-09-01", "encerrada"),
    ],
)
def test_situacao_agrupa_rotulo_da_fonte(rotulo, abertura, grupo):
    assert situacao(rotulo, abertura, hoje="2026-09-24")["grupo"] == grupo
