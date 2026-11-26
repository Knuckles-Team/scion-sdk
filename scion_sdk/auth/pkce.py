"""Browser authorization-code flow with PKCE as one reusable auth manager.

SCION-CONNECTOR-CONTROL-R046. :class:`PkceAuthManager` runs one host-admitted
ceremony: a random 64-character verifier, the S256 challenge, a state and a
nonce bound to the authenticated caller, one total deadline, and persistence
only through the injected broker and vault port. Codes, verifiers and tokens
stay private transport material: results and records carry references and
status only. No served request blocks on stdin or a code file; headless input
arrives through the challenge transport, bounded by the remaining deadline.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import httpx2
from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.credentials.binding import CredentialAccessBinding, binding_digest

__all__ = [
    "REFUSAL_CODES",
    "PkceAuthConfig",
    "PkceAuthManager",
    "PkceAuthRefusedError",
    "PkceAuthResult",
    "PkceBearerAuth",
    "PkceBroker",
    "PkceChallenge",
    "PkceChallengeTransport",
    "PkcePorts",
    "PkceRecord",
    "PkceTokenEndpoint",
    "ProviderTokenProfile",
    "s256_challenge",
]

REFUSAL_CODES = frozenset(
    {
        "no_credential",
        "interaction_required",
        "browser_launch_failed",
        "callback_unavailable",
        "input_timeout",
        "state_mismatch",
        "insecure_redirect",
        "provider_unavailable",
        "persistence_failed",
        "cancelled",
        "expired",
        "revoked",
    }
)
_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class PkceAuthRefusedError(PermissionError):
    """A ceremony was refused; ``reason`` is one of :data:`REFUSAL_CODES`."""

    def __init__(self, reason: str) -> None:
        if reason not in REFUSAL_CODES:
            raise ValueError("unknown refusal code")
        super().__init__(f"pkce authorization refused: {reason}")
        self.reason = reason


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderTokenProfile(_Frozen):
    """What each token role is; applied before persistence."""

    access: Literal["jwt", "opaque"] = "opaque"
    refresh: Literal["jwt", "opaque", "none"] = "opaque"
    identity: Literal["oidc", "none"] = "none"
    introspection_required: bool = False


class PkceAuthConfig(_Frozen):
    issuer: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    scope: str = ""
    redirect_mode: Literal["loopback", "headless"] = "loopback"
    token_ref: str = Field(min_length=1)
    token_profile: ProviderTokenProfile = ProviderTokenProfile()
    deadline_s: int = Field(default=120, ge=30, le=600)


class PkceChallenge(_Frozen):
    """One ceremony, bound to the caller, client and operation."""

    binding_digest: str
    client: str
    operation_digest: str
    challenge_ref: str
    state: str
    nonce: str
    callback: str
    deadline_ms: int


class PkceAuthResult(_Frozen):
    """Status and references only; never a code, verifier or token."""

    status: Literal["authenticated"]
    token_ref: str
    receipt_ref: str
    challenge_ref: str


class PkceRecord(_Frozen):
    issuer_profile: str
    redirect_mode: str
    binding_digest: str
    challenge_ref: str
    outcome: str
    deadline_s: int
    token_ref: str


class PkceTokenEndpoint(Protocol):
    async def exchange(
        self, *, code: str, verifier: str, redirect_uri: str, client_id: str
    ) -> Mapping[str, Any]: ...

    async def introspect(self, token: str) -> bool: ...


class PkceChallengeTransport(Protocol):
    """Delivers the authorization URL and receives the bound redirect response."""

    def callback_uri(self) -> str: ...

    async def deliver(self, challenge: PkceChallenge, url: str) -> None: ...

    async def receive(self, challenge: PkceChallenge, timeout_s: float) -> str | None:
        """The complete redirect response, or ``None`` when the input timed out."""
        ...


class PkceBroker(Protocol):
    """Host broker and vault: single-use claim, then persistence with a receipt."""

    async def claim(self, challenge_ref: str) -> bool: ...

    async def persist(
        self, challenge: PkceChallenge, token: Mapping[str, Any], token_ref: str
    ) -> str | None:
        """The persistence receipt reference, or ``None``."""
        ...


def s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _secure(callback: str) -> bool:
    parts = urlsplit(callback)
    return parts.scheme == "https" or (
        parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK
    )


class PkceAuthManager:
    """Runs one authorization-code ceremony with PKCE."""

    def __init__(
        self,
        config: PkceAuthConfig,
        *,
        endpoint: PkceTokenEndpoint,
        transport: PkceChallengeTransport,
        broker: PkceBroker,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._config = config
        self._endpoint = endpoint
        self._transport = transport
        self._broker = broker
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.records: list[PkceRecord] = []

    async def authenticate(
        self,
        binding: CredentialAccessBinding,
        *,
        operation_digest: str,
        interactive: bool,
    ) -> PkceAuthResult:
        """Run the ceremony; every failure is a typed :class:`PkceAuthRefusedError`."""
        started = self._clock_ms()
        deadline = started + self._config.deadline_s * 1000
        challenge = self._challenge(binding, operation_digest, deadline)
        try:
            result = await self._run(challenge, interactive, deadline)
        except PkceAuthRefusedError as refused:
            self._record(challenge, refused.reason, "")
            raise
        self._record(challenge, "persisted", result.receipt_ref)
        return result

    def _challenge(
        self, binding: CredentialAccessBinding, operation_digest: str, deadline: int
    ) -> PkceChallenge:
        return PkceChallenge(
            binding_digest=binding_digest(binding),
            client=binding.client,
            operation_digest=operation_digest,
            challenge_ref=secrets.token_hex(16),
            state=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(24),
            callback="",
            deadline_ms=deadline,
        )

    def _remaining(self, deadline: int) -> float:
        left = (deadline - self._clock_ms()) / 1000
        if left <= 0:
            raise PkceAuthRefusedError("expired")
        return left

    async def _run(
        self, challenge: PkceChallenge, interactive: bool, deadline: int
    ) -> PkceAuthResult:
        if not interactive:
            raise PkceAuthRefusedError("interaction_required")
        callback = self._transport.callback_uri()
        if not _secure(callback):
            raise PkceAuthRefusedError("insecure_redirect")
        challenge = challenge.model_copy(update={"callback": callback})
        if not await self._broker.claim(challenge.challenge_ref):
            raise PkceAuthRefusedError("expired")
        verifier = "".join(secrets.choice(_UNRESERVED) for _ in range(64))
        url = self._authorization_url(challenge, verifier)
        self._remaining(deadline)
        try:
            await self._transport.deliver(challenge, url)
        except Exception:
            raise PkceAuthRefusedError("browser_launch_failed") from None
        code = await self._receive_code(challenge, deadline)
        token = await self._exchange(code, verifier, challenge, deadline)
        await self._apply_profile(token)
        self._remaining(deadline)
        try:
            receipt = await self._broker.persist(
                challenge, token, self._config.token_ref
            )
        except Exception:
            raise PkceAuthRefusedError("persistence_failed") from None
        if not receipt:
            raise PkceAuthRefusedError("persistence_failed")
        return PkceAuthResult(
            status="authenticated",
            token_ref=self._config.token_ref,
            receipt_ref=receipt,
            challenge_ref=challenge.challenge_ref,
        )

    def _authorization_url(self, challenge: PkceChallenge, verifier: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._config.client_id,
                "redirect_uri": challenge.callback,
                "scope": self._config.scope,
                "state": challenge.state,
                "nonce": challenge.nonce,
                "code_challenge": s256_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{self._config.issuer.rstrip('/')}/authorize?{query}"

    async def _receive_code(self, challenge: PkceChallenge, deadline: int) -> str:
        timeout = self._remaining(deadline)
        try:
            response = await self._transport.receive(challenge, timeout)
        except Exception:
            raise PkceAuthRefusedError("callback_unavailable") from None
        if response is None:
            raise PkceAuthRefusedError("input_timeout")
        self._remaining(deadline)
        query = parse_qs(urlsplit(response).query or response)
        states = query.get("state", [])
        if len(states) != 1 or not hmac.compare_digest(states[0], challenge.state):
            raise PkceAuthRefusedError("state_mismatch")
        if "error" in query:
            raise PkceAuthRefusedError("cancelled")
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0]:
            raise PkceAuthRefusedError("no_credential")
        return codes[0]

    async def _exchange(
        self, code: str, verifier: str, challenge: PkceChallenge, deadline: int
    ) -> Mapping[str, Any]:
        self._remaining(deadline)
        try:
            return await self._endpoint.exchange(
                code=code,
                verifier=verifier,
                redirect_uri=challenge.callback,
                client_id=self._config.client_id,
            )
        except Exception:
            raise PkceAuthRefusedError("provider_unavailable") from None

    async def _apply_profile(self, token: Mapping[str, Any]) -> None:
        access = token.get("access_token")
        if not isinstance(access, str) or not access:
            raise PkceAuthRefusedError("provider_unavailable")
        profile = self._config.token_profile
        if profile.access == "jwt" and access.count(".") != 2:
            raise PkceAuthRefusedError("provider_unavailable")
        if profile.introspection_required:
            try:
                active = await self._endpoint.introspect(access)
            except Exception:
                raise PkceAuthRefusedError("provider_unavailable") from None
            if not active:
                raise PkceAuthRefusedError("revoked")

    def _record(self, challenge: PkceChallenge, outcome: str, receipt: str) -> None:
        profile = self._config.token_profile
        self.records.append(
            PkceRecord(
                issuer_profile=f"{profile.access}/{profile.identity}",
                redirect_mode=self._config.redirect_mode,
                binding_digest=challenge.binding_digest,
                challenge_ref=challenge.challenge_ref,
                outcome=outcome,
                deadline_s=self._config.deadline_s,
                token_ref=self._config.token_ref if receipt else "",
            )
        )


@dataclass(frozen=True)
class PkcePorts:
    """The host-supplied pieces of one ceremony: manager, caller binding, token read."""

    manager: PkceAuthManager
    binding: CredentialAccessBinding
    operation_digest: str
    load_token: Callable[[], Awaitable[str | None]]
    interactive: bool = True


class PkceBearerAuth(httpx.Auth, httpx2.Auth):
    """Bearer auth whose token comes from a PKCE ceremony when none is held."""

    def __init__(
        self,
        load_token: Callable[[], Awaitable[str | None]],
        authenticate: Callable[[], Awaitable[None]],
    ) -> None:
        self._load = load_token
        self._authenticate = authenticate

    @classmethod
    def from_ports(cls, ports: PkcePorts) -> PkceBearerAuth:
        async def authenticate() -> None:
            await ports.manager.authenticate(
                ports.binding,
                operation_digest=ports.operation_digest,
                interactive=ports.interactive,
            )

        return cls(ports.load_token, authenticate)

    def sync_auth_flow(self, request: Any) -> Any:
        """A ceremony is asynchronous; a synchronous request refuses typed."""
        raise PkceAuthRefusedError("interaction_required")

    def auth_flow(self, request: Any) -> Any:
        return self.sync_auth_flow(request)

    async def async_auth_flow(self, request: Any) -> Any:
        token = await self._load()
        if token is None:
            await self._authenticate()
            token = await self._load()
        if token is None:
            raise PkceAuthRefusedError("no_credential")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request
