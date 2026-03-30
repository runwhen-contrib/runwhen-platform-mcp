"""Authentication providers for the remote hosted MCP server.

Provides auth strategies that integrate with the RunWhen platform:

1. PAPITokenVerifier -- validates RunWhen JWTs and PATs by calling PAPI's
   /api/v3/users/whoami endpoint. Works with HS256 tokens without needing
   the signing secret.

2. Auth0 OAuth via FastMCP's Auth0Provider for interactive clients
   (Cursor, Claude.ai). After OAuth, the Auth0 access token is exchanged
   for a PAPI JWT via PAPI's /api/v3/token/exchange/ endpoint.

3. build_auth_provider() -- factory that wires up MultiAuth combining both
   strategies: OAuth for interactive clients, PAT for programmatic use.

4. exchange_auth0_for_papi() -- called after OAuth to convert the Auth0
   access token into a PAPI JWT that tools can use for API calls.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier

logger = logging.getLogger(__name__)

_papi_token_cache: dict[str, str] = {}


class PAPITokenVerifier(TokenVerifier):
    """Validates RunWhen JWTs and PATs by calling PAPI's whoami endpoint.

    This avoids needing to share the HS256 signing secret outside PAPI.
    The token is forwarded to PAPI which validates it and returns user info.
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
) -> TokenVerifier | MultiAuth:
    """Build the appropriate auth provider based on available configuration.

    If Auth0 credentials are provided, returns a MultiAuth combining
    OAuth (interactive) and PAT verification (programmatic).

    If only PAPI URL is provided, returns a PAT-only verifier.

    Configuration is read from parameters or environment variables:
      - RW_API_URL: PAPI base URL (required)
      - MCP_AUTH0_CONFIG_URL: Auth0 OIDC config URL (optional)
      - MCP_AUTH0_CLIENT_ID: Auth0 OAuth app client ID (optional)
      - MCP_AUTH0_CLIENT_SECRET: Auth0 OAuth app client secret (optional)
      - MCP_AUTH0_AUDIENCE: Auth0 API audience identifier (optional)
      - MCP_BASE_URL: Public URL of this MCP server (required for OAuth)
    """
    papi = papi_url or os.environ.get("RW_API_URL", "")
    if not papi:
        raise ValueError(
            "RW_API_URL is required for remote MCP server authentication. "
            "Set it to your PAPI base URL (e.g. https://papi.beta.runwhen.com)."
        )

    pat_verifier = PAPITokenVerifier(papi_url=papi)

    config_url = auth0_config_url or os.environ.get("MCP_AUTH0_CONFIG_URL", "")
    client_id = auth0_client_id or os.environ.get("MCP_AUTH0_CLIENT_ID", "")
    client_secret = auth0_client_secret or os.environ.get("MCP_AUTH0_CLIENT_SECRET", "")
    audience = auth0_audience or os.environ.get("MCP_AUTH0_AUDIENCE", "")
    server_url = base_url or os.environ.get("MCP_BASE_URL", "")

    if config_url and client_id and client_secret and server_url:
        from fastmcp.server.auth.providers.auth0 import Auth0Provider

        auth0_provider = Auth0Provider(
            config_url=config_url,
            client_id=client_id,
            client_secret=client_secret,
            audience=audience or client_id,
            base_url=server_url,
        )
        return MultiAuth(
            server=auth0_provider,
            verifiers=[pat_verifier],
        )

    return pat_verifier
