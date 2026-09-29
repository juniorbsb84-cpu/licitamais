"""Tetos por fonte para a coleta diária."""

import pytest

from licitamais.adapters.brb import BRBAdapter
from licitamais.adapters.caixa import CaixaAdapter
from licitamais.adapters.iges import IgesAdapter
from licitamais.adapters.senac import SenacAdapter
from licitamais.adapters.sesc import SescAdapter
from licitamais.adapters.sescoop import SescoopAdapter
from licitamais.adapters.sestsenat import SestSenatAdapter
from licitamais.adapters.sistema_industria import SistemaIndustriaAdapter
from licitamais.runner import RunConfig, _parametros_de_taxa


@pytest.mark.parametrize(
    ("adaptador", "teto", "delay_minimo"),
    [
        (SestSenatAdapter, 2000, 250),
        (BRBAdapter, 2000, 250),
        (SenacAdapter, 600, 250),
        (IgesAdapter, 600, 250),
        (SistemaIndustriaAdapter, 600, 500),
        (SescoopAdapter, 600, 500),
        (SescAdapter, 600, 250),
        (CaixaAdapter, 400, 1000),
    ],
)
def test_teto_diario_e_delay_preservado(adaptador, teto, delay_minimo):
    rate = adaptador.capabilities.rate
    assert rate["max_calls_per_run"] == teto
    assert rate["min_delay_ms"] >= delay_minimo
    assert _parametros_de_taxa(RunConfig(), adaptador())[2] == teto

    # Pior caso com jitter atual: 2000*(250+100)=700 s; 600*(500+200)=420 s;
    # 600*(250+100)=210 s; Caixa 400*(1000+200)=480 s. Todos < 3600 s.
    assert teto * (rate["min_delay_ms"] + rate["jitter_ms"]) <= 3_600_000
