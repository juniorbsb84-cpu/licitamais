from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from licitamais import origem as origem_mod

from ..deps import Conta, db, leitor
from ..esquemas import Arquivo, Contrato, Fase, Item, LicitacaoDetalhe, LinkOrigem, Vencedor
from ..ocultas import fontes_ocultas
from .licitacoes import _dias

rota = APIRouter()


def _reais(cents):
    return None if cents is None else cents / 100.0


@rota.get("/licitacoes/{pid}", response_model=LicitacaoDetalhe)
def detalhe(pid: int, _: Conta | None = Depends(leitor), con: sqlite3.Connection = Depends(db)) -> LicitacaoDetalhe:
    v = con.execute("SELECT * FROM v_licitacao WHERE id = ?", (pid,)).fetchone()
    if v is None or v["fonte"] in fontes_ocultas():
        raise HTTPException(404, "licitação não encontrada")
    try:
        linha = con.execute("SELECT attrs, source_native_id FROM process WHERE id = ?", (pid,)).fetchone()
        attrs = json.loads(linha[0] or "{}")
        nativa = str(linha[1] or "")
    except (TypeError, ValueError):
        attrs = {}
        nativa = ""
    fases = [
        Fase(rotulo=r["phase_label"], inicio=r["started_at"], fim=r["ended_at"])
        for r in con.execute(
            "SELECT phase_label, started_at, ended_at FROM phase_event WHERE process_id = ? ORDER BY started_at, id",
            (pid,),
        )
    ]
    contratos = [
        Contrato(
            numero=r["number"],
            fornecedor=r["fornecedor"],
            cnpj=r["cnpj"],
            valor=_reais(r["value_cents"]),
            assinatura=r["signed_at_source"],
            vigencia_inicio=r["vigency_start"],
            vigencia_fim=r["vigency_end"],
        )
        for r in con.execute(
            "SELECT c.number, COALESCE(c.supplier_name_raw, o.name_raw) AS fornecedor, o.cnpj, c.value_cents,"
            " c.signed_at_source, c.vigency_start, c.vigency_end FROM contract c"
            " LEFT JOIN organization o ON o.id = c.supplier_org_id WHERE c.process_id = ?"
            " AND NOT EXISTS (SELECT 1 FROM contract n WHERE n.supersedes_id = c.id)"
            " ORDER BY c.signed_at_source DESC, c.id",
            (pid,),
        )
    ]
    vencedores = [
        Vencedor(
            item=r["item"],
            fornecedor=r["fornecedor"],
            cnpj=r["cnpj"],
            quantidade=r["qty_awarded"],
            valor=_reais(r["amount_cents"]),
        )
        for r in con.execute(
            "SELECT i.description AS item, COALESCE(a.supplier_name_raw, o.name_raw) AS fornecedor, o.cnpj,"
            " a.qty_awarded, a.amount_cents FROM award a LEFT JOIN organization o ON o.id = a.supplier_org_id"
            " LEFT JOIN item i ON i.id = a.item_id WHERE a.process_id = ?"
            " AND NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id) ORDER BY a.amount_cents DESC",
            (pid,),
        )
    ]
    itens = [
        Item(
            descricao=r["description"],
            quantidade=r["qty"],
            unidade=r["unit"],
            valor_estimado=_reais(r["total_price_estimated_cents"]),
        )
        for r in con.execute(
            "SELECT description, qty, unit, total_price_estimated_cents FROM item WHERE process_id = ? ORDER BY id",
            (pid,),
        )
    ]
    arquivos = [
        Arquivo(
            nome=r["filename"] or r["kind"] or "arquivo",
            url=r["url_download"] if str(r["url_download"] or "").startswith("https://") else None,
        )
        for r in con.execute(
            "SELECT filename, kind, url_download FROM attachment WHERE process_id = ? AND deleted_at IS NULL ORDER BY id",
            (pid,),
        )
    ]
    return LicitacaoDetalhe(
        id=v["id"],
        fonte=v["fonte"],
        numero=v["numero"],
        objeto=v["objeto"],
        modalidade=v["modalidade"],
        orgao=v["orgao"] or attrs.get("nome_filial") or attrs.get("org_nome"),
        abertura=v["abertura"],
        situacao=v["situacao"],
        rotulo=v["rotulo"],
        dias_para_abertura=_dias(v["abertura"]),
        publicacao=v["publicacao"],
        criterio=attrs.get("criterio_julgamento"),
        homologacao=attrs.get("data_homologacao"),
        link_origem=_link_origem(v["fonte"], nativa, attrs, v["numero"]),
        fases=fases,
        contratos=contratos,
        vencedores=vencedores,
        itens=itens,
        arquivos=arquivos,
    )


def _link_origem(fonte, nativa, attrs, numero):
    achado = origem_mod.link_origem(str(fonte or ""), str(nativa or ""), attrs or {}, numero)
    if achado is None:
        return None
    return LinkOrigem(url=achado["url"], tipo=achado["tipo"], rotulo=achado["rotulo"])
