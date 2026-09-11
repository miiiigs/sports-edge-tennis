"""Kill-only Tennis Veto V1.

The Veto is a downstream safety layer. It can only turn an otherwise-qualified
Type A market decision into VETOED; it never creates or rescues a selection.
"""

from .contracts import PreviousMatchEvidence, VetoContext, VetoEvaluation
from .service import evaluate_veto_for_decision, persist_veto_audit

__all__ = [
    "PreviousMatchEvidence",
    "VetoContext",
    "VetoEvaluation",
    "evaluate_veto_for_decision",
    "persist_veto_audit",
]
