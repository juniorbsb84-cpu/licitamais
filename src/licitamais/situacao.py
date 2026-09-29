"""Grupo da situacao publicada pela fonte. Regra unica; a data entra na view v_licitacao e em web.licitacao."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta, timezone

# Brasilia sem horario de verao desde 2019; o SQL usa o mesmo deslocamento: date('now', '-3 hours')
BRASILIA = timezone(timedelta(hours=-3))


def hoje_brasil(agora: datetime | None = None) -> str:
    return (agora or datetime.now(UTC)).astimezone(BRASILIA).date().isoformat()


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


# Ordem importa: "Edital Encerrado" nao pode cair em "aberta"; cancelamento vence tudo.
GRUPOS_SITUACAO = (
    ("cancelada", ("cancelad", "revogad", "anulad")),
    ("suspensa", ("suspens",)),
    ("aberta", ("edital aberto", "aguardando abertura", "credenciamento aberto", "registro de proposta")),
    ("encerrada", ("encerrad", "homologad", "finalizad", "desert", "fracassad")),
    (
        "andamento",
        (
            "analise",
            "recurs",
            "negociac",
            "aprovac",
            "reconsiderac",
            "em processo",
            "impugnad",
            "remanescente",
            "andamento",
        ),
    ),
)


def grupo_do_rotulo(rotulo: str | None) -> str | None:
    if not rotulo:
        return None
    t = sem_acento(rotulo)
    return next((g for g, chaves in GRUPOS_SITUACAO if any(c in t for c in chaves)), None)


_TEXTO_GRUPO = {"aberta": "Aberta", "encerrada": "Encerrada", "desconhecida": "Situação não informada"}


def situacao(rotulo: str | None, abertura: str | None, hoje: str | None = None) -> dict:
    """Agrupa a situacao publicada pela fonte; sem rotulo conhecido, decide pela data de abertura."""
    hoje = hoje or hoje_brasil()
    grupo = grupo_do_rotulo(rotulo)
    if grupo is not None:
        texto = rotulo.strip()
        futura = bool(abertura) and str(abertura)[:10] >= hoje
        passada = bool(abertura) and not futura
        if grupo == "andamento" and futura:
            grupo = "aberta"  # SENAC "Em processo" antes da sessao
        elif grupo == "aberta" and passada:
            # rotulo que a fonte nunca atualizou (SEST/SENAT tem "Edital Aberto" de 2022)
            grupo, texto = "andamento", f"{texto} (abertura em {data_br(abertura)[:10]} já passou)"
        elif grupo == "aberta" and not abertura:
            # "aberto" sem data nao confirma nada: 227 dispensas SEST/SENAT de 2023-2024 nunca atualizadas
            grupo, texto = "desconhecida", f"{texto} na fonte, sem data"
        return {"grupo": grupo, "texto": texto, "abertura": abertura}
    if abertura:
        grupo = "aberta" if str(abertura)[:10] >= hoje else "encerrada"
    else:
        grupo = "desconhecida"
    return {"grupo": grupo, "texto": _TEXTO_GRUPO[grupo], "abertura": abertura}


def data_br(valor) -> str:
    texto = str(valor or "")
    if len(texto) < 10 or texto[4] != "-":
        return texto or "-"
    data = f"{texto[8:10]}/{texto[5:7]}/{texto[:4]}"
    hora = texto[11:16] if len(texto) >= 16 and texto[10] in "T " else ""
    return f"{data}, {hora}" if hora and hora != "00:00" else data
