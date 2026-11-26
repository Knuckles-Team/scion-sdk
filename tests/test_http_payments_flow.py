"""A served operation pays within budget and refuses over budget (R049.6)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from fastmcp import Client, FastMCP

from scion_sdk.http.client import build_http_client
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.payments import (
    PaymentAuthorization,
    PaymentBudget,
    PaymentRecord,
    PaymentRefused,
)
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.mcp.confirmation import ctx_payment_approver


def _endpoint(request: httpx.Request) -> httpx.Response:
    if "X-PAYMENT" in request.headers:
        return httpx.Response(200, json={"rows": 3})
    amount = request.url.params.get("price", "1")
    return httpx.Response(
        402, json={"amount": amount, "currency": "USDC", "payee": "0xp"}
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R049.6")
async def test_served_operation_pays_within_budget_and_refuses_over() -> None:
    records: list[PaymentRecord] = []
    server = FastMCP("pay")

    @server.tool
    async def fetch(price: str, ctx: object = None) -> str:
        auth = PaymentAuthorization(
            pay=lambda c: "proof",
            record=records.append,
            budget=lambda: PaymentBudget(
                per_call_limit=Decimal(3), per_day_limit=Decimal(10)
            ),
            approve=lambda _c: True,
            approver="caller",
        )
        client = build_http_client(
            HttpClientOptions(
                base_url="http://127.0.0.1:9",
                retry=RetryPolicy(max_attempts=1),
                payment=auth,
            ),
            async_=True,
            transport=httpx.MockTransport(_endpoint),
        )
        try:
            reply = await client.get("/d", params={"price": price})
        except PaymentRefused as refusal:
            return f"refused:{refusal.reason}"
        return str(reply.status_code)

    async with Client(server) as client:
        paid = await client.call_tool("fetch", {"price": "2"})
        over = await client.call_tool("fetch", {"price": "4"})
    assert paid.data == "200" and over.data == "refused:over_budget"
    assert [r.outcome for r in records] == ["paid", "refused:over_budget"]


def test_ctx_payment_approver_is_exported() -> None:
    assert callable(ctx_payment_approver)
