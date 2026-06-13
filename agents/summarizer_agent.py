"""
agents/summarizer_agent.py

Claims Summarizer Agent (Foundry Hosted Agent):
  - Always runs as the FINAL stage (stage 7) after a claim is completed
  - Produces a structured executive summary saved to checkpoints/
  - No KB tool — synthesises from the fully populated ClaimRecord

Unique capability:
  This agent is the only one with a whole-pipeline view. It synthesises
  every agent's output — intake flags, KB citations, fraud findings,
  compliance regulations, HITL interventions, appeal outcomes, and the
  final financial result — into a compact audit trail. This makes every
  claim fully reconstructable for compliance review without reading raw logs.
"""
import json
import os
from pathlib import Path

from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import (
    AgentThreadCreationOptions,
    MessageRole,
    ThreadMessageOptions,
)
from dotenv import load_dotenv

from agents.models import ClaimRecord

load_dotenv()

_INSTRUCTIONS = """\
You are the Claims Summarizer Agent for an insurance claims processing system.

You receive a completed claim record after all pipeline stages are done.
Generate a concise, structured executive summary of the entire claim journey.

Include:
1. Claim identity: ID, category, type, policyholder name
2. Financial summary: claimed amount, deductible, approved/denied amount
3. Pipeline journey: key findings at each stage (intake flags, missing docs,
   fraud findings, policy citations, compliance result, decision path)
4. Any HITL intervention (human decision, reason), appeal (outcome, evidence)
5. One clear outcome statement

Format under these exact headings with no markdown:
CLAIM IDENTITY
FINANCIAL SUMMARY
PIPELINE JOURNEY
KNOWLEDGE BASE CITATIONS
OUTCOME

Under 250 words total. Be factual. Use plain text only.
"""

_CHECKPOINT_DIR = Path("checkpoints")


async def create_summarizer_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Claims Summarizer Agent. Returns agent_id."""
    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="claims-summarizer-agent",
        instructions=_INSTRUCTIONS,
    )
    return agent.id


async def run_summarizer(
    record: ClaimRecord, agents_client: AgentsClient, agent_id: str
) -> ClaimRecord:
    """
    Generate the executive summary and save it to checkpoints/{claim_id}_summary.txt.
    """
    digest = {
        "claim_id": record.claim_id,
        "category": record.claim_category,
        "claim_type": record.claim_type,
        "policyholder": record.policyholder_name,
        "estimated_amount": record.estimated_repair_cost,
        "deductible": record.deductible_amount,
        "reimbursement_amount": record.reimbursement_amount,
        "reimbursement_reference": record.reimbursement_reference,
        "payment_timeline_days": record.payment_timeline_days,
        "days_since_inception": record.days_since_policy_inception,
        "intake_notes": record.intake_notes,
        "missing_documents": record.missing_documents,
        "fraud_indicators": record.fraud_indicators,
        "policy_citations": record.policy_citations,
        "compliance_status": record.compliance_status,
        "regulatory_citations": record.regulatory_citations,
        "compliance_notes": record.compliance_notes,
        "compliance_override_reason": record.compliance_override_reason,
        "recommended_decision": record.recommended_decision,
        "decision_reasoning": record.decision_reasoning,
        "required_human_approval": record.requires_human_approval,
        "human_decision": record.human_decision,
        "final_decision": record.final_decision,
        "is_appeal": record.is_appeal,
        "appeal_outcome": record.appeal_outcome,
        "appeal_reasoning": record.appeal_reasoning,
    }

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(
                role=MessageRole.USER,
                content=(
                    f"Generate the executive summary for this completed claim:\n\n"
                    f"{json.dumps(digest, indent=2, default=str)}"
                ),
            )]
        ),
    )
    if run.status == "failed":
        raise RuntimeError(f"Summarizer agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    record.executive_summary = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    _CHECKPOINT_DIR.mkdir(exist_ok=True)
    summary_path = _CHECKPOINT_DIR / f"{record.claim_id}_summary.txt"
    summary_path.write_text(record.executive_summary, encoding="utf-8")

    return record
