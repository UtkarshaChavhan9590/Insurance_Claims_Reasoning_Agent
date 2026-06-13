"""
orchestration/workflow.py

End-to-end insurance claims pipeline using eight Foundry Hosted Agents:

  [1/7] Intake Agent           — Foundry agent: normalise + LLM summary
  [2/7] Document Validation    — deterministic per-category doc check (no LLM)
  [3/7] Policy + Fraud         — CONCURRENT Foundry agents, KB-grounded
  [4/7] Decision Agent + HITL  — Foundry agent: chain-of-thought + human gate
  [5/7] Compliance Agent       — Foundry agent + KB: regulatory validation
  [6/7] Reimbursement Agent    — Foundry agent: approval/denial letter
  [7/7] Claims Summarizer      — Foundry agent: whole-pipeline executive summary

Orchestration patterns:
  SEQUENTIAL  — stages 1 → 2 → 4 → 5 → 6 → 7
  CONCURRENT  — stage 3: Policy Reasoning + Fraud Detection via asyncio.gather
  HITL        — stage 4: pauses if requires_human_approval=True
  OVERRIDE    — stage 5: Compliance can override Decision and re-trigger HITL
  APPEAL      — separate flow: Appeals → optional resume_claim() → stages 6+7
"""

import asyncio
import dataclasses
import json
import os
from dataclasses import asdict
from pathlib import Path

from azure.ai.agents.aio import AgentsClient
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

from agents.compliance_agent import create_compliance_agent, run_compliance_check
from agents.decision_agent import apply_human_decision, create_decision_agent, run_decision
from agents.document_agent import run_document_validation
from agents.fraud_agent import create_fraud_agent, run_fraud_detection
from agents.intake_agent import create_intake_agent, run_intake
from agents.models import ClaimRecord
from agents.policy_agent import create_policy_agent, run_policy_reasoning
from agents.reimbursement_agent import create_reimbursement_agent, run_reimbursement
from agents.summarizer_agent import create_summarizer_agent, run_summarizer
from agents.appeals_agent import create_appeals_agent, run_appeals_review

load_dotenv()

CHECKPOINT_DIR = Path("checkpoints")
CHECKPOINT_DIR.mkdir(exist_ok=True)

_W = 66
_BAR = "━" * 70


# ── Agent registry ────────────────────────────────────────────────────────────

class AgentRegistry:
    """Holds the shared AgentsClient and all eight Foundry agent IDs."""

    def __init__(self):
        self.client: AgentsClient | None = None
        self._credential: DefaultAzureCredential | None = None
        self.intake_id: str | None = None
        self.fraud_id: str | None = None
        self.policy_id: str | None = None
        self.decision_id: str | None = None
        self.compliance_id: str | None = None
        self.reimbursement_id: str | None = None
        self.appeals_id: str | None = None
        self.summarizer_id: str | None = None

    async def initialise(self) -> None:
        """Create the shared client and all eight Foundry hosted agents."""
        self._credential = DefaultAzureCredential()
        self.client = AgentsClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=self._credential,
        )
        print("  ◈  Initialising Foundry IQ agents...")
        (
            self.intake_id,
            self.fraud_id,
            self.policy_id,
            self.decision_id,
            self.compliance_id,
            self.reimbursement_id,
            self.appeals_id,
            self.summarizer_id,
        ) = await asyncio.gather(
            create_intake_agent(self.client),
            create_fraud_agent(self.client),
            create_policy_agent(self.client),
            create_decision_agent(self.client),
            create_compliance_agent(self.client),
            create_reimbursement_agent(self.client),
            create_appeals_agent(self.client),
            create_summarizer_agent(self.client),
        )
        _print_agent_table(self)

    async def cleanup(self) -> None:
        """Delete all hosted agents and close the client on shutdown."""
        if self.client is None:
            return
        ids = [
            self.intake_id, self.fraud_id, self.policy_id, self.decision_id,
            self.compliance_id, self.reimbursement_id, self.appeals_id, self.summarizer_id,
        ]
        await asyncio.gather(*[
            self.client.delete_agent(aid) for aid in ids if aid
        ])
        await self.client.close()
        if self._credential:
            await self._credential.close()


# ── Pipeline ──────────────────────────────────────────────────────────────────
def _stage(n: "int | str", name: str, tag: str = "Foundry") -> None:
    """Print a stage progress header with symbol and badge."""
    label = f"Stage {n}/7  │  {name}" if isinstance(n, int) else f"{n}  │  {name}"
    print(f"\n  ◉ {label:<46} [{tag}]")


def _detail(key: str, value) -> None:
    """Print a stage detail line."""
    print(f"    ▸ {key:<16} {value}")

async def process_claim(claim_input: dict, registry: AgentRegistry) -> ClaimRecord:
    """
    Run the 7-stage pipeline.

    Returns early after stage 4 or stage 5 when requires_human_approval=True
    (the ClaimRecord is checkpointed and chatbot.py handles HITL display).
    """
    cid = claim_input["claim_id"]
    cat = claim_input.get("claim_category", "auto").upper()

    print(f"\n{_BAR}")
    print(f"  ◆  CLAIM {cid}  │  Category: {cat}")
    print(_BAR)

    # ── [1/7] Intake ──────────────────────────────────────────────────────────
    _stage(1, "Intake Agent")
    record = await run_intake(claim_input, registry.client, registry.intake_id)
    record.claim_category = claim_input.get("claim_category", "auto")
    record.claim_subtype = claim_input.get("claim_type", "")
    record.deductible_amount = float(claim_input.get("deductible_amount", 0.0))
    _detail("Notes", record.intake_notes)

    # ── [2/7] Document Validation ─────────────────────────────────────────────
    _stage(2, "Document Validation", "Rules")
    record = run_document_validation(record)
    if record.missing_documents:
        _detail("Missing", ", ".join(record.missing_documents))
    else:
        _detail("Documents", "✓ All present")

    # ── [3/7] Policy + Fraud (concurrent) ────────────────────────────────────
    _stage(3, "Policy Reasoning  ⇄  Fraud Detection", "Foundry ◈ KB · concurrent")
    record, fraud_flags = await asyncio.gather(
        run_policy_reasoning(record, registry.client, registry.policy_id),
        run_fraud_detection(record, registry.client, registry.fraud_id),
    )
    merged_fraud = list(record.fraud_indicators)
    for flag in fraud_flags:
        if flag not in merged_fraud:
            merged_fraud.append(flag)
    record.fraud_indicators = merged_fraud
    _detail("Citations", record.policy_citations or "none")
    _detail("Fraud flags", record.fraud_indicators or "none")

    # ── [4/7] Decision Agent + HITL gate ─────────────────────────────────────
    _stage(4, "Decision Agent + HITL Gate")
    record = await run_decision(record, registry.client, registry.decision_id)
    _detail("Recommendation", record.recommended_decision)
    _detail("Reasoning", record.decision_reasoning)

    if record.requires_human_approval:
        _checkpoint(record)
        return record  # chatbot.py displays HITL prompt and calls resume_claim()

    # ── [5/7] Compliance Agent ────────────────────────────────────────────────
    _stage(5, "Compliance Agent", "Foundry ◈ KB")
    record = await run_compliance_check(record, registry.client, registry.compliance_id)
    _detail("Status", record.compliance_status)
    _detail("Citations", record.regulatory_citations or "none")
    if record.compliance_override_reason:
        _detail("Override", record.compliance_override_reason)

    if record.requires_human_approval:  # compliance override re-triggered HITL
        _checkpoint(record)
        return record

    # ── [6/7] Reimbursement Agent ─────────────────────────────────────────────
    _stage(6, "Reimbursement Agent")
    record = await run_reimbursement(record, registry.client, registry.reimbursement_id)
    record.reimbursement_status = "initiated"
    _detail("Amount", f"₹{record.reimbursement_amount:,.2f}")
    _detail("Reference", record.reimbursement_reference)

    # ── [7/7] Claims Summarizer ───────────────────────────────────────────────
    _stage(7, "Claims Summarizer")
    record = await run_summarizer(record, registry.client, registry.summarizer_id)
    _detail("Saved", f"checkpoints/{cid}_summary.txt")

    _checkpoint(record)
    return record


async def resume_claim(
    claim_id: str, human_decision: str, registry: AgentRegistry
) -> ClaimRecord:
    """
    Resume a paused workflow after a human reviewer responds.
    Continues from stage 5 (Compliance) through stage 7 (Summarizer).
    """
    record = load_checkpoint(claim_id)
    record = apply_human_decision(record, human_decision)

    # ── [5/7] Compliance ──────────────────────────────────────────────────────
    _stage(5, "Compliance Agent", "Foundry ◈ KB")
    record = await run_compliance_check(record, registry.client, registry.compliance_id)
    _detail("Status", record.compliance_status)

    if record.requires_human_approval:   # compliance override — needs another HITL
        _checkpoint(record)
        return record

    # ── [6/7] Reimbursement ───────────────────────────────────────────────────
    _stage(6, "Reimbursement Agent")
    record = await run_reimbursement(record, registry.client, registry.reimbursement_id)
    record.reimbursement_status = "initiated"
    _detail("Amount", f"₹{record.reimbursement_amount:,.2f}")

    # ── [7/7] Summarizer ──────────────────────────────────────────────────
    _stage(7, "Claims Summarizer")
    record = await run_summarizer(record, registry.client, registry.summarizer_id)
    _detail("Saved", f"checkpoints/{claim_id}_summary.txt")

    _checkpoint(record)
    return record


async def process_appeal(
    claim_id: str, new_evidence: str, registry: AgentRegistry
) -> ClaimRecord:
    """
    Appeal flow: load checkpoint → Appeals Agent → route by outcome.
      'approve'                 → compliance → reimbursement → summarizer
      'uphold_denial'           → reimbursement (denial letter) → summarizer
      'escalate_for_fraud_review' | 'request_more_info' → HITL pause
    """
    record = load_checkpoint(claim_id)

    _stage("Appeal", "Appeals Agent", "Foundry · counterfactual reasoning")
    record = await run_appeals_review(
        record, new_evidence, registry.client, registry.appeals_id
    )
    _detail("Outcome", record.appeal_outcome)
    _detail("Reasoning", record.appeal_reasoning)

    if record.appeal_outcome == "approve":
        record.recommended_decision = "approve"
        record.requires_human_approval = False

        _stage(5, "Compliance Agent", "Foundry ◈ KB")
        record = await run_compliance_check(record, registry.client, registry.compliance_id)
        _detail("Status", record.compliance_status)

        if record.requires_human_approval:
            _checkpoint(record)
            return record

        _stage(6, "Reimbursement Agent")
        record = await run_reimbursement(record, registry.client, registry.reimbursement_id)
        record.reimbursement_status = "initiated"
        _detail("Amount", f"₹{record.reimbursement_amount:,.2f}")

    elif record.appeal_outcome == "uphold_denial":
        record.recommended_decision = "deny"
        record.final_decision = "deny"
        record.requires_human_approval = False

        _stage(6, "Reimbursement Agent", "Foundry · denial letter")
        record = await run_reimbursement(record, registry.client, registry.reimbursement_id)

    else:  # escalate_for_fraud_review | request_more_info
        record.requires_human_approval = True
        _checkpoint(record)
        return record

    _stage(7, "Claims Summarizer")
    record = await run_summarizer(record, registry.client, registry.summarizer_id)
    _detail("Saved", f"checkpoints/{claim_id}_summary.txt")

    _checkpoint(record)
    return record


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_checkpoint(claim_id: str) -> ClaimRecord:
    """
    Load a ClaimRecord from disk, filtering unknown fields so that old
    checkpoints without new ClaimRecord fields don't cause a TypeError.
    """
    path = CHECKPOINT_DIR / f"{claim_id}.json"
    with open(path) as f:
        data = json.load(f)
    known = {field.name for field in dataclasses.fields(ClaimRecord)}
    filtered = {k: v for k, v in data.items() if k in known}
    return ClaimRecord(**filtered)


def _checkpoint(record: ClaimRecord) -> None:
    path = CHECKPOINT_DIR / f"{record.claim_id}.json"
    with open(path, "w") as f:
        json.dump(asdict(record), f, indent=2, default=str)


def save_checkpoint(record: ClaimRecord) -> None:
    """Public function to persist an updated ClaimRecord to disk."""
    _checkpoint(record)


def _print_agent_table(reg: AgentRegistry) -> None:
    rows = [
        ("◈  Intake",          reg.intake_id),
        ("◈  Fraud",           reg.fraud_id),
        ("◈  Policy",          reg.policy_id),
        ("◈  Decision",        reg.decision_id),
        ("◈  Compliance",      reg.compliance_id),
        ("◈  Reimbursement",   reg.reimbursement_id),
        ("◈  Appeals",         reg.appeals_id),
        ("◈  Summarizer",      reg.summarizer_id),
    ]
    header = "─ Foundry Agents Initialised ─"
    inner_w = 64
    print(f"\n  ┌{header}{'─' * (inner_w - len(header))}┐")
    for name, aid in rows:
        line = f"  {name:<20} {aid or 'n/a'}"
        print(f"  │{line.ljust(inner_w)}│")
    print(f"  └{'─' * inner_w}┘")
    print()
