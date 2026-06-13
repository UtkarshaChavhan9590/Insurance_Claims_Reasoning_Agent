"""
agents/models.py

Shared data models passed between agents in the workflow.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ClaimRecord:
    """Structured claim data, populated incrementally as it moves through agents."""

    claim_id: str
    raw_input: dict

    # ── Intake Agent ──────────────────────────────────────────────────────────
    policyholder_name: Optional[str] = None
    claim_type: Optional[str] = None
    estimated_repair_cost: Optional[float] = None
    documents_attached: list[str] = field(default_factory=list)
    police_report_attached: bool = False
    rideshare_active_at_incident: bool = False
    days_since_policy_inception: Optional[int] = None
    prior_claims_last_6_months: int = 0
    intake_notes: str = ""

    # ── Policy Reasoning Agent ────────────────────────────────────────────────
    coverage_assessment: Optional[str] = None
    policy_citations: list[str] = field(default_factory=list)
    fraud_indicators: list[str] = field(default_factory=list)
    missing_documents: list[str] = field(default_factory=list)

    # ── Document Validation Agent ─────────────────────────────────────────────
    claim_category: str = "auto"          # "auto" | "home" | "health" | "travel"
    claim_subtype: str = ""               # e.g. "collision", "fire", "hospitalization"
    document_requirements: list[str] = field(default_factory=list)
    deductible_amount: float = 0.0        # read from claim JSON input

    # ── Decision Agent ────────────────────────────────────────────────────────
    recommended_decision: Optional[str] = None  # "approve" | "deny" | "escalate"
    decision_reasoning: str = ""
    requires_human_approval: bool = False
    human_decision: Optional[str] = None
    final_decision: Optional[str] = None

    # ── Compliance Agent ──────────────────────────────────────────────────────
    compliance_status: str = ""              # "PASS" | "CONDITIONAL" | "FAIL"
    compliance_notes: str = ""
    regulatory_citations: list[str] = field(default_factory=list)
    compliance_override_reason: str = ""

    # ── Reimbursement Agent ───────────────────────────────────────────────────
    reimbursement_amount: float = 0.0
    reimbursement_reference: str = ""     # PAY-XXXXXXXX
    payment_timeline_days: int = 0
    reimbursement_status: str = ""        # "pending" | "initiated" | "on_hold"
    notice_letter: str = ""               # approval payment notice OR denial letter

    # ── Appeals Agent ─────────────────────────────────────────────────────────
    is_appeal: bool = False
    appeal_evidence: str = ""
    appeal_outcome: Optional[str] = None    # "approve"|"uphold_denial"|"escalate_for_fraud_review"|"request_more_info"
    appeal_reasoning: str = ""

    # ── Claims Summarizer Agent ───────────────────────────────────────────────
    executive_summary: str = ""
