"""Async WarcraftLogs v2 GraphQL client (client-credentials flow)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"
DEFAULT_TOKEN_CACHE_DIR = Path.home() / ".cache" / "wcl_bot"
DEFAULT_QUERY_CACHE_DIR = Path.home() / ".cache" / "wcl_bot" / "queries"

# Suggested TTLs for callers — exposed so each query site documents its choice.
CACHE_TTL_LEADERBOARD = 6 * 3600       # fightRankings: updates as guilds submit
CACHE_TTL_STATIC = 30 * 86400          # report data (playerDetails, events) is immutable
CACHE_TTL_GAME_DATA = 30 * 86400       # ability/spell metadata, stable between patches

log = logging.getLogger(__name__)


class WCLError(Exception):
    pass


class WCLAuthError(WCLError):
    pass


class WCLGraphQLError(WCLError):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__(str(errors))


class WCLSchemaError(WCLError):
    """Raised when a JSON-scalar response (e.g. fightRankings, playerDetails)
    has an unexpected shape — usually means WCL changed key names."""


@dataclass
class _Token:
    access_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        # Refresh a minute early to avoid edge-of-expiry 401s.
        return time.time() >= self.expires_at - 60


def _token_cache_path(client_id: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(client_id.encode()).hexdigest()[:16]
    return cache_dir / f"token-{digest}.json"


def _load_cached_token(path: Path) -> _Token | None:
    try:
        data = json.loads(path.read_text())
        token = _Token(
            access_token=str(data["access_token"]),
            expires_at=float(data["expires_at"]),
        )
    except (FileNotFoundError, KeyError, ValueError, OSError):
        return None
    return None if token.expired else token


def _save_cached_token(path: Path, token: _Token) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"access_token": token.access_token, "expires_at": token.expires_at}
            )
        )
        path.chmod(0o600)
    except OSError as exc:
        log.warning("Could not write token cache at %s: %s", path, exc)


def _query_cache_key(query: str, variables: dict[str, Any] | None) -> str:
    """SHA-256 over canonical (query + sorted-key variables JSON)."""
    blob = json.dumps(
        {"q": query, "v": variables or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _query_cache_path(key: str, cache_dir: Path) -> Path:
    return cache_dir / f"{key}.json"


def _load_cached_query(path: Path, ttl_seconds: int) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text())
        cached_at = float(raw["cached_at"])
        if time.time() - cached_at > ttl_seconds:
            return None
        return raw["response"]
    except (FileNotFoundError, KeyError, ValueError, OSError):
        return None


def _save_cached_query(path: Path, response: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cached_at": time.time(), "response": response})
        )
    except OSError as exc:
        log.warning("Could not write query cache at %s: %s", path, exc)


class WCLClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        timeout: float = 30.0,
        token_cache_dir: Path | str | None = DEFAULT_TOKEN_CACHE_DIR,
        query_cache_dir: Path | str | None = DEFAULT_QUERY_CACHE_DIR,
        cache_disabled: bool = False,
    ) -> None:
        cid = client_id or os.environ.get("WCL_CLIENT_ID")
        secret = client_secret or os.environ.get("WCL_CLIENT_SECRET")
        if not cid or not secret:
            raise WCLAuthError(
                "WCL_CLIENT_ID and WCL_CLIENT_SECRET must be set "
                "(env vars or constructor args)."
            )
        self._client_id = cid
        self._client_secret = secret
        self._http = httpx.AsyncClient(timeout=timeout)
        # Persistent token cache survives process restarts. WCL's OAuth tokens
        # last ~1 year; without this, every script run hits /oauth/token which
        # has its own (aggressive) rate limit separate from the GraphQL API.
        self._token_cache_path: Path | None = (
            _token_cache_path(self._client_id, Path(token_cache_dir))
            if token_cache_dir is not None
            else None
        )
        self._token: _Token | None = (
            _load_cached_token(self._token_cache_path)
            if self._token_cache_path
            else None
        )
        self._query_cache_dir: Path | None = (
            Path(query_cache_dir) if query_cache_dir is not None else None
        )
        self._cache_disabled = cache_disabled
        # Cheap counters for diagnostics; reset per WCLClient instance.
        self.cache_hits = 0
        self.cache_misses = 0

    async def __aenter__(self) -> WCLClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _get_token(self) -> str:
        if self._token and not self._token.expired:
            return self._token.access_token
        resp = await self._http.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        if resp.status_code == 429:
            raise WCLAuthError(
                "WCL OAuth token endpoint is rate-limiting us (HTTP 429). "
                "Wait a few minutes before retrying. Tokens are now cached to "
                f"{self._token_cache_path}, so this won't happen on subsequent "
                "runs once a token is obtained."
            )
        if resp.status_code != 200:
            # Truncate body so we don't dump a full HTML error page into the log.
            body = resp.text[:200].replace("\n", " ")
            raise WCLAuthError(
                f"Token request failed: HTTP {resp.status_code} — {body}"
            )
        data = resp.json()
        self._token = _Token(
            access_token=data["access_token"],
            expires_at=time.time() + data["expires_in"],
        )
        if self._token_cache_path is not None:
            _save_cached_token(self._token_cache_path, self._token)
        return self._token.access_token

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        cache_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query and return the `data` payload.

        If `cache_ttl_seconds` is set and the client wasn't constructed with
        `cache_disabled=True`, the result is read from / written to a disk
        cache keyed by (query, variables). Only successful responses are
        cached — GraphQL errors and HTTP failures always re-execute.

        Raises WCLGraphQLError if the response contains GraphQL errors.
        """
        effective_ttl = (
            None if self._cache_disabled else cache_ttl_seconds
        )
        cache_path: Path | None = None
        if effective_ttl is not None and self._query_cache_dir is not None:
            key = _query_cache_key(query, variables)
            cache_path = _query_cache_path(key, self._query_cache_dir)
            cached = _load_cached_query(cache_path, effective_ttl)
            if cached is not None:
                self.cache_hits += 1
                log.debug("Cache hit: %s", key)
                return cached
            self.cache_misses += 1
            log.debug("Cache miss: %s", key)

        payload = await self._post_graphql(query, variables)
        # Single retry on 401 in case the cached token was revoked server-side.
        if payload is None:
            self._token = None
            payload = await self._post_graphql(query, variables)
            if payload is None:
                raise WCLAuthError("Authentication failed after token refresh.")
        if payload.get("errors"):
            raise WCLGraphQLError(payload["errors"])
        data = payload["data"]
        if cache_path is not None:
            _save_cached_query(cache_path, data)
        return data

    async def _post_graphql(
        self,
        query: str,
        variables: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        token = await self._get_token()
        resp = await self._http.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            return None
        resp.raise_for_status()
        return resp.json()
