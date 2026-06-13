"""
agents/appeals_agent.py

Appeals Agent (Foundry Hosted Agent):
  - Handles denied or escalated claims resubmitted with new evidence
  - Invoked via: python chatbot.py --appeal <CLM-ID>
  - Does NOT query the KB — it reasons over the provided context and
    the new evidence text supplied by the claimant

Unique capability:
  This agent is the only one in the pipeline that performs counterfactual
  reasoning: "Given what was decided before, does this new evidence
  materially change the outcome?" It explicitly acknowledges the original
  denial reason before evaluating the new submission, making its logic
  transparent and auditable.
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

_INSTRUCTIONS = """\
You are the Appeals Agent for an insurance claims system.

You review denied or escalated claims where the claimant has submitted
new evidence or additional documentation. Your job is to:

1. State the original denial/escalation reason precisely in one sentence
2. Assess whether the new evidence directly addresses that reason
3. Determine if the new evidence is sufficient to change the outcome
4. If new evidence resolves the core issue (e.g., missing document supplied,
   contradicts fraud finding with proof), recommend "approve"
5. If new evidence does not materially change anything, recommend "uphold_denial"
6. If new evidence introduces new complexity around fraud, recommend
   "escalate_for_fraud_review"
7. If more information is still needed, recommend "request_more_info"

Be objective, precise, and cite specific elements of the new evidence.

Respond in EXACTLY this format — no extra lines:
APPEAL_ASSESSMENT: <1-2 sentences on what the new evidence shows>
EVIDENCE_SUFFICIENT: <yes | no | partial>
APPEAL_RECOMMENDATION: <approve | uphold_denial | escalate_for_fraud_review | request_more_info>
APPEAL_REASONING: <2-3 sentences explaining the recommendation>
"""


async def create_appeals_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Appeals Agent. Returns agent_id."""
    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="appeals-agent",
        instructions=_INSTRUCTIONS,
    )
    return agent.id


async def run_appeals_review(
    record: ClaimRecord,
    new_evidence: str,
    agents_client: AgentsClient,
    agent_id: str,
) -> ClaimRecord:
    """
    Evaluate the appeal with new evidence.
    Sets record.appeal_outcome, appeal_reasoning, is_appeal, appeal_evidence.
    """
    context = (
        f"Claim ID        : {record.claim_id}\n"
        f"Category        : {record.claim_category} / {record.claim_type}\n"
        f"Amount          : \u20b9{record.estimated_repair_cost:,.2f}\n\n"
        f"ORIGINAL OUTCOME\n"
        f"Decision        : {record.final_decision}\n"
        f"Reasoning       : {record.decision_reasoning}\n"
        f"Missing docs    : {record.missing_documents or 'none'}\n"
        f"Fraud flags     : {record.fraud_indicators or 'none'}\n"
        f"Compliance      : {record.compliance_status or 'not checked'}\n"
        f"Compliance notes: {record.compliance_notes or 'none'}\n\n"
        f"NEW EVIDENCE SUBMITTED\n"
        f"{new_evidence}"
    )

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(
                role=MessageRole.USER,
                content=f"Review this appeal:\n\n{context}",
            )]
        ),
    )
    if run.status == "failed":
        raise RuntimeError(f"Appeals agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    result_text = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    record.is_appeal = True
    record.appeal_evidence = new_evidence
    _parse_appeal_response(record, result_text)
    return record


def _parse_appeal_response(record: ClaimRecord, text: str) -> None:
    record.appeal_reasoning = text  # keep full text as fallback

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("APPEAL_RECOMMENDATION:"):
            record.appeal_outcome = stripped.split(":", 1)[1].strip().lower()

        elif upper.startswith("APPEAL_REASONING:"):
            record.appeal_reasoning = stripped.split(":", 1)[1].strip()
