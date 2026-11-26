"""A local issuer, challenge transport and broker for PKCE ceremonies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from scion_sdk.auth.pkce import (
    PkceAuthConfig,
    PkceAuthManager,
    PkceChallenge,
    ProviderTokenProfile,
    s256_challenge,
)

CODE = "code-zz9-private"  # sanitizer:ignore - synthetic fixture value
TOKEN = "tok-zz9-private"  # sanitizer:ignore - synthetic fixture value


class Issuer:
    """Checks the S256 verifier at exchange; records every exchange."""

    def __init__(self, access: str = TOKEN, active: bool = True) -> None:
        self.access, self.active = access, active
        self.challenges: dict[str, str] = {}
        self.exchanges = 0
        self.fail = False

    async def exchange(
        self, *, code: str, verifier: str, redirect_uri: str, client_id: str
    ) -> Mapping[str, Any]:
        self.exchanges += 1
        if self.fail:
            raise ConnectionError(CODE)
        assert code == CODE and s256_challenge(verifier) in self.challenges.values()
        assert len(verifier) == 64
        return {"access_token": self.access}

    async def introspect(self, token: str) -> bool:
        return self.active


class Transport:
    """Plays the browser: answers with the bound redirect response."""

    def __init__(self, issuer: Issuer, now: dict[str, int] | None = None) -> None:
        self.issuer = issuer
        self.callback = "http://127.0.0.1:8765/callback"
        self.state_override: str | None = None
        self.silent = False
        self.launch_fails = False
        self.now = now
        self.advance_ms = 0
        self.timeouts: list[float] = []
        self._state = ""

    def callback_uri(self) -> str:
        return self.callback

    async def deliver(self, challenge: PkceChallenge, authorization_url: str) -> None:
        if self.launch_fails:
            raise OSError("no browser")
        query = parse_qs(urlsplit(authorization_url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == [self.callback]
        self.issuer.challenges[challenge.challenge_ref] = query["code_challenge"][0]
        self._state = query["state"][0]

    async def receive(self, challenge: PkceChallenge, timeout_s: float) -> str | None:
        self.timeouts.append(timeout_s)
        if self.now is not None:
            self.now["ms"] += self.advance_ms
        if self.silent:
            return None
        state = self.state_override or self._state
        return f"{self.callback}?code={CODE}&state={state}"


class Broker:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.persisted: list[str] = []
        self.fail = False
        self.no_receipt = False
        self.deny_claim = False

    async def claim(self, challenge_ref: str) -> bool:
        if self.deny_claim:
            return False
        if challenge_ref in self.claimed:
            return False
        self.claimed.add(challenge_ref)
        return True

    async def persist(
        self, challenge: PkceChallenge, token: Mapping[str, Any], token_ref: str
    ) -> str | None:
        if self.fail:
            raise RuntimeError(TOKEN)
        if self.no_receipt:
            return None
        self.persisted.append(token_ref)
        return f"receipt:{challenge.challenge_ref}"


def make_manager(
    *,
    profile: ProviderTokenProfile | None = None,
    issuer: Issuer | None = None,
    now: dict[str, int] | None = None,
    **config: Any,
) -> tuple[PkceAuthManager, Issuer, Transport, Broker]:
    issuer = issuer or Issuer()
    transport = Transport(issuer, now)
    broker = Broker()
    clock = now if now is not None else {"ms": 1_000_000}
    transport.now = clock
    cfg = PkceAuthConfig(
        issuer="https://issuer.example.test",
        client_id="client-a",
        token_ref="vault:tok/1",
        token_profile=profile or ProviderTokenProfile(),
        **config,
    )
    manager = PkceAuthManager(
        cfg,
        endpoint=issuer,
        transport=transport,
        broker=broker,
        clock_ms=lambda: clock["ms"],
    )
    return manager, issuer, transport, broker
