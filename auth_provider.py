"""
Servidor de autorizacion OAuth 2.1 para el MCP server.

Disenado para un unico usuario (vos). Los tokens se persisten en Redis
para sobrevivir reinicios del proceso en Railway — resuelve el problema
de needs_reconnect despues de cada restart.

Si Redis no esta disponible (desarrollo local sin REDIS_URL), cae a
almacenamiento en memoria con un warning.
"""
import os
import time
import secrets
import json
import logging
from typing import Optional

import redis as redis_lib
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL_SECONDS  = 60 * 60 * 24 * 30    # 30 dias
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 365   # 1 año
AUTH_CODE_TTL_SECONDS     = 60 * 5               # 5 min para completar el login

# Codigos de un solo uso que vinculan la sesion de login HTML con el
# pedido de autorizacion OAuth original. Estos si van en memoria
# (son efimeros por diseno, duran 5 minutos maximo).
_pending_logins: dict[str, AuthorizationParams] = {}
_pending_clients: dict[str, OAuthClientInformationFull] = {}


# ---------- Redis ----------

def _get_redis() -> Optional[redis_lib.Redis]:
    """Devuelve una conexion a Redis, o None si no hay REDIS_URL configurada."""
    url = os.environ.get("REDIS_URL")
    if not url:
        logger.warning("REDIS_URL no configurada — usando almacenamiento en memoria (tokens no persisten entre reinicios)")
        return None
    try:
        r = redis_lib.from_url(url, decode_responses=True)
        r.ping()
        return r
    except Exception as e:
        logger.error("No se pudo conectar a Redis: %s — usando memoria como fallback", e)
        return None


_redis: Optional[redis_lib.Redis] = None
_redis_checked = False

def get_redis() -> Optional[redis_lib.Redis]:
    global _redis, _redis_checked
    if not _redis_checked:
        _redis = _get_redis()
        _redis_checked = True
        if _redis:
            logger.info("Redis conectado — tokens persistentes activos")
    return _redis


# ---------- Helpers de storage (Redis o memoria) ----------

# Fallback en memoria para desarrollo local
_mem: dict = {}

def _store(key: str, value: dict, ttl: int):
    r = get_redis()
    if r:
        r.setex(key, ttl, json.dumps(value))
    else:
        _mem[key] = (time.time() + ttl, value)

def _fetch(key: str) -> Optional[dict]:
    r = get_redis()
    if r:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    else:
        entry = _mem.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del _mem[key]
            return None
        return value

def _delete(key: str):
    r = get_redis()
    if r:
        r.delete(key)
    else:
        _mem.pop(key, None)

def _keys_with_prefix(prefix: str) -> list:
    r = get_redis()
    if r:
        return list(r.scan_iter(f"{prefix}*"))
    else:
        now = time.time()
        return [k for k, (exp, _) in list(_mem.items()) if k.startswith(prefix) and exp > now]


# ---------- Provider OAuth ----------

class SingleUserOAuthProvider(OAuthAuthorizationServerProvider):
    """Provider OAuth 2.1 con almacenamiento persistente en Redis."""

    # ---- Clientes registrados ----

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        data = _fetch(f"oauth:client:{client_id}")
        if not data:
            return None
        return OAuthClientInformationFull(**data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        _store(
            f"oauth:client:{client_info.client_id}",
            client_info.model_dump(mode="json"),
            ttl=REFRESH_TOKEN_TTL_SECONDS,
        )

    # ---- Autorizacion ----

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        login_id = secrets.token_urlsafe(24)
        _pending_logins[login_id] = params
        _pending_clients[login_id] = client
        return f"/login?login_id={login_id}"

    # ---- Authorization codes ----

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        data = _fetch(f"oauth:code:{authorization_code}")
        if not data:
            return None
        return AuthorizationCode(**data)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        _delete(f"oauth:code:{authorization_code.code}")

        access_token  = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        now = int(time.time())

        _store(f"oauth:access:{access_token}", {
            "token": access_token,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "expires_at": now + ACCESS_TOKEN_TTL_SECONDS,
        }, ttl=ACCESS_TOKEN_TTL_SECONDS)

        _store(f"oauth:refresh:{refresh_token}", {
            "token": refresh_token,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "expires_at": now + REFRESH_TOKEN_TTL_SECONDS,
        }, ttl=REFRESH_TOKEN_TTL_SECONDS)

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # ---- Refresh tokens ----

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        data = _fetch(f"oauth:refresh:{refresh_token}")
        if not data:
            return None
        return RefreshToken(**data)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        _delete(f"oauth:refresh:{refresh_token.token}")

        new_access  = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        now = int(time.time())
        use_scopes = scopes or refresh_token.scopes

        _store(f"oauth:access:{new_access}", {
            "token": new_access,
            "client_id": client.client_id,
            "scopes": use_scopes,
            "expires_at": now + ACCESS_TOKEN_TTL_SECONDS,
        }, ttl=ACCESS_TOKEN_TTL_SECONDS)

        _store(f"oauth:refresh:{new_refresh}", {
            "token": new_refresh,
            "client_id": client.client_id,
            "scopes": use_scopes,
            "expires_at": now + REFRESH_TOKEN_TTL_SECONDS,
        }, ttl=REFRESH_TOKEN_TTL_SECONDS)

        return OAuthToken(
            access_token=new_access,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=new_refresh,
            scope=" ".join(use_scopes) if use_scopes else None,
        )

    # ---- Verificacion de access tokens ----

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        data = _fetch(f"oauth:access:{token}")
        if not data:
            return None
        return AccessToken(**data)

    async def revoke_token(self, token) -> None:
        _delete(f"oauth:access:{token.token}")
        _delete(f"oauth:refresh:{token.token}")

    # ---- Helper interno: completar el login ----

    def complete_login(self, login_id: str) -> str:
        params = _pending_logins.pop(login_id)
        client = _pending_clients.pop(login_id)

        code = secrets.token_urlsafe(32)
        _store(f"oauth:code:{code}", {
            "code": code,
            "scopes": params.scopes or [],
            "expires_at": time.time() + AUTH_CODE_TTL_SECONDS,
            "client_id": client.client_id,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
        }, ttl=AUTH_CODE_TTL_SECONDS)

        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)


provider = SingleUserOAuthProvider()


# ---------- Rutas HTTP: pantalla de login ----------

LOGIN_PAGE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Garmin + Strava Coach — Login</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0f1115; color:#eee;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
  form {{ background:#1b1e25; padding:32px; border-radius:12px; width:300px; }}
  h1 {{ font-size:18px; margin-bottom:20px; }}
  input {{ width:100%; padding:10px; margin-bottom:12px; border-radius:6px;
           border:1px solid #333; background:#0f1115; color:#eee; box-sizing:border-box; }}
  button {{ width:100%; padding:10px; border-radius:6px; border:none;
            background:#1e90ff; color:white; font-weight:600; cursor:pointer; }}
  .error {{ color:#ff7070; font-size:13px; margin-bottom:12px; }}
</style>
</head>
<body>
  <form method="POST" action="/login">
    <h1>Acceso a tu Coach (Garmin + Strava)</h1>
    {error_html}
    <input type="hidden" name="login_id" value="{login_id}">
    <input type="text" name="username" placeholder="Usuario" autofocus required>
    <input type="password" name="password" placeholder="Contraseña" required>
    <button type="submit">Ingresar</button>
  </form>
</body>
</html>
"""


async def login_get(request: Request):
    login_id = request.query_params.get("login_id", "")
    if login_id not in _pending_logins:
        return HTMLResponse("Sesion de login invalida o expirada. Volve a intentar desde Claude.", status_code=400)
    return HTMLResponse(LOGIN_PAGE.format(error_html="", login_id=login_id))


async def login_post(request: Request):
    form = await request.form()
    login_id  = str(form.get("login_id", ""))
    username  = str(form.get("username", ""))
    password  = str(form.get("password", ""))

    if login_id not in _pending_logins:
        return HTMLResponse("Sesion de login invalida o expirada. Volve a intentar desde Claude.", status_code=400)

    expected_user = os.environ.get("OWNER_USERNAME", "")
    expected_pass = os.environ.get("OWNER_PASSWORD", "")

    if not secrets.compare_digest(username, expected_user) or not secrets.compare_digest(password, expected_pass):
        return HTMLResponse(
            LOGIN_PAGE.format(
                error_html='<div class="error">Usuario o contraseña incorrectos.</div>',
                login_id=login_id,
            ),
            status_code=401,
        )

    redirect_uri = provider.complete_login(login_id)
    return RedirectResponse(redirect_uri, status_code=302)


login_routes = [
    Route("/login", endpoint=login_get,  methods=["GET"]),
    Route("/login", endpoint=login_post, methods=["POST"]),
]
