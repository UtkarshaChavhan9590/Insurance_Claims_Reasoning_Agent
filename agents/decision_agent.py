"""
agents/decision_agent.py

Decision Agent (Foundry Hosted Agent):
  - Created once at startup via project_client.agents.create_agent()
  - Synthesizes intake + policy reasoning into a recommended decision
  - Implements the Human-in-the-Loop (HITL) gate

HITL rules:
  - Auto-approved: amount < limit AND no fraud AND no missing docs
  - Everything else: paused, requires human input (approve/deny/request_more_info)
"""

import os

from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import (
    AgentThreadCreationOptions,
    MessageRole,
    ThreadMessageOptions,
)
from dotenv import load_dotenv

from agents.models import ClaimRecord

load_dotenv()

DECISION_AGENT_INSTRUCTIONS = """\
You are the Decision Agent for an insurance claims system. You receive a
claim summary along with the Policy Reasoning Agent's coverage assessment
(including citations, missing documents, and fraud indicators).

Your job:
1. Reason step-by-step (briefly) about whether the claim should be
   approved, denied, or escalated for further review.
2. If documents are missing, recommend "escalate" with a note that the
   claimant should be asked for the specific missing items -- do not deny
   solely for missing documents.
3. If any fraud indicator is present, recommend "escalate" -- fraud
   findings always require a human fraud analyst regardless of amount.
4. If an exclusion clearly applies, recommend "deny" and cite the
   exclusion.
5. Otherwise, recommend "approve".

Respond in this exact format:
REASONING: <2-4 sentences of step-by-step reasoning>
RECOMMENDATION: <approve | deny | escalate>
"""


async def create_decision_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Decision Agent and return its agent_id."""
    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="decision-agent",
        instructions=DECISION_AGENT_INSTRUCTIONS,
    )
    return agent.id


async def run_decision(record: ClaimRecord, agents_client: AgentsClient, agent_id: str) -> ClaimRecord:
    """
    Run the Decision Agent to produce a recommendation, then apply the HITL gate.
    """
    auto_approve_limit = float(os.environ.get("CLAIM_AUTO_APPROVE_LIMIT", "2000"))

    context = (
        f"Claim ID: {record.claim_id}\n"
        f"Estimated repair cost: \u20b9{record.estimated_repair_cost:,.2f}\n"
        f"Auto-approval limit: \u20b9{auto_approve_limit:,.2f}\n\n"
        f"Policy Reasoning Agent assessment:\n{record.coverage_assessment}\n\n"
        f"Missing documents: {record.missing_documents or 'none'}\n"
        f"Fraud indicators: {record.fraud_indicators or 'none'}"
    )

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(
                role=MessageRole.USER,
                content=f"Recommend a decision for this claim:\n\n{context}",
            )]
        ),
    )
    if run.status == "failed":
        raise RuntimeError(f"Decision agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    result_text = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    _parse_decision_response(record, result_text)

    # ---- Human-in-the-Loop gate ----
    eligible_for_auto_approval = (
        record.recommended_decision == "approve"
        and record.estimated_repair_cost < auto_approve_limit
        and not record.fraud_indicators
        and not record.missing_documents
    )

    if eligible_for_auto_approval:
        record.requires_human_approval = False
        record.final_decision = "approved (auto)"
    else:
        record.requires_human_approval = True
        record.final_decision = None  # pending human input

    return record


def _parse_decision_response(record: ClaimRecord, text: str) -> None:
    record.decision_reasoning = text
    lines = text.splitlines()
    reasoning_lines: list[str] = []
    in_reasoning = False

    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("RECOMMENDATION:"):
            val = stripped.split(":", 1)[1].strip().lower()
            record.recommended_decision = val
            in_reasoning = False
        elif stripped.upper().startswith("REASONING:"):
            reasoning_lines = [stripped.split(":", 1)[1].strip()]
            in_reasoning = True
        elif in_reasoning and stripped:
            reasoning_lines.append(stripped)

    if reasoning_lines:
        record.decision_reasoning = " ".join(reasoning_lines)


def apply_human_decision(record: ClaimRecord, human_decision: str) -> ClaimRecord:
    """
    Apply a human reviewer's decision to a paused claim.
    human_decision: "approve" | "deny" | "request_more_info"
    """
    record.human_decision = human_decision
    if human_decision == "approve":
        record.final_decision = "approved (human)"
    elif human_decision == "deny":
        record.final_decision = "denied (human)"
    elif human_decision == "request_more_info":
        record.final_decision = "pending - more info requested"
    else:
        raise ValueError(f"Unknown human decision: {human_decision}")
    return record
