"""Stamp the namespace tag and upstream metadata onto proxied components (R914.5).

Every proxied tool, resource, template and prompt carries the tag
``ns:<namespace>`` and the metadata key ``scion/upstream`` set to
``{namespace, upstream}``. The tag is what the visibility gate matches; the
metadata is what a fleet gateway and the certification filter read.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform

from scion_sdk.mcp.upstreams.model import UpstreamDecl
from scion_sdk.upstream_wire import (
    UPSTREAM_META_KEY,
    UPSTREAM_META_NAMESPACE_KEY,
)

__all__ = [
    "UPSTREAM_META_KEY",
    "UPSTREAM_META_NAMESPACE_KEY",
    "NamespaceTag",
    "upstream_metadata",
]


def upstream_metadata(decl: UpstreamDecl) -> dict[str, dict[str, str]]:
    """The metadata value a proxied component carries for ``decl``."""
    return {
        UPSTREAM_META_KEY: {
            UPSTREAM_META_NAMESPACE_KEY: decl.effective_namespace,
            "upstream": decl.name,
        }
    }


class NamespaceTag(Transform):
    """Adds ``ns:<namespace>`` and the ``scion/upstream`` metadata to every component."""

    def __init__(self, decl: UpstreamDecl) -> None:
        super().__init__()
        self._tag = f"ns:{decl.effective_namespace}"
        self._meta = upstream_metadata(decl)

    def _stamp(self, component: Any) -> Any:
        tags = set(component.tags or ()) | {self._tag}
        meta = {**(component.meta or {}), **self._meta}
        return component.model_copy(update={"tags": tags, "meta": meta})

    async def list_tools(self, tools: Sequence[Any]) -> Sequence[Any]:
        return [self._stamp(tool) for tool in tools]

    async def get_tool(self, name: str, call_next: Any, *, version: Any = None) -> Any:
        tool = await call_next(name, version=version)
        return self._stamp(tool) if tool is not None else None

    async def list_resources(self, resources: Sequence[Any]) -> Sequence[Any]:
        return [self._stamp(resource) for resource in resources]

    async def get_resource(
        self, uri: str, call_next: Any, *, version: Any = None
    ) -> Any:
        resource = await call_next(uri, version=version)
        return self._stamp(resource) if resource is not None else None

    async def list_resource_templates(self, templates: Sequence[Any]) -> Sequence[Any]:
        return [self._stamp(template) for template in templates]

    async def get_resource_template(
        self, uri: str, call_next: Any, *, version: Any = None
    ) -> Any:
        template = await call_next(uri, version=version)
        return self._stamp(template) if template is not None else None

    async def list_prompts(self, prompts: Sequence[Any]) -> Sequence[Any]:
        return [self._stamp(prompt) for prompt in prompts]

    async def get_prompt(
        self, name: str, call_next: Any, *, version: Any = None
    ) -> Any:
        prompt = await call_next(name, version=version)
        return self._stamp(prompt) if prompt is not None else None
