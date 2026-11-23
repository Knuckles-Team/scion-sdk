"""The SCM change-proposal contract: typed records, feedback events and the
verified webhook normalization protocol."""

from scion_sdk.scm.events import (
    SCM_SCHEMES,
    ScmEventNormalizer,
    ScmWebhookRefused,
    verify_webhook,
)
from scion_sdk.scm.records import (
    ChangeProposalRef,
    CheckRunRecord,
    ReviewRecord,
    ReviewThreadRecord,
    ScmFeedbackEvent,
)

__all__ = [
    "SCM_SCHEMES",
    "ChangeProposalRef",
    "CheckRunRecord",
    "ReviewRecord",
    "ReviewThreadRecord",
    "ScmEventNormalizer",
    "ScmFeedbackEvent",
    "ScmWebhookRefused",
    "verify_webhook",
]
