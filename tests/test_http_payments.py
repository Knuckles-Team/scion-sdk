"""Typed 402 payment models and budget decisions."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from scion_sdk.http.client import build_http_client
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.payments import (
    PaymentAuthorization,
    PaymentBudget,
    PaymentChallenge,
    PaymentDecision,
    PaymentGate,
    PaymentRecord,
    PaymentRefused,
)
from scion_sdk.http.retry import RetryPolicy


def _challenge(amount: str = "1.00") -> PaymentChallenge:
    return PaymentChallenge(
        amount=Decimal(amount),
        currency="USDC",
        payee="0xpayee",
        request_url="https://api.example/data",
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049.1")
def test_models_are_frozen_and_typed() -> None:
    challenge = _challenge()
    with pytest.raises(ValidationError):
        challenge.amount = Decimal(2)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PaymentChallenge(amount=Decimal(0), currency="USDC", payee="p", request_url="u")
    with pytest.raises(ValidationError):
        PaymentBudget(per_call_limit=Decimal(-1))
    with pytest.raises(ValidationError):
        PaymentBudget(extra=1)  # type: ignore[call-arg]
    record = PaymentRecord(
        transaction_id="t1",
        amount=Decimal(1),
        currency="USDC",
        payee="p",
        request_url="u",
        approver="alice",
        outcome="paid",
    )
    assert set(type(record).model_fields) == {
        "transaction_id",
        "amount",
        "currency",
        "payee",
        "request_url",
        "approver",
        "outcome",
    }
    with pytest.raises(ValidationError):
        record.outcome = "x"  # type: ignore[misc]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049.2")
def test_decide_applies_named_limits() -> None:
    gate = PaymentGate()
    assert gate.decide(_challenge(), PaymentBudget()).allowed is False
    budget = PaymentBudget(
        per_call_limit=Decimal(5), per_day_limit=Decimal(8), spent_today=Decimal(0)
    )
    assert gate.decide(_challenge("5"), budget).allowed is True
    over_call = gate.decide(_challenge("5.01"), budget)
    assert (over_call.allowed, over_call.limit) == (False, "per_call")
    used = budget.model_copy(update={"spent_today": Decimal(4)})
    over_day = gate.decide(_challenge("5"), used)
    assert (over_day.allowed, over_day.limit) == (False, "per_day")
    assert gate.decide(_challenge("4"), used) == PaymentDecision(
        allowed=True, limit="per_day"
    )


def _auth(
    records: list[PaymentRecord],
    *,
    per_call: str = "5",
    approve: object = True,
    scheme: str = "x402",
) -> PaymentAuthorization:
    return PaymentAuthorization(
        pay=lambda challenge: f"proof-{challenge.amount}",
        record=records.append,
        budget=lambda: PaymentBudget(
            per_call_limit=Decimal(per_call), per_day_limit=Decimal(10)
        ),
        approve=(lambda _c: approve) if approve is not None else None,  # type: ignore[arg-type]
        approver="alice",
    )


def _fake(seen: list[httpx.Request], scheme: str = "x402") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "X-PAYMENT" in request.headers:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            402,
            json={"amount": "2", "currency": "USDC", "payee": "0xp", "scheme": scheme},
        )

    return httpx.MockTransport(handler)


def _client(
    auth: PaymentAuthorization, seen: list[httpx.Request], scheme: str = "x402"
):
    options = HttpClientOptions(
        base_url="http://127.0.0.1:9",
        retry=RetryPolicy(max_attempts=1),
        payment=auth,
    )
    return build_http_client(options, transport=_fake(seen, scheme))


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049.3")
def test_client_hook_gates_402_through_approval() -> None:
    records: list[PaymentRecord] = []
    seen: list[httpx.Request] = []
    reply = _client(_auth(records), seen).get("/data")
    assert reply.status_code == 200
    assert seen[-1].headers["X-PAYMENT"] == "proof-2"
    assert [r.outcome for r in records] == ["paid"]
    plain = build_http_client(
        HttpClientOptions(
            base_url="http://127.0.0.1:9", retry=RetryPolicy(max_attempts=1)
        ),
        transport=_fake([]),
    )
    assert plain.get("/data").status_code == 402


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049.4")
@pytest.mark.parametrize(
    ("kwargs", "scheme", "reason"),
    [
        ({"per_call": "1"}, "x402", "over_budget"),
        ({"approve": False}, "x402", "not_approved"),
        ({"approve": None}, "x402", "no_context"),
        ({}, "other", "unsupported_scheme"),
    ],
)
def test_refusals_send_no_payment_header(
    kwargs: dict[str, object], scheme: str, reason: str
) -> None:
    records: list[PaymentRecord] = []
    seen: list[httpx.Request] = []
    with pytest.raises(PaymentRefused) as caught:
        _client(_auth(records, **kwargs), seen, scheme).get("/data")  # type: ignore[arg-type]
    assert caught.value.reason == reason
    assert all("X-PAYMENT" not in r.headers for r in seen) and len(seen) == 1
    assert records == [caught.value.record]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049.5")
def test_every_attempt_emits_a_record() -> None:
    records: list[PaymentRecord] = []
    _client(_auth(records), []).get("/a")
    with pytest.raises(PaymentRefused):
        _client(_auth(records, per_call="1"), []).get("/b")
    assert [r.outcome for r in records] == ["paid", "refused:over_budget"]
    assert all(r.approver == "alice" and r.transaction_id for r in records)


def test_transports_pay_directly_and_sync_refuses_an_async_approver() -> None:
    from scion_sdk.http.payments import AsyncPaymentTransport, PaymentTransport

    records: list[PaymentRecord] = []

    async def approve(_c: object) -> bool:
        return True

    auth = PaymentAuthorization(
        pay=lambda c: "p",
        record=records.append,
        budget=lambda: PaymentBudget(
            per_call_limit=Decimal(5), per_day_limit=Decimal(5)
        ),
        approve=approve,
    )
    sync = PaymentTransport(_fake([]), auth)
    with pytest.raises(TypeError):
        sync.handle_request(httpx.Request("GET", "http://127.0.0.1:9/x"))
    assert isinstance(
        AsyncPaymentTransport(httpx.MockTransport(_fake([]).handler), auth),
        httpx.AsyncBaseTransport,
    )  # type: ignore[attr-defined]
