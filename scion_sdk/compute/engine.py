"""Call medulla compute methods and ontology-driven algorithm selection.

:class:`EngineCompute` wraps one connected synchronous medulla client.
:meth:`EngineCompute.select_algorithms` reads, by SPARQL over the connector's
imported ontology pack, which algorithms apply to an analysis and which served
method runs each one. :meth:`EngineCompute.run` calls that method. Any method
the client or server does not serve raises :class:`MethodNotServedError`. An
engine refusal that carries a stable ``KERNEL_*`` code (the served numeric
kernel evaluation) surfaces as :class:`ComputeRefusal` with that code.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from scion_sdk.compute._selection import _iri, _is_not_served, _selection_query
from scion_sdk.credentials.resolution import resolve_secret_reference
from scion_sdk.credentials.resolver import CredentialResolver
from scion_sdk.ingest.engine import EngineSettings

__all__ = [
    "COMPUTE_SCOPES",
    "AlgorithmBinding",
    "ComputeRefusal",
    "ComputeUnavailableError",
    "EngineCompute",
    "MethodNotServedError",
    "connect_compute",
]

#: Engine scopes a connector's compute session asks for: finance compute and
#: SPARQL reads of its ontology pack.
COMPUTE_SCOPES = ("compute:finance", "sparql:read")

_CLIENT_PATH = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


class ComputeUnavailableError(RuntimeError):
    """The graph service is not configured or cannot be reached."""


class MethodNotServedError(LookupError):
    """The graph service does not serve the requested method yet.

    Attributes:
        method: The client path (``namespace.method``) or analysis that was refused.
        handoff: The owner spec the missing method is recorded against, if known.
    """

    def __init__(self, method: str, *, handoff: str = "") -> None:
        self.method = method
        self.handoff = handoff
        detail = f" (handoff: {handoff})" if handoff else ""
        super().__init__(f"medulla does not serve {method}{detail}")


#: A stable numeric kernel refusal code (medulla's ``KernelRefusal``), at the
#: start of an error or after one status separator.
_KERNEL_CODE = re.compile(r"(?:^|: )(KERNEL_[A-Z0-9_]+)")


class ComputeRefusal(RuntimeError):
    """An engine refusal of a compute call carrying a stable kernel code.

    The engine renders a kernel refusal as ``INVALID_ARGUMENT`` with the
    stable ``KERNEL_*`` code first in the detail. Callers branch on
    :attr:`code`; :attr:`detail` is diagnostic text only.

    Attributes:
        code: The stable refusal code (for example ``KERNEL_INPUT_SHORT``).
        detail: The engine's diagnostic detail, for logging only.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _kernel_refusal(exc: BaseException) -> ComputeRefusal | None:
    """The stable kernel refusal an engine error carries, if it sent one."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        match = _KERNEL_CODE.match(detail)
        if match is not None:
            return ComputeRefusal(match.group(1), detail[match.end() :].lstrip(": "))
    text = str(exc)
    match = _KERNEL_CODE.search(text)
    if match is None:
        return None
    return ComputeRefusal(match.group(1), text[match.end() :].lstrip(": "))


@dataclass(frozen=True)
class AlgorithmBinding:
    """One algorithm the ontology binds to an analysis.

    Attributes:
        algorithm: The algorithm's IRI.
        method: The client path of the served method, ``namespace.method``.
        op: The operation selector for a multi-operation method, or ``""``.
    """

    algorithm: str
    method: str
    op: str = ""


class EngineCompute:
    """Typed calls into one connected medulla client."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        """The connected client, for served methods this adapter does not wrap."""
        return self._client

    def _bound(self, method: str) -> Callable[..., Any]:
        if not _CLIENT_PATH.match(method):
            raise ValueError("method must be a client path 'namespace.method'")
        namespace, name = method.split(".")
        target = getattr(getattr(self._client, namespace, None), name, None)
        if not callable(target):
            raise MethodNotServedError(method)
        bound: Callable[..., Any] = target
        return bound

    def call(self, method: str, /, **params: Any) -> Any:
        """Call the served method at client path ``method`` with ``params``.

        Raises:
            MethodNotServedError: the client or the server does not serve it.
            ComputeRefusal: the engine refuses the call with a stable kernel
                code.
        """
        target = self._bound(method)
        try:
            return target(**params)
        except Exception as exc:
            if _is_not_served(exc):
                raise MethodNotServedError(method) from exc
            refusal = _kernel_refusal(exc)
            if refusal is not None:
                raise refusal from exc
            raise

    def select_algorithms(
        self, analysis: str, *, vocabulary: str
    ) -> list[AlgorithmBinding]:
        """Ask the graph which algorithms its ontology binds to ``analysis``.

        ``vocabulary`` is the namespace IRI (ending in ``#`` or ``/``) that
        declares ``appliesAlgorithm``, ``servedBy`` and ``servedOp``.

        Raises:
            MethodNotServedError: the ontology binds no served algorithm, or
                SPARQL is not served.
        """
        _iri(analysis, "analysis")
        if not vocabulary.endswith(("#", "/")):
            raise ValueError("vocabulary must end with '#' or '/'")
        _iri(vocabulary, "vocabulary")
        rows = self.call("rdf.sparql", query=_selection_query(analysis, vocabulary))
        bindings = [
            AlgorithmBinding(
                algorithm=str(row["algorithm"]),
                method=str(row["method"]),
                op=str(row.get("op") or ""),
            )
            for row in rows or []
            if row.get("algorithm") and row.get("method")
        ]
        if not bindings:
            raise MethodNotServedError(analysis)
        return bindings

    def run(self, binding: AlgorithmBinding, /, **params: Any) -> Any:
        """Run one selected algorithm through its served method."""
        if binding.op:
            return self.call(binding.method, op=binding.op, **params)
        return self.call(binding.method, **params)

    def analyse(self, analysis: str, *, vocabulary: str, **params: Any) -> Any:
        """Select the first algorithm bound to ``analysis`` and run it."""
        return self.run(
            self.select_algorithms(analysis, vocabulary=vocabulary)[0], **params
        )


def _connect_client(
    settings: EngineSettings | None,
    scopes: tuple[str, ...],
    resolver: CredentialResolver | None,
) -> Any:
    resolved = settings or EngineSettings.from_settings()
    if resolved is None:
        raise ComputeUnavailableError("MEDULLA_ENDPOINT is not configured")
    secret = resolve_secret_reference(resolved.auth_secret_ref, resolver)
    address = resolved.address()
    from medulla.client import SyncMedullaClient

    return SyncMedullaClient.connect(
        auth_secret=secret,
        graph_name=resolved.graph,
        verified_context=resolved.verified_context(scopes),
        **address,
    )


def connect_compute(
    settings: EngineSettings | None = None,
    *,
    scopes: tuple[str, ...] = COMPUTE_SCOPES,
    resolver: CredentialResolver | None = None,
) -> EngineCompute:
    """Connect to medulla from :class:`EngineSettings` for compute calls.

    Raises:
        ComputeUnavailableError: no endpoint is configured, or the endpoint,
            secret or connection is unavailable.
    """
    try:
        client = _connect_client(settings, scopes, resolver)
    except ComputeUnavailableError:
        raise
    except Exception as exc:
        raise ComputeUnavailableError("medulla compute is unavailable") from exc
    return EngineCompute(client)
