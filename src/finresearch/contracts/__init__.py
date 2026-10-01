"""研究请求与资料可用性契约。"""

from .document_manifest import DocumentManifestRecord
from .evidence import AnswerValidation, DocumentPage, EvidenceCandidate, PageScore, RetrievalResult
from .model_output import ConnectivityProbeOutput, EvidenceAnswer, EvidenceReference, ToolRequest
from .metrics import (
    CalculationResult,
    ComparabilityCheck,
    MetricDefinition,
    MetricEvidenceRef,
    MetricObservation,
    MetricObservationSeed,
    stable_sha256,
)
from .research_request import (
    ResearchRequest,
    ScopeViolation,
    select_available_documents,
    validate_request_against_scope,
)

__all__ = [
    "ResearchRequest",
    "DocumentManifestRecord",
    "DocumentPage",
    "PageScore",
    "EvidenceCandidate",
    "RetrievalResult",
    "AnswerValidation",
    "ConnectivityProbeOutput",
    "EvidenceAnswer",
    "EvidenceReference",
    "ToolRequest",
    "MetricDefinition",
    "MetricEvidenceRef",
    "MetricObservationSeed",
    "MetricObservation",
    "ComparabilityCheck",
    "CalculationResult",
    "stable_sha256",
    "ScopeViolation",
    "select_available_documents",
    "validate_request_against_scope",
]
