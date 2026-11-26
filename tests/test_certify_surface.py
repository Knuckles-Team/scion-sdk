"""The fingerprint, listing and certification surface that reads a live server."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from fastmcp import FastMCP

from scion_sdk.certify.certification import CertificationReport, _exact_names
from scion_sdk.certify.checkout import ConnectorCheckout
from scion_sdk.certify.cli import (
    PLACEHOLDER_VALUE,
    build_endpoint,
    parse_arguments,
)
from scion_sdk.certify.cli import main as certify_main
from scion_sdk.certify.evidence import (
    EvidenceRefused,
    read_conformance_evidence,
    submit_certification_evidence,
)
from scion_sdk.certify.listing import (
    ToolListing,
    ToolListingError,
    list_server_tools,
)
from scion_sdk.certify.verdicts import ToolStatus, ToolVerdict
from scion_sdk.manifest.fingerprints import (
    EmptyToolSchemaError,
    is_empty_schema_pin,
    output_schema_digest,
    tool_fingerprint,
)
from scion_sdk.manifest.live_contract import (
    LiveToolContract,
    validate_live_tool_contract,
)
from scion_sdk.manifest.model import ResourceRelation
from scion_sdk.manifest.tool_schema import (
    COMPATIBILITY_FINGERPRINT_ALGORITHM,
    ToolSchemaContractError,
    canonical_input_schema,
    canonical_output_schema,
    compatibility_fingerprint,
    read_field,
    schema_fingerprint,
)
from scion_sdk.signing import canonical
from scion_sdk.transports.mcp import McpTransport

TOOL = SimpleNamespace(
    name="t",
    inputSchema={"type": "object", "properties": {"a": {"type": "string"}}},
    outputSchema=None,
)


def test_fingerprints_are_deterministic_and_refuse_empty_schemas() -> None:
    first = tool_fingerprint(TOOL)
    assert first == tool_fingerprint(TOOL)
    assert first == compatibility_fingerprint(
        "t", canonical_input_schema(TOOL, include_presentation=False), None
    )
    assert first != schema_fingerprint("t", {"type": "object"})
    assert canonical_output_schema(TOOL, include_presentation=False) in (None, {})
    assert len(output_schema_digest(TOOL)) == 64
    assert read_field(TOOL, "name") == "t"
    assert COMPATIBILITY_FINGERPRINT_ALGORITHM
    empty = SimpleNamespace(name="e", inputSchema={})
    with pytest.raises(EmptyToolSchemaError):
        tool_fingerprint(empty)
    assert is_empty_schema_pin("e", compatibility_fingerprint("e", {}))
    assert COMPATIBILITY_FINGERPRINT_ALGORITHM == "scion-sdk:mcp-tool-contract-compat"
    with pytest.raises(ToolSchemaContractError):
        tool_fingerprint(SimpleNamespace(name="", inputSchema={"type": "object"}))


def test_live_contract_carries_both_fingerprints() -> None:
    contract = validate_live_tool_contract([TOOL], tool_name="t")
    assert isinstance(contract, LiveToolContract)
    assert contract.compatibility_sha256 == tool_fingerprint(TOOL)


def test_listing_reads_tools_and_declaration() -> None:
    mcp = FastMCP("listed")

    @mcp.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    class _Transport:
        def session(self, endpoint: object):
            from scion_sdk.ports.session import TransportEndpoint

            return McpTransport().session(TransportEndpoint(in_process=mcp))

    async def run() -> None:
        listing = await list_server_tools(_Transport(), None, timeout_seconds=5)  # type: ignore[arg-type]
        assert isinstance(listing, ToolListing)
        assert [t.name for t in listing.tools] == ["ping"]

    # no declaration resource is served here, so listing it fails closed
    with pytest.raises(ToolListingError):
        anyio.run(run)


def test_cli_arguments_and_endpoint() -> None:
    args = parse_arguments(["--placeholder", "TOKEN", "--", "server", "--flag"])
    assert args.command == ["server", "--flag"]
    endpoint = build_endpoint(args)
    assert endpoint.env == {"TOKEN": PLACEHOLDER_VALUE}
    assert endpoint.command == "server"


def test_report_and_verdict_documents() -> None:
    verdict = ToolVerdict(
        tool="t", presets=("p",), status=ToolStatus.CERTIFIED, live="abc"
    )
    report = CertificationReport(
        connector="c", server="s", listed=True, verdicts=(verdict,)
    )
    assert report.passed is True
    assert report.document()["tools"][0]["status"] == "certified"
    checkout = ConnectorCheckout(connector="c", server="s", presets={})
    assert checkout.tools == ()


def test_exact_name_check_refuses_ambiguous_listings() -> None:
    old = (TOOL,)
    assert _exact_names("c", (TOOL, TOOL), old_connector="c", old_tools=old)[0] is False
    assert _exact_names("c", (TOOL,), old_connector="c", old_tools=())[0] is False
    assert _exact_names("c", (TOOL,), old_connector="other", old_tools=old)[0] is False


def test_resource_relation_and_canonical_json() -> None:
    relation = ResourceRelation(name="owns", target="Thing")
    assert relation.label == ""
    assert canonical({"b": 1, "a": 2}) == '{"a":2,"b":1}'


class _EvidenceEngine:
    """A loopback engine that stores evidence when a pack is imported."""

    def __init__(self) -> None:
        self.stored: dict[str, Any] = {}
        self.digest = "a" * 64

    async def import_pack(self, pack: Any) -> Any:
        self.stored[pack.connector] = {
            "connector": pack.connector,
            "tenant_id": "t1",
            "pack_digest": self.digest,
            "revision": 1,
            "issued_at": 1,
            "expires_at": 9,
            "state": "active",
            "tool_fingerprints": {"t": "b" * 64},
        }
        return SimpleNamespace(pack_digest=self.digest)

    async def _send(
        self, method: str, params: dict[str, Any], graph: str | None, **_: Any
    ) -> Any:
        assert method == "ConnectorPack"
        assert params["op"]["op"] == "conformance_evidence"
        found = self.stored.get(params["op"]["request"]["connector"])
        if found is None:
            raise RuntimeError("CONFORMANCE_EVIDENCE_ABSENT")
        return found


def _passing_report(connector: str = "demo") -> CertificationReport:
    verdict = ToolVerdict("t", ("p",), ToolStatus.CERTIFIED, live="c" * 64)
    return CertificationReport(connector, "srv", True, verdicts=(verdict,))


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R041")
async def test_report_is_submitted_as_conformance_evidence() -> None:
    engine = _EvidenceEngine()
    pack: Any = SimpleNamespace(connector="demo")
    evidence = await submit_certification_evidence(
        _passing_report(), sink=engine, client=engine, pack=pack, tenant_id="t1"
    )
    assert evidence.pack_digest == engine.digest
    assert set(evidence.tool_fingerprints) == {"t"}
    read = await read_conformance_evidence(engine, tenant_id="t1", connector="demo")
    assert read == evidence


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R041")
async def test_evidence_refuses_failing_mismatched_and_absent() -> None:
    engine = _EvidenceEngine()
    pack: Any = SimpleNamespace(connector="demo")
    failing = CertificationReport("demo", "srv", False)
    with pytest.raises(EvidenceRefused, match="report_not_passing"):
        await submit_certification_evidence(
            failing, sink=engine, client=engine, pack=pack, tenant_id="t1"
        )
    other: Any = SimpleNamespace(connector="other")
    with pytest.raises(EvidenceRefused, match="pack_connector_mismatch"):
        await submit_certification_evidence(
            _passing_report(), sink=engine, client=engine, pack=other, tenant_id="t1"
        )
    with pytest.raises(EvidenceRefused, match="evidence_mismatch"):
        await submit_certification_evidence(
            _passing_report(), sink=engine, client=engine, pack=pack, tenant_id="t2"
        )
    with pytest.raises(EvidenceRefused, match="evidence_unavailable"):
        await read_conformance_evidence(engine, tenant_id="t1", connector="none")


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R041")
def test_certify_main_submits_a_passing_report_and_exits_two_on_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run(_: Any) -> CertificationReport:
        return _passing_report()

    monkeypatch.setattr("scion_sdk.certify.cli._run_certification", run)
    seen: list[str] = []

    async def accept(report: CertificationReport) -> Any:
        seen.append(report.connector)

    async def refuse(report: CertificationReport) -> Any:
        raise EvidenceRefused("evidence_mismatch")

    args = ["--url", "http://127.0.0.1:9/mcp"]
    assert certify_main(args, evidence=accept) == 0
    assert seen == ["demo"]
    assert certify_main(args, evidence=refuse) == 2
    assert certify_main(args) == 0
