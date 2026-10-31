"""Assemble the per-process ``kg_intelligence`` payload from a graph reader."""

from __future__ import annotations

from typing import Any

__all__ = ["gather_intelligence"]


def _rel(props: Any) -> str:
    if isinstance(props, dict):
        return str(props.get("rel_type") or props.get("relType") or "").upper()
    return ""


def _node_type(props: Any) -> str:
    if isinstance(props, dict):
        return str(props.get("type") or props.get("label") or "")
    return ""


def _name(props: Any, fallback: str = "") -> str:
    if isinstance(props, dict):
        return str(props.get("name") or props.get("short_description") or fallback)
    return fallback


def _capabilities_from_in_edges(
    reader: Any, in_edges: list[tuple[str, Any]]
) -> list[str]:
    capabilities: list[str] = []
    for src, props in in_edges:
        if _rel(props) != "REALIZES":
            continue
        wf_name = _name(reader.node_props(src) or {}, src)
        orchestrated = [
            _name(reader.node_props(tgt) or {}, tgt)
            for tgt, ep in (reader.out_edges(src) or [])
            if _rel(ep) == "ORCHESTRATES"
        ]
        capabilities.append(
            f"{wf_name} ({', '.join(orchestrated)})" if orchestrated else wf_name
        )
    return capabilities


def _alignment_and_governance(
    reader: Any,
    out_edges: list[tuple[str, Any]],
    in_edges: list[tuple[str, Any]],
) -> tuple[list[str], list[str]]:
    aligned_with: list[str] = []
    governance: list[str] = []
    for tgt, props in out_edges:
        rel = _rel(props)
        if rel == "ALIGNED_WITH":
            aligned_with.append(str(tgt))
        elif rel in ("GOVERNEDBY", "GOVERNED_BY"):
            governance.append(_name(reader.node_props(tgt) or {}, tgt))
    for src, props in in_edges:
        if _rel(props) == "ALIGNED_WITH":
            aligned_with.append(str(src))
    return aligned_with, governance


def _incidents_from_in_edges(reader: Any, in_edges: list[tuple[str, Any]]) -> list[str]:
    incidents: list[str] = []
    for src, props in in_edges:
        if _rel(props) == "AFFECTS":
            incidents.append(_name(reader.node_props(src) or {}, str(src)))
    return incidents


def _task_ids_from_in_edges(reader: Any, in_edges: list[tuple[str, Any]]) -> list[str]:
    return [
        src
        for src, props in in_edges
        if _rel(props) == "PART_OF"
        and "task" in _node_type(reader.node_props(src) or {}).lower()
    ]


def _data_objects_and_glossary(
    reader: Any, task_ids: list[str]
) -> tuple[list[str], list[str]]:
    data_objects: list[str] = []
    glossary_terms: list[str] = []
    for task_id in task_ids:
        for tgt, ep in reader.out_edges(task_id) or []:
            tprops = reader.node_props(tgt) or {}
            ttype = _node_type(tprops).lower()
            rel = _rel(ep)
            if "dataobject" in ttype and rel in (
                "FLOWS_TO",
                "DERIVESFROM",
                "DERIVES_FROM",
            ):
                data_objects.append(_name(tprops, str(tgt)))
            elif "concept" in ttype and rel in ("MENTIONS", "RELATES_TO"):
                glossary_terms.append(_name(tprops, str(tgt)))
    return data_objects, glossary_terms


def gather_intelligence(reader: Any, process_id: str) -> dict[str, Any]:
    """Assemble the ``kg_intelligence`` payload for one process node."""
    out_edges = list(reader.out_edges(process_id) or [])
    in_edges = list(reader.in_edges(process_id) or [])

    capabilities = _capabilities_from_in_edges(reader, in_edges)
    aligned_with, governance = _alignment_and_governance(reader, out_edges, in_edges)
    incidents = _incidents_from_in_edges(reader, in_edges)
    task_ids = _task_ids_from_in_edges(reader, in_edges)
    data_objects, glossary_terms = _data_objects_and_glossary(reader, task_ids)

    return {
        "capabilities": sorted(set(capabilities)),
        "aligned_with": sorted(set(aligned_with)),
        "governance": sorted(set(governance)),
        "incidents": sorted(set(incidents)),
        "glossary_terms": sorted(set(glossary_terms)),
        "data_objects": sorted(set(data_objects)),
    }
