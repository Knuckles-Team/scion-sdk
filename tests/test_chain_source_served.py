"""A registered chain-trade tool reaches a JSON-RPC node and the real engine (R005.15, R005.18)."""

import asyncio
import json

import pytest
from fastmcp import FastMCP
from live_engine import live_ingest, scoped_client  # noqa: F401
from source_graph_helpers import (
    import_source_mapping,
    loopback_options,
    sync_failure_cycle,
)
from test_contracts_and_sink import _catalog
from test_evm_chain_models import LAYOUT, _log, descriptor

from scion_sdk.adapters.event_errors import ChainLogRefused
from scion_sdk.adapters.evm_chain import read_trade_logs
from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.http.client import build_http_client
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.sinks.medulla import MedullaSink
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse

CONNECTOR = "chain-demo"
_JSON = {"Content-Type": "application/json"}
BLOCKS_PER_PAGE = 5


def _reply(*logs):
    return ScriptedResponse(
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "result": list(logs)}).encode(),
        headers=_JSON,
    )


def _register_chain_trades(server, url, bounds):
    """The connector's ``chain_trades`` tool: one bounded block range per call."""

    @server.tool()
    async def chain_trades(
        cursor: str | None = None, mode: str = "delta", state: str | None = None
    ) -> dict[str, object]:
        resumed = int(state.removeprefix("block-")) + 1 if state else 0
        first = int(cursor) if cursor else resumed
        last = first + BLOCKS_PER_PAGE - 1
        desc = descriptor(layout=LAYOUT, first_block=first, last_block=last)

        async def rpc(method, params):
            async with build_http_client(loopback_options(url), async_=True) as http:
                response = await http.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params,
                    },
                )
            bounds.append((params[0]["fromBlock"], params[0]["toBlock"]))
            return response.json()["result"]

        trades, rejected = await read_trade_logs(rpc, desc)
        if rejected:
            reasons = ",".join(r.reason for r in rejected)
            raise ChainLogRefused("LOG_UNDECODABLE", f"page not advanced: {reasons}")
        return {
            "records": [
                {
                    "id": f"{t.transaction_hash}:{t.log_index}",
                    "label": t.side,
                    "amount_in": str(t.amount_in),
                    "amount_out": str(t.amount_out),
                    "scale_in": t.scale_in,
                    "block_hash": t.block_hash,
                    "block_number": t.block_number,
                }
                for t in trades
            ],
            "next": str(last + 1) if first == 0 else None,
            "checkpoint": f"block-{last}",
        }


async def _exercise(url):
    bounds: list[tuple[str, str]] = []
    server = FastMCP("chain")
    _register_chain_trades(server, url, bounds)
    engine = await scoped_client(["*"])
    try:
        context = await import_source_mapping(
            engine,
            CONNECTOR,
            {
                "Trade": {
                    "fields": {"label": "xsd:string"},
                    "ontology_class": "Document",
                }
            },
        )

        async def authority(_connector):
            return _catalog(), context

        preset = ToolPreset(
            name="chain_trades",
            server=CONNECTOR,
            tool="chain_trades",
            id_field="id",
            records_path="records",
            params_style="args",
            params={"mode": "delta"},
            pagination="cursor",
            cursor_param="cursor",
            cursor_path="next",
            updated_since_param="state",
            checkpoint_path="checkpoint",
        )

        def adapter():
            return McpToolSourceAdapter(
                preset,
                connector=CONNECTOR,
                mapping_reference=f"connector:{CONNECTOR}#mappings/Trade",
            )

        cycle = await sync_failure_cycle(
            server,
            MedullaSink(engine, authority),
            CONNECTOR,
            "chain_trades",
            "chain_trades",
            adapter,
        )
        rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        return cycle, rows, bounds
    finally:
        await engine.close()


def _run(live_ingest, *replies):  # noqa: F811
    with ScriptedHttpServer(*replies) as node:
        return asyncio.run_coroutine_threadsafe(
            _exercise(node.base_url), live_ingest._loop
        ).result(60)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.15")
def test_registered_tool_reaches_rpc_and_ingestion(live_ingest):  # noqa: F811
    cycle, rows, bounds = _run(
        live_ingest,
        _reply(_log(1, 2), _log(2, 3)),
        _reply(_log(9, 7, data="0x12")),
        _reply(_log(3, 6)),
        _reply(_log(3, 6)),
    )
    first, _before, _failed, second, _ = cycle
    assert first.accepted == 2 and second.accepted == 1
    trades = [n["n"] for n in rows if n["n"].get("source_stream") == "chain_trades"]
    assert len(trades) == 3, trades
    assert bounds[0] == ("0x0", "0x4")


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.18")
def test_rpc_decode_and_refuse(live_ingest):  # noqa: F811
    cycle, rows, bounds = _run(
        live_ingest,
        _reply(_log(1, 2), _log(2, 3)),
        _reply(_log(9, 7, data="0x12")),
        _reply(_log(3, 6)),
        _reply(_log(3, 6)),
    )
    first, before, failed, second, _ = cycle
    assert before.accepted_checkpoint == failed.accepted_checkpoint  # refused advance
    assert first.accepted == 2 and second.accepted == 1
    assert bounds == [("0x0", "0x4"), ("0x5", "0x9"), ("0x5", "0x9")]
    ids = {
        n["n"].get("source_record_id") or n["n"].get("id")
        for n in rows
        if n["n"].get("source_stream") == "chain_trades"
    }
    assert len(ids) == 3
