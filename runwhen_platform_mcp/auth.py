"""Authentication providers for the remote hosted MCP server.

Provides auth strategies that integrate with the RunWhen platform:

1. JWKSTokenVerifier -- verifies RS256 JWTs locally using PAPI's JWKS
   endpoint.  No per-request network call — keys are cached and refreshed
   periodically.  This is the fast path for OAuth-issued tokens (Phase 2,
   RW-454).

2. PAPITokenVerifier -- validates RunWhen JWTs and PATs by calling PAPI's
   /api/v3/users/whoami endpoint.  Still used as a fallback for HS256
   tokens and PATs that can't be verified via JWKS.

3. PAPI OAuth 2.1 via FastMCP's Auth0Provider (generic OIDC RP) pointing at
   PAPI's /.well-known/openid-configuration.  This is the preferred auth
   path now that PAPI is an OAuth 2.1 Authorization Server (RW-454).

4. Auth0 OAuth (legacy) -- still supported via MCP_AUTH0_* env vars for
   backward compatibility.

5. build_auth_provider() -- factory that wires up MultiAuth combining
   OAuth (interactive) and token verification (programmatic).

6. exchange_auth0_for_papi() -- called after Auth0 OAuth (legacy path) to
   convert the Auth0 access token into a PAPI JWT.
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier

logger = logging.getLogger(__name__)

_papi_token_cache: dict[str, str] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}  # papi_url -> (fetched_at, jwks_data)
JWKS_CACHE_TTL = 3600  # re-fetch keys every hour


async def _fetch_jwks(papi_url: str) -> dict | None:
    """Fetch JWKS from PAPI with caching."""
    now = time.monotonic()
    cached = _jwks_cache.get(papi_url)
    if cached and (now - cached[0]) < JWKS_CACHE_TTL:
        return cached[1]

    url = f"{papi_url}/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            jwks_data = resp.json()
            _jwks_cache[papi_url] = (now, jwks_data)
            logger.info("JWKS fetched from %s (%d keys)", url, len(jwks_data.get("keys", [])))
            return jwks_data
    except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as exc:
        logger.warning("JWKS fetch failed from %s: %s", url, exc)
        if cached:
            return cached[1]
        return None


class JWKSTokenVerifier(TokenVerifier):
    """Verifies RS256 JWTs locally using PAPI's JWKS endpoint.

    No per-request network call to PAPI — keys are cached and refreshed
    periodically.  This is the fast path for tokens issued by PAPI's
    OAuth 2.1 AS.  Falls through to None for HS256 tokens and PATs.
    """

    def __init__(self, papi_url: str) -> None:
        super().__init__()
        self._papi_url = papi_url.rstrip("/")

    async def verify_token(self, token: str) -> AccessToken | None:
        from jose import JWTError
        from jose import jwt as jose_jwt

        jwks = await _fetch_jwks(self._papi_url)
        if not jwks or not jwks.get("keys"):
            return None

        try:
            unverified_header = jose_jwt.get_unverified_header(token)
        except JWTError:
            return None

        if unverified_header.get("alg") != "RS256":
            return None

        kid = unverified_header.get("kid")
        rsa_key = None
        for key in jwks["keys"]:
            if key.get("kid") == kid:
                rsa_key = key
                break

        if rsa_key is None:
            jwks_refreshed = await self._force_refresh_jwks()
            if jwks_refreshed:
                for key in jwks_refreshed.get("keys", []):
                    if key.get("kid") == kid:
                        rsa_key = key
                        break

        if rsa_key is None:
            return None

        try:
            payload = jose_jwt.decode(
                token,
                rsa_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except JWTError:
            return None

        user_id = payload.get("sub", "")
        return AccessToken(
            token=token,
            client_id="papi-jwt",
            scopes=["openid", "profile", "email"],
            claims={
                "sub": user_id,
                "iss": payload.get("iss", ""),
                "type": payload.get("type", "access"),
            },
        )

    async def _force_refresh_jwks(self) -> dict | None:
        _jwks_cache.pop(self._papi_url, None)
        return await _fetch_jwks(self._papi_url)


class PAPITokenVerifier(TokenVerifier):
    """Validates RunWhen JWTs and PATs by calling PAPI's whoami endpoint.

    This is the fallback for HS256 tokens and PATs that can't be verified
    via JWKS.  Incurs a network roundtrip per verification.
    """

    def __init__(self, papi_url: str, timeout: float = 10.0) -> None:
        super().__init__()
        self._papi_url = papi_url.rstrip("/")
        self._timeout = timeout

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a token by calling PAPI's whoami endpoint.

        Returns an AccessToken if valid, None if invalid/expired.
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._papi_url}/api/v3/users/whoami",
                    headers=headers,
                )
                if resp.status_code in (401, 403):
                    return None
                resp.raise_for_status()

                data = resp.json()
                user_id = str(data.get("id", ""))
                email = (
                    data.get("primaryEmail")
                    or data.get("primary_email")
                    or data.get("username", "")
                )

                return AccessToken(
                    token=token,
                    client_id="runwhen-pat",
                    scopes=["openid", "profile", "email"],
                    claims={
                        "sub": user_id,
                        "email": email,
                        "username": data.get("username", ""),
                        "is_staff": data.get("isStaff", False) or data.get("is_staff", False),
                        "is_superuser": (
                            data.get("isSuperuser", False) or data.get("is_superuser", False)
                        ),
                    },
                )
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException):
            return None


async def exchange_auth0_for_papi(auth0_token: str, papi_url: str) -> str | None:
    """Exchange an Auth0 access token for a PAPI JWT.

    Calls PAPI's /api/v3/token/exchange/ endpoint which validates the
    Auth0 token against Auth0's /userinfo and issues a PAPI JWT.

    Returns the PAPI access token on success, None on failure.
    Results are cached by auth0_token to avoid repeated exchanges.
    """
    if auth0_token in _papi_token_cache:
        return _papi_token_cache[auth0_token]

    url = f"{papi_url.rstrip('/')}/api/v3/token/exchange/"
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": auth0_token,
        "subject_token_type": "auth0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                logger.warning(
                    "Token exchange failed: %d %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None

            data = resp.json()
            papi_token = data.get("access_token")
            if papi_token:
                _papi_token_cache[auth0_token] = papi_token
            return papi_token

    except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as exc:
        logger.warning("Token exchange request failed: %s", exc)
        return None


def build_auth_provider(
    papi_url: str | None = None,
    auth0_config_url: str | None = None,
    auth0_client_id: str | None = None,
    auth0_client_secret: str | None = None,
    auth0_audience: str | None = None,
    base_url: str | None = None,
    # PAPI OIDC (preferred — RW-454)
    papi_oauth_client_id: str | None = None,
    papi_oauth_client_secret: str | None = None,
) -> TokenVerifier | MultiAuth:
    """Build the appropriate auth provider based on available configuration.

    Priority order:
      1. **PAPI OIDC** (MCP_PAPI_OAUTH_CLIENT_ID + SECRET) — PAPI is the AS.
         Uses PAPI's ``/.well-known/openid-configuration``.  No token exchange
         needed because the tokens are already PAPI JWTs.
      2. **Auth0** (MCP_AUTH0_*) — legacy path, still supported.
      3. **PAT-only** — fall-through when no OAuth is configured.

    Configuration env vars (parameters take precedence):
      - RW_API_URL: PAPI base URL (required)
      - MCP_BASE_URL: Public URL of this MCP server (required for OAuth)
      - MCP_PAPI_OAUTH_CLIENT_ID: PAPI OAuth client ID (preferred)
      - MCP_PAPI_OAUTH_CLIENT_SECRET: PAPI OAuth client secret (preferred)
      - MCP_AUTH0_CONFIG_URL: Auth0 OIDC config URL (legacy)
      - MCP_AUTH0_CLIENT_ID: Auth0 OAuth app client ID (legacy)
      - MCP_AUTH0_CLIENT_SECRET: Auth0 OAuth app client secret (legacy)
      - MCP_AUTH0_AUDIENCE: Auth0 API audience identifier (legacy)
    """
    papi = papi_url or os.environ.get("RW_API_URL", "")
    if not papi:
        raise ValueError(
            "RW_API_URL is required for remote MCP server authentication. "
            "Set it to your PAPI base URL (e.g. https://papi.beta.runwhen.com)."
        )
    papi = papi.rstrip("/")

    jwks_verifier = JWKSTokenVerifier(papi_url=papi)
    pat_verifier = PAPITokenVerifier(papi_url=papi)
    verifiers: list[TokenVerifier] = [jwks_verifier, pat_verifier]

    server_url = base_url or os.environ.get("MCP_BASE_URL", "")

    # --- Path 1: PAPI as OAuth 2.1 AS (preferred) ---
    p_client_id = papi_oauth_client_id or os.environ.get("MCP_PAPI_OAUTH_CLIENT_ID", "")
    p_client_secret = papi_oauth_client_secret or os.environ.get("MCP_PAPI_OAUTH_CLIENT_SECRET", "")

    if p_client_id and p_client_secret and server_url:
        from fastmcp.server.auth.providers.auth0 import Auth0Provider

        papi_oidc_provider = Auth0Provider(
            config_url=f"{papi}/.well-known/openid-configuration",
            client_id=p_client_id,
            client_secret=p_client_secret,
            audience=p_client_id,
            base_url=server_url,
            token_endpoint_auth_method="client_secret_post",
        )
        logger.info("Using PAPI as OAuth 2.1 AS for MCP auth (RW-454)")
        return MultiAuth(
            server=papi_oidc_provider,
            verifiers=verifiers,
        )

    # --- Path 2: Auth0 (legacy) ---
    config_url = auth0_config_url or os.environ.get("MCP_AUTH0_CONFIG_URL", "")
    client_id = auth0_client_id or os.environ.get("MCP_AUTH0_CLIENT_ID", "")
    client_secret = auth0_client_secret or os.environ.get("MCP_AUTH0_CLIENT_SECRET", "")
    audience = auth0_audience or os.environ.get("MCP_AUTH0_AUDIENCE", "")

    if config_url and client_id and client_secret and server_url:
        from fastmcp.server.auth.providers.auth0 import Auth0Provider

        auth0_provider = Auth0Provider(
            config_url=config_url,
            client_id=client_id,
            client_secret=client_secret,
            audience=audience or client_id,
            base_url=server_url,
        )
        logger.info("Using Auth0 for MCP auth (legacy path)")
        return MultiAuth(
            server=auth0_provider,
            verifiers=verifiers,
        )

    # --- Path 3: verifiers-only (JWKS + PAT, no interactive OAuth) ---
    logger.info("No OAuth configured — JWKS + PAT verification mode")
    return MultiAuth(server=None, verifiers=verifiers)
