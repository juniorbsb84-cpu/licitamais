"""Registro idempotente das fontes oficiais e suas sondas de saude (T18).

O banco nasce sem linhas em source e source_probe; registrar_fontes e o unico
criador oficial dessas linhas. Chamada repetida nao duplica nada nem sobrescreve
ajustes manuais (ex.: enabled desligado pelo operador).
"""

from __future__ import annotations

import json
import sqlite3

import licitamais.adapters.brb as modulo_brb
import licitamais.adapters.caixa as modulo_caixa
import licitamais.adapters.iges as modulo_iges
import licitamais.adapters.senac as modulo_senac
import licitamais.adapters.sescoop as modulo_sescoop
import licitamais.adapters.sestsenat as modulo_sestsenat
import licitamais.adapters.sistema_industria as modulo_si

MIN_ROWS_PCT = 0.85
MAX_STALENESS_DAYS = 30
MAX_QUARANTINE_PCT = 0.02

_FONTES = (
    (
        "brb",
        "https://pdd.brb.com.br/PLC",
        modulo_brb,
        modulo_brb.BRBAdapter,
    ),
    (
        "sistema_industria",
        "https://compras.sistemaindustria.com.br/compras",
        modulo_si,
        modulo_si.SistemaIndustriaAdapter,
    ),
    (
        "senac",
        "https://transparencia.senac.br",
        modulo_senac,
        modulo_senac.SenacAdapter,
    ),
    (
        "iges",
        "https://igesdf.org.br",
        modulo_iges,
        modulo_iges.IgesAdapter,
    ),
    (
        "sestsenat",
        "https://transparencia.sestsenat.org.br/api",
        modulo_sestsenat,
        modulo_sestsenat.SestSenatAdapter,
    ),
    (
        "sescoop",
        "https://compras.somoscooperativismo.coop.br/compras",
        modulo_sescoop,
        modulo_sescoop.SescoopAdapter,
    ),
    (
        "caixa",
        "https://pncp.gov.br",
        modulo_caixa,
        modulo_caixa.CaixaAdapter,
    ),
    ("bb", "https://pncp.gov.br", modulo_caixa, modulo_caixa.BBAdapter),
    ("bbts", "https://pncp.gov.br", modulo_caixa, modulo_caixa.BBTSAdapter),
)


def _campos_obrigatorios(modulo: object) -> list[str]:
    for nome in ("CAMPOS_SONDA", "_CAMPOS_OBRIGATORIOS", "CAMPOS_OBRIGATORIOS"):
        valor = getattr(modulo, nome, None)
        if valor:
            return [str(campo) for campo in valor]
    # sonda com campos inventados nunca acusaria campo ausente: falhar alto
    raise ValueError(f"adaptador {getattr(modulo, '__name__', modulo)} nao declara _CAMPOS_OBRIGATORIOS")


def _criar_probe(con: sqlite3.Connection, source_id: int, modulo: object) -> None:
    con.execute(
        "INSERT INTO source_probe (source_id, min_rows_pct, required_fields,"
        " max_staleness_days, max_quarantine_pct) VALUES (?, ?, ?, ?, ?)",
        (
            source_id,
            MIN_ROWS_PCT,
            json.dumps(_campos_obrigatorios(modulo), ensure_ascii=False),
            MAX_STALENESS_DAYS,
            MAX_QUARANTINE_PCT,
        ),
    )


def registrar_fontes(con: sqlite3.Connection) -> int:
    """Insere as fontes oficiais com uma sonda cada.

    Devolve quantas linhas foram criadas agora: fonte nova (com sonda) ou
    sonda recriada para fonte existente. Fonte ja registrada nunca e
    atualizada, preservando ajustes manuais (ex.: enabled desligado).
    """
    criadas = 0
    for codigo, base_url, modulo, classe_adapter in _FONTES:
        existente = con.execute("SELECT id FROM source WHERE code = ?", (codigo,)).fetchone()
        if existente is None:
            cursor = con.execute(
                "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
                " VALUES (?, 'api_json', ?, ?, 1)",
                (codigo, base_url, str(classe_adapter.adapter_version)),
            )
            _criar_probe(con, int(cursor.lastrowid), modulo)
            criadas += 1
            continue
        source_id = int(existente[0])
        sonda = con.execute("SELECT 1 FROM source_probe WHERE source_id = ?", (source_id,)).fetchone()
        if sonda is None:
            _criar_probe(con, source_id, modulo)
            criadas += 1
    return criadas
