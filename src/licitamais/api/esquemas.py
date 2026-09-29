from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Situacao = Literal["aberta", "andamento", "suspensa", "encerrada", "cancelada", "desconhecida"]


class LicitacaoResumo(BaseModel):
    id: int
    fonte: str
    numero: str | None
    objeto: str | None
    modalidade: str | None
    orgao: str | None
    abertura: str | None
    situacao: Situacao
    rotulo: str | None
    dias_para_abertura: int | None


class PaginaLicitacoes(BaseModel):
    itens: list[LicitacaoResumo]
    total: int
    pagina: int
    por_pagina: int
    facetas: dict[str, dict[str, int]]


class Fase(BaseModel):
    rotulo: str | None
    inicio: str | None
    fim: str | None


class Contrato(BaseModel):
    numero: str | None
    fornecedor: str | None
    cnpj: str | None
    valor: float | None
    assinatura: str | None
    vigencia_inicio: str | None
    vigencia_fim: str | None


class Vencedor(BaseModel):
    item: str | None
    fornecedor: str | None
    cnpj: str | None
    quantidade: float | None
    valor: float | None


class Item(BaseModel):
    descricao: str | None
    quantidade: float | None
    unidade: str | None
    valor_estimado: float | None


class Arquivo(BaseModel):
    nome: str
    url: str | None


class LinkOrigem(BaseModel):
    url: str
    tipo: Literal["processo", "edital", "lista"]
    rotulo: str


class LicitacaoDetalhe(LicitacaoResumo):
    publicacao: str | None
    criterio: str | None
    homologacao: str | None
    link_origem: LinkOrigem | None = None
    fases: list[Fase]
    contratos: list[Contrato]
    vencedores: list[Vencedor]
    itens: list[Item]
    arquivos: list[Arquivo]
