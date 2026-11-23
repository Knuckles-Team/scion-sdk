"""Webhook verification and the connector-side normalization protocol.

SCION-SCM-R003.2: a connector proves its webhook deliveries with one of the
two schemes in :data:`SCM_SCHEMES` before it normalizes them into
:class:`~scion_sdk.scm.records.ScmFeedbackEvent`. The secret travels as a
credential reference, is resolved by :mod:`scion_sdk.credentials` and never
enters a log, an error message or an event. Every refusal emits no event.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from scion_sdk.credentials.resolution import resolve_secret_reference
from scion_sdk.scm.records import ScmFeedbackEvent

__all__ = [
    "SCM_SCHEMES",
    "ScmEventNormalizer",
    "ScmWebhookRefused",
    "verify_webhook",
]

#: The only signature schemes a webhook may prove its deliveries with.
SCM_SCHEMES: tuple[str, ...] = ("hmac_sha256", "shared_token")

_SIGNATURE_HEADER = "x-hub-signature-256"
_TOKEN_HEADER = "x-gitlab-token"  # nosec B105 - header name constant, not a credential


class ScmWebhookRefused(Exception):
    """An SCM webhook delivery was refused; no event is emitted.

    ``code`` is ``SCM_SIGNATURE_INVALID`` (missing or wrong signature or
    token, or an unknown scheme — an unknown scheme cannot hold a valid
    signature, so the refusal fails closed) or ``SCM_EVENT_UNSUPPORTED``
    (the provider event type has no feedback kind). Messages never contain
    the secret value.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class ScmEventNormalizer(Protocol):
    """A connector's webhook normalization surface.

    The connector maps its provider event types onto the closed feedback
    kinds and refuses everything else with
    :class:`ScmWebhookRefused` code ``SCM_EVENT_UNSUPPORTED``.
    """

    provider: str

    def normalize(self, headers: Mapping[str, str], body: bytes) -> ScmFeedbackEvent:
        """Normalize one verified delivery; raise for the unsupported types."""
        ...


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """A header value by case-insensitive name; HTTP headers are not
    case-sensitive on the wire."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def verify_webhook(
    scheme: str,
    secret_ref: str,
    headers: Mapping[str, str],
    body: bytes,
) -> None:
    """Verify a webhook delivery's signature before it is normalized.

    ``scheme`` is one of :data:`SCM_SCHEMES`; ``secret_ref`` is a credential
    reference (for example ``env://SCM_WEBHOOK_SECRET``) resolved through
    :func:`scion_sdk.credentials.resolution.resolve_secret_reference`.
    ``hmac_sha256`` compares ``sha256=<hex>`` of the HMAC-SHA256 of the raw
    ``body`` against the ``X-Hub-Signature-256`` header; ``shared_token``
    compares the secret against the ``X-Gitlab-Token`` header. Both
    comparisons use :func:`hmac.compare_digest` (constant time).

    Raises:
        ScmWebhookRefused: code ``SCM_SIGNATURE_INVALID`` when the scheme is
            unknown or the signature or token is missing or wrong.
    """
    if scheme not in SCM_SCHEMES:
        raise ScmWebhookRefused(
            "SCM_SIGNATURE_INVALID",
            f"webhook scheme {scheme!r} is not in {SCM_SCHEMES}",
        )
    secret = resolve_secret_reference(secret_ref)
    if scheme == "hmac_sha256":
        provided = _header(headers, _SIGNATURE_HEADER)
        if provided is None:
            raise ScmWebhookRefused(
                "SCM_SIGNATURE_INVALID", "webhook signature header is missing"
            )
        expected = (
            "sha256="
            + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(provided, expected):
            raise ScmWebhookRefused(
                "SCM_SIGNATURE_INVALID", "webhook signature is invalid"
            )
    else:
        provided = _header(headers, _TOKEN_HEADER)
        if provided is None:
            raise ScmWebhookRefused(
                "SCM_SIGNATURE_INVALID", "webhook token header is missing"
            )
        if not hmac.compare_digest(provided, secret):
            raise ScmWebhookRefused("SCM_SIGNATURE_INVALID", "webhook token is invalid")
