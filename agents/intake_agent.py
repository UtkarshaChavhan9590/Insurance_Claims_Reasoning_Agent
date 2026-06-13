"""
agents/intake_agent.py

Intake Agent (Foundry Hosted Agent):
  - Created once at startup via project_client.agents.create_agent()
  - Each claim gets a fresh thread; the agent summarises the raw submission
  - Does NOT make coverage decisions — extracts fields and flags anomalies

Foundry pattern used:
  create_agent → create_thread → create_message → create_and_process_run
  → list_messages → delete_thread
"""

import json
import os
from datetime import datetime

from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import (
    AgentThreadCreationOptions,
    MessageRole,
    ThreadMessageOptions,
)
from dotenv import load_dotenv

from agents.models import ClaimRecord

load_dotenv()

INTAKE_INSTRUCTIONS = """\
You are the Intake Agent for an insurance claims processing system.

Your job is to review a raw claim submission and produce a short, clear
summary of the claim for downstream reasoning agents. Focus on:
- Briefly restating the incident in one or two sentences
- Noting anything unusual or ambiguous about the submission
- Flagging if any obviously required field looks missing or inconsistent

Do NOT make a coverage decision. Do NOT speculate about fraud. Just
summarize and flag anomalies factually. Keep your response under 100 words.
"""


async def create_intake_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Intake Agent and return its agent_id."""
    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="intake-agent",
        instructions=INTAKE_INSTRUCTIONS,
    )
    return agent.id


async def run_intake(claim_input: dict, agents_client: AgentsClient, agent_id: str) -> ClaimRecord:
    """
    Run the Intake Agent over a raw claim dict and return a populated ClaimRecord.
    Uses Foundry Agents API: thread → message → run → collect reply.
    """
    record = ClaimRecord(claim_id=claim_input["claim_id"], raw_input=claim_input)

    # Deterministic field extraction — no LLM needed for structured fields
    record.policyholder_name = claim_input.get("policyholder_name")
    record.claim_type = claim_input.get("claim_type")
    record.estimated_repair_cost = claim_input.get("estimated_repair_cost")
    record.documents_attached = claim_input.get("documents_attached", [])
    record.police_report_attached = claim_input.get("police_report_attached", False)
    record.rideshare_active_at_incident = claim_input.get("rideshare_active_at_incident", False)
    record.prior_claims_last_6_months = claim_input.get("prior_claims_last_6_months", 0)

    inception = datetime.fromisoformat(claim_input["policy_inception_date"])
    filed = datetime.fromisoformat(claim_input["claim_filed_date"])
    record.days_since_policy_inception = (filed - inception).days

    # Foundry Agents API: create thread + message + run atomically, then get reply
    prompt = (
        f"Claim submission:\n{json.dumps(claim_input, indent=2)}\n\n"
        f"Days since policy inception: {record.days_since_policy_inception}"
    )
    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(role=MessageRole.USER, content=prompt)]
        ),
    )
    if run.status == "failed":
        raise RuntimeError(f"Intake agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    record.intake_notes = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    return record
