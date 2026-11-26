"""Typed 402 payment models and the budget decision for the governed HTTP client.

Authorization only: nothing here sends a payment header. The X402 proof and
signing helpers stay in :mod:`scion_sdk.payments`.
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AsyncPaymentTransport",
    "PaymentAuthorization",
    "PaymentBudget",
    "PaymentChallenge",
    "PaymentDecision",
    "PaymentGate",
    "PaymentRecord",
    "PaymentRefused",
    "PaymentTransport",
]

LimitName = Literal["per_call", "per_day"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaymentChallenge(_Frozen):
    """An HTTP 402 payment demand."""

    amount: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(min_length=1)
    payee: str = Field(min_length=1)
    request_url: str = Field(min_length=1)
    scheme: str = Field(default="x402", min_length=1)


class PaymentBudget(_Frozen):
    """Limits a caller grants. Both default to zero, so nothing is paid."""

    per_call_limit: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    per_day_limit: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    spent_today: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)

    @property
    def remaining_today(self) -> Decimal:
        """Day allowance left; never negative."""
        return max(self.per_day_limit - self.spent_today, Decimal(0))


class PaymentRecord(_Frozen):
    """Provenance of one payment attempt, paid or refused."""

    transaction_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(min_length=1)
    payee: str = Field(min_length=1)
    request_url: str = Field(min_length=1)
    approver: str | None = None
    outcome: str = Field(min_length=1)


class PaymentDecision(_Frozen):
    """Budget verdict. ``limit`` names the limit that decided it."""

    allowed: bool
    limit: LimitName


class PaymentGate:
    """Decides whether a challenge fits the remaining budget."""

    def decide(
        self, challenge: PaymentChallenge, budget: PaymentBudget
    ) -> PaymentDecision:
        """Allow only when both the per-call and the remaining daily limit fit."""
        if challenge.amount > budget.per_call_limit:
            return PaymentDecision(allowed=False, limit="per_call")
        if challenge.amount > budget.remaining_today:
            return PaymentDecision(allowed=False, limit="per_day")
        return PaymentDecision(allowed=True, limit="per_day")


PaymentRefusal = Literal[
    "over_budget", "not_approved", "no_context", "unsupported_scheme"
]
_SUPPORTED_SCHEME = "x402"
_PAYMENT_HEADER = "X-PAYMENT"


class PaymentRefused(RuntimeError):
    """A 402 challenge was not paid; no payment header was sent."""

    def __init__(self, reason: PaymentRefusal, record: PaymentRecord | None) -> None:
        self.reason = reason
        self.record = record
        super().__init__(f"payment refused: {reason}")


@dataclass(frozen=True)
class PaymentAuthorization:
    """What a caller grants a client for 402 challenges.

    ``budget`` reads the caller's current grant (the SDK holds no spend state;
    the default grant is zero, so nothing is paid). ``approve`` asks the caller
    to approve one payment and may be asynchronous; ``None`` means there is no
    approval context. ``pay`` builds the payment header value. ``record`` receives
    one :class:`PaymentRecord` per attempt, paid or refused.
    """

    pay: Callable[[PaymentChallenge], str]
    record: Callable[[PaymentRecord], None]
    budget: Callable[[], PaymentBudget] = PaymentBudget
    approve: Callable[[PaymentChallenge], bool | Awaitable[bool]] | None = None
    approver: str | None = None
    gate: PaymentGate = field(default_factory=PaymentGate)


def _challenge(
    request: httpx.Request, response: httpx.Response
) -> PaymentChallenge | None:
    try:
        body: Any = json.loads(response.content)
        return PaymentChallenge(
            amount=Decimal(str(body["amount"])),
            currency=body["currency"],
            payee=body["payee"],
            scheme=body.get("scheme", _SUPPORTED_SCHEME),
            request_url=str(request.url),
        )
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None


def _attempt(
    auth: PaymentAuthorization, challenge: PaymentChallenge, outcome: str
) -> PaymentRecord:
    record = PaymentRecord(
        transaction_id=uuid.uuid4().hex,
        amount=challenge.amount,
        currency=challenge.currency,
        payee=challenge.payee,
        request_url=challenge.request_url,
        approver=auth.approver,
        outcome=outcome,
    )
    auth.record(record)
    return record


def _refuse(
    auth: PaymentAuthorization, challenge: PaymentChallenge, reason: PaymentRefusal
) -> PaymentRefused:
    return PaymentRefused(reason, _attempt(auth, challenge, f"refused:{reason}"))


def _precheck(
    auth: PaymentAuthorization, request: httpx.Request, response: httpx.Response
) -> PaymentChallenge | None:
    """The challenge to pay, ``None`` for a 402 that is not a challenge."""
    challenge = _challenge(request, response)
    if challenge is None:
        return None
    if challenge.scheme != _SUPPORTED_SCHEME:
        raise _refuse(auth, challenge, "unsupported_scheme")
    if not auth.gate.decide(challenge, auth.budget()).allowed:
        raise _refuse(auth, challenge, "over_budget")
    if auth.approve is None:
        raise _refuse(auth, challenge, "no_context")
    return challenge


def _paid(
    request: httpx.Request, auth: PaymentAuthorization, challenge: PaymentChallenge
) -> httpx.Request:
    headers = httpx.Headers(request.headers)
    headers[_PAYMENT_HEADER] = auth.pay(challenge)
    return httpx.Request(
        request.method, request.url, headers=headers, content=request.content
    )


class PaymentTransport(httpx.BaseTransport):
    """Answers one 402 challenge within budget and after approval."""

    def __init__(self, inner: httpx.BaseTransport, auth: PaymentAuthorization) -> None:
        self._inner = inner
        self._auth = auth

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        if response.status_code != 402:
            return response
        response.read()
        challenge = _precheck(self._auth, request, response)
        if challenge is None:
            return response
        assert self._auth.approve is not None
        approved = self._auth.approve(challenge)
        if inspect.isawaitable(approved):
            raise TypeError("an asynchronous approver needs an asynchronous client")
        if not approved:
            raise _refuse(self._auth, challenge, "not_approved")
        _attempt(self._auth, challenge, "paid")
        return self._inner.handle_request(_paid(request, self._auth, challenge))

    def close(self) -> None:
        self._inner.close()


class AsyncPaymentTransport(httpx.AsyncBaseTransport):
    """The asynchronous form of :class:`PaymentTransport`."""

    def __init__(
        self, inner: httpx.AsyncBaseTransport, auth: PaymentAuthorization
    ) -> None:
        self._inner = inner
        self._auth = auth

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if response.status_code != 402:
            return response
        await response.aread()
        challenge = _precheck(self._auth, request, response)
        if challenge is None:
            return response
        assert self._auth.approve is not None
        approved = self._auth.approve(challenge)
        if inspect.isawaitable(approved):
            approved = await approved
        if not approved:
            raise _refuse(self._auth, challenge, "not_approved")
        _attempt(self._auth, challenge, "paid")
        return await self._inner.handle_async_request(
            _paid(request, self._auth, challenge)
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
