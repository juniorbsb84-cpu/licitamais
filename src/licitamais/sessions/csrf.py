"""Sessao com handshake CSRF de tres passos para portais estilo GoBuyer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

import requests

from licitamais.types import FetchRequest

CONTENT_TYPE_JSON = "application/json; charset=utf-8"
TIMEOUT_SEGUNDOS = 30


@dataclass
class CsrfSession:
    token: str | None = None
    cookie_jar: dict[str, str] = field(default_factory=dict)
    last_handshake_at: str = ""


@dataclass
class RawPage:
    request: FetchRequest
    status_code: int
    headers: dict[str, str]
    body: bytes


def url_decode_token(raw: str) -> str:
    return unquote(raw)


def is_retryable_auth(http_status: int) -> bool:
    return http_status in (401, 403)


class CsrfSessionManager:
    def __init__(self, base_url: str, user_agent: str, ttl_seconds: int = 600) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self.ttl_seconds = ttl_seconds

    def open(self) -> CsrfSession:
        return CsrfSession()

    def refresh(self, sess: CsrfSession) -> CsrfSession:
        return handshake_3_steps(self, sess)

    def call(self, sess: CsrfSession, req: FetchRequest) -> RawPage:
        if _handshake_expirado(sess, self.ttl_seconds):
            self.refresh(sess)

        pagina = self._chamada_dados(sess, req)
        if is_retryable_auth(pagina.status_code):
            self.refresh(sess)
            pagina = self._chamada_dados(sess, req)

        if not 200 <= pagina.status_code < 300:
            erro = RuntimeError(f"chamada de dados falhou com HTTP {pagina.status_code}: {req.endpoint}")
            try:
                cabecalhos = dict(pagina.headers or {})
            except Exception:
                cabecalhos = {}
            erro.status = pagina.status_code
            erro.status_code = pagina.status_code
            erro.http_status = pagina.status_code
            erro.headers = cabecalhos
            erro.response_headers = cabecalhos
            erro.response = pagina
            raise erro
        return pagina

    def _chamada_dados(self, sess: CsrfSession, req: FetchRequest) -> RawPage:
        headers = _headers_base(self)
        headers["X-XSRF-TOKEN"] = sess.token or ""
        headers.update(dict(req.headers_extra))

        resposta = requests.Session().request(
            req.method.upper(),
            _url_dados(self.base_url, req.endpoint),
            headers=headers,
            params=dict(req.params),
            cookies=dict(sess.cookie_jar),
            timeout=TIMEOUT_SEGUNDOS,
        )
        _absorver_cookies(sess, resposta)

        corpo = resposta.content
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")

        return RawPage(
            request=req,
            status_code=resposta.status_code,
            headers=dict(resposta.headers),
            body=corpo,
        )


def handshake_3_steps(mgr: CsrfSessionManager, sess: CsrfSession) -> CsrfSession:
    _get(mgr, sess, _url_portal_publico(mgr.base_url))
    resposta_token = _get(mgr, sess, _url_token(mgr.base_url))

    codigo_token = getattr(resposta_token, "status_code", None)
    try:
        cabecalhos_token = dict(getattr(resposta_token, "headers", None) or {})
    except Exception:
        cabecalhos_token = {}
    codigo_int = None
    if codigo_token is not None:
        try:
            codigo_int = int(codigo_token)
        except Exception:
            codigo_int = None
    if codigo_int is not None and not 200 <= codigo_int < 300:
        erro_status = RuntimeError(f"handshake CSRF falhou com HTTP {codigo_int}")
        erro_status.status = codigo_int
        erro_status.status_code = codigo_int
        erro_status.http_status = codigo_int
        erro_status.headers = cabecalhos_token
        erro_status.response_headers = cabecalhos_token
        erro_status.response = resposta_token
        raise erro_status

    token_bruto = _extrair_token_bruto(resposta_token)
    if not token_bruto:
        erro = RuntimeError("handshake CSRF nao devolveu token")
        if codigo_int is not None:
            erro.status = codigo_int
            erro.status_code = codigo_int
            erro.http_status = codigo_int
        erro.headers = cabecalhos_token
        erro.response_headers = cabecalhos_token
        erro.response = resposta_token
        raise erro

    sess.token = url_decode_token(token_bruto)
    sess.last_handshake_at = datetime.now(UTC).isoformat()
    return sess


def _origem(base_url: str) -> str:
    partes = urlsplit(base_url)
    return f"{partes.scheme}://{partes.netloc}"


def _url_portal_publico(base_url: str) -> str:
    if "portalpublico" in base_url.lower():
        return base_url
    return base_url.rstrip("/") + "/app/portalpublico"


def _url_token(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/csrf/token"


def _url_dados(base_url: str, endpoint: str) -> str:
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return base_url.rstrip("/") + endpoint


def _headers_base(mgr: CsrfSessionManager) -> dict[str, str]:
    return {
        "User-Agent": mgr.user_agent,
        "Content-Type": CONTENT_TYPE_JSON,
        "Referer": _url_portal_publico(mgr.base_url),
    }


def _get(mgr: CsrfSessionManager, sess: CsrfSession, url: str):
    resposta = requests.Session().request(
        "GET",
        url,
        headers=_headers_base(mgr),
        cookies=dict(sess.cookie_jar),
        timeout=TIMEOUT_SEGUNDOS,
    )
    _absorver_cookies(sess, resposta)
    return resposta


def _absorver_cookies(sess: CsrfSession, resposta: object) -> None:
    cookies = getattr(resposta, "cookies", None)
    if cookies is None:
        return

    if isinstance(cookies, dict):
        sess.cookie_jar.update(cookies)
        return

    for cookie in cookies:
        sess.cookie_jar[cookie.name] = cookie.value


def _extrair_token_bruto(resposta: object) -> str | None:
    try:
        dados = json.loads(resposta.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError):
        dados = None

    if isinstance(dados, dict) and isinstance(dados.get("token"), str):
        return dados["token"]

    for cookie in getattr(resposta, "cookies", None) or []:
        if cookie.name.upper() == "XSRF-TOKEN":
            return cookie.value
    return None


def _handshake_expirado(sess: CsrfSession, ttl_seconds: int) -> bool:
    if not sess.token or not sess.last_handshake_at:
        return True

    try:
        instante = datetime.fromisoformat(sess.last_handshake_at)
    except ValueError:
        return True

    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=UTC)

    return (datetime.now(UTC) - instante).total_seconds() > ttl_seconds
