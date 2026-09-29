"""Limite por conta para buscas pesadas em uma instancia da API; visitantes do site aberto por IP e no total."""

import os
from collections import deque
from threading import Lock
from time import monotonic

from fastapi import Depends, HTTPException, Request

from .deps import Conta, leitor

_MENSAGEM = "Muitas consultas. Aguarde um minuto e tente novamente."


class LimiteConsultas:
    def __init__(self, max_consultas: int = 60, janela_segundos: int = 60) -> None:
        self.max_consultas = max_consultas
        self.janela_segundos = janela_segundos
        self.acessos: dict[int, deque[float]] = {}
        self.lock = Lock()
        self.ultima_limpeza = 0.0

    def verificar(self, account_id: int) -> None:
        instante = monotonic()
        with self.lock:
            if instante - self.ultima_limpeza >= self.janela_segundos:
                for aid, tempos in list(self.acessos.items()):
                    while tempos and instante - tempos[0] >= self.janela_segundos:
                        tempos.popleft()
                    if not tempos:
                        del self.acessos[aid]
                self.ultima_limpeza = instante
            tempos = self.acessos.setdefault(account_id, deque())
            while tempos and instante - tempos[0] >= self.janela_segundos:
                tempos.popleft()
            if len(tempos) >= self.max_consultas:
                raise HTTPException(429, _MENSAGEM)
            tempos.append(instante)


class LimiteVisitantes:
    """Visitante sem conta: teto por IP e teto somado de todos os visitantes do processo."""

    def __init__(self, por_ip: int = 30, total: int = 600, janela_segundos: int = 60) -> None:
        self.por_ip = LimiteConsultas(por_ip, janela_segundos)
        self.total = total
        self.janela_segundos = janela_segundos
        self.todos: deque[float] = deque()
        self.lock = Lock()

    def verificar(self, ip: str) -> None:
        instante = monotonic()
        with self.lock:
            while self.todos and instante - self.todos[0] >= self.janela_segundos:
                self.todos.popleft()
            if len(self.todos) >= self.total:
                raise HTTPException(429, _MENSAGEM)
        self.por_ip.verificar(ip)  # type: ignore[arg-type]
        with self.lock:
            self.todos.append(instante)


def ip_visitante(request: Request) -> str:
    if os.environ.get("LICITAMAIS_CONFIA_CLOUDFLARE") == "1" and request.headers.get("CF-Connecting-IP"):
        return request.headers["CF-Connecting-IP"].strip()
    return request.client.host if request.client else "desconhecido"


def limitar_consulta(request: Request, atual: Conta | None = Depends(leitor)) -> Conta | None:
    """O processo unico usa este limite; o proxy deve limitar a carga total."""
    if atual is not None:
        request.app.state.limite_consultas.verificar(atual.id)
    else:
        request.app.state.limite_visitantes.verificar(ip_visitante(request))
    return atual
