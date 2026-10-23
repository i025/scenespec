"""Fixed proposal/audit controller; candidate execution is deliberately unavailable."""

from .fixed import (
    FixedMetaConfig,
    IsolationUnavailable,
    assert_candidate_execution_available,
    prepare_request,
    proposal_plan,
    record_candidate,
)

__all__ = [
    "FixedMetaConfig", "IsolationUnavailable", "prepare_request",
    "proposal_plan", "record_candidate", "assert_candidate_execution_available",
]
