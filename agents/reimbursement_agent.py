"""
agents/reimbursement_agent.py

Reimbursement Agent (Foundry Hosted Agent):
  - Created once at startup via project_client.agents.create_agent()
  - Runs after the final decision is committed (stage 5 handoff)

On APPROVAL:
  - Computes reimbursement_amount = estimated_repair_cost - deductible_amount
  - Generates a PAY-XXXXXXXX payment reference
  - Sets payment_timeline_days (3 for auto-approved, 7 for human-reviewed)
  - Foundry agent writes the formal payment notice letter

On DENIAL:
  - Foundry agent writes the formal denial letter citing decision_reasoning

Foundry pattern used:
  create_agent → create_thread → create_message → create_and_process_run
  → list_messages → delete_thread
"""

import os
import secrets

from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import (
    AgentThreadCreationOptions,
    MessageRole,
    ThreadMessageOptions,
)
from dotenv import load_dotenv

from agents.models import ClaimRecord

load_dotenv()

_AGENT_INSTRUCTIONS = """\
You are a professional insurance claims correspondent.
You will be asked to write either an approval payment notice or a denial notice.
The specific letter type and all required details will be provided in the user message.
Write a formal, professional letter of under 200 words. Do not invent details not provided.
For contact information use the placeholder: 1-800-CLAIMS.
"""

_APPROVAL_PROMPT_TEMPLATE = """\
Write a formal, courteous PAYMENT APPROVAL NOTICE for:
  Policyholder: {policyholder_name}
  Claim ID: {claim_id}
  Claim type: {claim_type} ({claim_category})
  Total claim amount: \u20b9{total:,.2f}
  Deductible applied: \u20b9{deductible:,.2f}
  Approved reimbursement: \u20b9{reimbursement:,.2f}
  Payment reference: {ref}
  Payment timeline: {timeline} business days
  Decision type: {decision_type}

The letter should acknowledge the claim, state the reimbursement amount and
payment reference, give the expected timeline, and close with contact info.
"""

_DENIAL_PROMPT_TEMPLATE = """\
Write a formal, empathetic DENIAL NOTICE for:
  Policyholder: {policyholder_name}
  Claim ID: {claim_id}
  Claim type: {claim_type} ({claim_category})
  Claim amount: \u20b9{total:,.2f}
  Decision: {decision}
  Reason: {reason}

The letter should acknowledge the claim, clearly state the denial with reason,
explain any right to appeal or provide additional documentation, and close with contact info.
"""


def _generate_payment_ref() -> str:
    return "PAY-" + secrets.token_hex(4).upper()


async def create_reimbursement_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Reimbursement Agent and return its agent_id."""
    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="reimbursement-agent",
        instructions=_AGENT_INSTRUCTIONS,
    )
    return agent.id


async def run_reimbursement(record: ClaimRecord, agents_client: AgentsClient, agent_id: str) -> ClaimRecord:
    """
    Generate the post-decision notice letter and set reimbursement fields.
    Called for both approved and denied final decisions (stage 5 handoff).
    """
    final = (record.final_decision or "").lower()
    is_approved = "approved" in final
    is_auto = "auto" in final

    if is_approved:
        deductible = record.deductible_amount or 0.0
        reimbursement = max(0.0, (record.estimated_repair_cost or 0.0) - deductible)
        ref = _generate_payment_ref()
        timeline = 3 if is_auto else 7

        record.reimbursement_amount = reimbursement
        record.reimbursement_reference = ref
        record.payment_timeline_days = timeline
        record.reimbursement_status = "initiated"

        prompt = _APPROVAL_PROMPT_TEMPLATE.format(
            policyholder_name=record.policyholder_name,
            claim_id=record.claim_id,
            claim_type=record.claim_type,
            claim_category=record.claim_category,
            total=record.estimated_repair_cost or 0.0,
            deductible=deductible,
            reimbursement=reimbursement,
            ref=ref,
            timeline=timeline,
            decision_type="automated approval" if is_auto else "approved by human adjuster",
        )
    else:
        record.reimbursement_amount = 0.0
        record.reimbursement_reference = ""
        record.payment_timeline_days = 0
        record.reimbursement_status = "on_hold"

        prompt = _DENIAL_PROMPT_TEMPLATE.format(
            policyholder_name=record.policyholder_name,
            claim_id=record.claim_id,
            claim_type=record.claim_type,
            claim_category=record.claim_category,
            total=record.estimated_repair_cost or 0.0,
            decision=record.final_decision,
            reason=record.decision_reasoning,
        )

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(role=MessageRole.USER, content=prompt)]
        ),
    )
    if run.status == "failed":
        raise RuntimeError(f"Reimbursement agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    record.notice_letter = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    return record
