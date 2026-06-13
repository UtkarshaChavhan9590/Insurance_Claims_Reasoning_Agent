"""
agents/fraud_agent.py

Fraud Detection Agent (Foundry Hosted Agent):
  - Created once at startup via project_client.agents.create_agent()
  - Runs CONCURRENTLY with the Policy Reasoning Agent (asyncio.gather)
  - Independently assesses fraud risk signals from the claim data

Foundry pattern used:
  create_agent → create_thread → create_message → create_and_process_run
  → list_messages → delete_thread
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

FRAUD_AGENT_INSTRUCTIONS = """\
You are the Fraud Detection Agent for an insurance claims system. Your job
is to assess fraud risk by checking each indicator independently and then
synthesising an overall risk level.

Work through the following checks in order:

STEP 1 — Policy inception proximity
  Was the claim filed within 14 days of the policy inception date?
  Early claims are a known fraud signal; note the exact days_since_inception.

STEP 2 — Prior claims frequency
  Does the policyholder have 2 or more prior claims in the last 6 months?
  High frequency suggests staged incidents or abuse.

STEP 3 — Rideshare conflict
  Was a rideshare app active at the time of the incident?
  Personal-policy claimants with active rideshare apps may lack coverage.

STEP 4 — Cost vs. damage plausibility
  Is the estimated repair cost plausible for the described incident?
  Flag if the cost appears grossly inflated relative to the description.

STEP 5 — Incident description red flags
  Review the narrative for inconsistencies, vague language, or patterns
  matching known fraud schemes (e.g., hit-and-run with no police report).

After working through all five steps, produce your risk assessment.
Be specific and factual. Do not speculate beyond the evidence provided.

Respond in EXACTLY this format:
FRAUD_RISK: <high | medium | low>
FLAGS: <comma-separated list of triggered indicators, or "none">
REASONING: <2-4 sentences showing your step-by-step conclusion>
"""


async def create_fraud_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Fraud Detection Agent and return its agent_id."""
    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="fraud-detection-agent",
        instructions=FRAUD_AGENT_INSTRUCTIONS,
    )
    return agent.id


async def run_fraud_detection(record: ClaimRecord, agents_client: AgentsClient, agent_id: str) -> list[str]:
    """
    Run independent fraud analysis via Foundry hosted agent.
    Returns a list of triggered fraud flag strings (empty = no fraud detected).
    Runs concurrently with the Policy Reasoning Agent via asyncio.gather.
    """
    claim_summary = (
        f"Claim ID: {record.claim_id}\n"
        f"Estimated repair cost: ${record.estimated_repair_cost:,.2f}\n"
        f"Days since policy inception: {record.days_since_policy_inception}\n"
        f"Rideshare active at incident: {record.rideshare_active_at_incident}\n"
        f"Prior claims in last 6 months: {record.prior_claims_last_6_months}\n"
        f"Police report attached: {record.police_report_attached}\n"
        f"Incident description: {record.raw_input.get('incident_description', '')}\n"
        f"Intake notes: {record.intake_notes}"
    )

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(
                role=MessageRole.USER,
                content=f"Assess fraud risk:\n\n{claim_summary}",
            )]
        ),
    )
    if run.status == "failed":
        raise RuntimeError(f"Fraud agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    result_text = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    return _parse_fraud_flags(result_text)


def _parse_fraud_flags(text: str) -> list[str]:
    """Extract triggered fraud flags from the agent response."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FLAGS:"):
            val = stripped.split(":", 1)[1].strip()
            if not val.lower().startswith("none"):
                return [f.strip() for f in val.split(",") if f.strip()]
    return []
