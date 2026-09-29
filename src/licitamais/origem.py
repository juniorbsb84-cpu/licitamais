"""Link direto para o processo no portal da fonte (r51). Funcao pura."""

from __future__ import annotations

import re
from collections.abc import Mapping

_URL_RE = re.compile(r"https?://[^\s\)\]\}\"'<>]+")

_ROTULO_PROCESSO = "Abrir o processo no portal da fonte"
_CNPJ_PNCP = {"caixa": "00360305000104", "bb": "00000000000191", "bbts": "42318949001318"}
_ROTULO_EDITAL = "Baixar o edital no portal da fonte"
_ROTULO_LISTA = "Abrir a lista de processos no portal da fonte"


def _primeira_url(texto: object) -> str | None:
    if not isinstance(texto, str):
        return None
    achado = _URL_RE.search(texto)
    if achado is None:
        return None
    url = achado.group(0).rstrip(".,;:!?)]}'")
    return url or None


def _texto(valor: object) -> str:
    return str(valor or "").strip()


def _uf_senac(attrs: Mapping) -> str | None:
    for chave in ("org_native_id", "uf", "regional"):
        valor = attrs.get(chave) if isinstance(attrs, Mapping) else None
        if not valor:
            continue
        texto = str(valor).strip()
        if "-" in texto:
            texto = texto.rsplit("-", 1)[-1]
        texto = texto.strip().upper()
        if len(texto) == 2 and texto.isalpha():
            return texto
    return None


def link_origem(fonte: str, native_id: str, attrs: dict, numero: str | None) -> dict | None:
    """Devolve {"url", "tipo", "rotulo"} ou None. Funcao pura, sem rede."""
    fonte = (fonte or "").strip()
    nativo = (native_id or "").strip()
    dados = attrs if isinstance(attrs, Mapping) else {}
    if fonte == "sistema_industria":
        if not nativo:
            return None
        return {
            "url": f"https://compras.sistemaindustria.com.br/compras/app/portalpublico/edital/{nativo}/detalhes/itens",
            "tipo": "processo",
            "rotulo": _ROTULO_PROCESSO,
        }
    if fonte == "sescoop":
        if not nativo:
            return None
        return {
            "url": f"https://compras.somoscooperativismo.coop.br/compras/app/portalpublico/edital/{nativo}/detalhes/itens",
            "tipo": "processo",
            "rotulo": _ROTULO_PROCESSO,
        }
    if fonte == "brb":
        url = _primeira_url(dados.get("observacao"))
        if not url:
            return None
        return {"url": url, "tipo": "processo", "rotulo": _ROTULO_PROCESSO}
    if fonte == "senac":
        processo = _texto(dados.get("link_processo"))
        if processo.startswith("http://") or processo.startswith("https://"):
            return {"url": processo, "tipo": "processo", "rotulo": _ROTULO_PROCESSO}
        edital = _texto(dados.get("link_edital"))
        if edital.startswith("http://") or edital.startswith("https://"):
            return {"url": edital, "tipo": "edital", "rotulo": _ROTULO_EDITAL}
        uf = _uf_senac(dados)
        if uf is None:
            return None
        return {
            "url": f"https://transparencia.senac.br/#/{uf.lower()}/licitacoes",
            "tipo": "lista",
            "rotulo": _ROTULO_LISTA,
        }
    if fonte == "sestsenat":
        return {
            "url": "https://transparencia.sestsenat.org.br/licitacoes-contratos/processos-compras-contratacao",
            "tipo": "lista",
            "rotulo": _ROTULO_LISTA,
        }
    if fonte in _CNPJ_PNCP:
        ano = None
        sequencial = None
        if isinstance(dados, Mapping):
            try:
                ano = int(dados.get("ano_contrato")) if dados.get("ano_contrato") is not None else None
            except (TypeError, ValueError):
                ano = None
            try:
                sequencial = (
                    int(dados.get("sequencial_contrato")) if dados.get("sequencial_contrato") is not None else None
                )
            except (TypeError, ValueError):
                sequencial = None
        if ano is None or sequencial is None:
            nativo = (native_id or "").strip()
            achados = re.findall(r"(\d{4})\D+(\d+)\s*$", nativo)
            if achados:
                try:
                    ano = int(achados[-1][0])
                    sequencial = int(achados[-1][1])
                except ValueError:
                    ano = None
                    sequencial = None
        if ano is None or sequencial is None:
            return None
        return {
            "url": f"https://pncp.gov.br/app/contratos/{_CNPJ_PNCP[fonte]}/{ano}/{sequencial}",
            "tipo": "processo",
            "rotulo": _ROTULO_PROCESSO,
        }
    return None
