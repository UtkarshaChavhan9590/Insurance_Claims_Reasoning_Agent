"""
agents/policy_agent.py

Policy Reasoning Agent (Foundry Hosted Agent with Azure AI Search tool):
  - Created once at startup with an Azure AI Search connection attached
  - Foundry handles grounded retrieval automatically via the search tool
  - Runs CONCURRENTLY with the Fraud Detection Agent (asyncio.gather)

Foundry pattern used:
  create_agent (with AzureAISearchTool) → create_thread → create_message
  → create_and_process_run → list_messages → delete_thread
"""

import os

from azure.ai.agents.aio import AgentsClient
from azure.ai.agents.models import (
    AgentThreadCreationOptions,
    AzureAISearchQueryType,
    AzureAISearchTool,
    MessageRole,
    ThreadMessageOptions,
)
from dotenv import load_dotenv

from agents.models import ClaimRecord

load_dotenv()

POLICY_AGENT_INSTRUCTIONS = """\
You are the Policy Reasoning Agent for an insurance claims system.

Use the attached Azure AI Search knowledge base to retrieve relevant excerpts
from the policy manual and historical claim precedents. Use ONLY these excerpts
to answer; do not guess.

Given a structured claim summary, you must:
1. Determine which coverage section applies (collision, comprehensive, fire, hospitalization, etc.)
2. Check for any exclusions that apply (cite the specific section)
3. Check for fraud indicators per the policy's fraud indicator list
4. Note the approval authority limit that applies to this claim amount

Document validation has already been performed by a separate agent — do NOT
repeat or modify the missing_documents field.

ALWAYS cite the specific policy section or precedent you are relying on.
Be precise and conservative — if the excerpts don't clearly support a
conclusion, say so rather than guessing.

Respond in this exact format:
COVERAGE: <which section applies>
EXCLUSIONS: <any exclusions that apply, or "none found">
FRAUD_INDICATORS: <short comma-separated list of indicator names only, e.g. "filed within 14 days of inception" — or "none">
APPROVAL_AUTHORITY: <what the policy says about who can approve this amount>
CITATIONS: <list the specific sections/precedents referenced>
"""


def _build_search_tool() -> "AzureAISearchTool | None":
    """Return an AzureAISearchTool if the connection is configured, else None."""
    connection_id = os.environ.get("AZURE_SEARCH_CONNECTION_ID", "").strip()
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "insurance-policy-index")
    if not connection_id:
        return None
    # Use SIMPLE (works on all tiers); SEMANTIC requires Standard+
    return AzureAISearchTool(
        index_connection_id=connection_id,
        index_name=index_name,
        query_type=AzureAISearchQueryType.SIMPLE,
        top_k=5,
    )


async def create_policy_agent(agents_client: AgentsClient) -> str:
    """
    Create the Foundry hosted Policy Reasoning Agent.
    Attaches Azure AI Search tool if AZURE_SEARCH_CONNECTION_ID is configured.
    Returns the agent_id.
    """
    search_tool = _build_search_tool()
    extra: dict = {}
    if search_tool:
        extra["tools"] = search_tool.definitions
        extra["tool_resources"] = search_tool.resources

    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="policy-reasoning-agent",
        instructions=POLICY_AGENT_INSTRUCTIONS,
        **extra,
    )
    return agent.id


async def run_policy_reasoning(record: ClaimRecord, agents_client: AgentsClient, agent_id: str) -> ClaimRecord:
    """
    Retrieve KB context via the Foundry Search tool and run the Policy Reasoning Agent.
    Runs concurrently with the Fraud Detection Agent via asyncio.gather.
    """
    claim_summary = (
        f"Claim ID: {record.claim_id}\n"
        f"Category: {record.claim_category}\n"
        f"Claim type: {record.claim_type}\n"
        f"Estimated repair cost: \u20b9{record.estimated_repair_cost:,.2f}\n"
        f"Documents attached: {', '.join(record.documents_attached) or 'none'}\n"
        f"Missing documents (pre-validated): {', '.join(record.missing_documents) or 'none'}\n"
        f"Police report attached: {record.police_report_attached}\n"
        f"Rideshare active at incident: {record.rideshare_active_at_incident}\n"
        f"Days since policy inception: {record.days_since_policy_inception}\n"
        f"Prior claims in last 6 months: {record.prior_claims_last_6_months}\n"
        f"Intake notes: {record.intake_notes}"
    )

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(
                role=MessageRole.USER,
                content=f"Assess the following claim against the policy knowledge base:\n\n{claim_summary}",
            )]
        ),
    )
    if run.status == "failed":
        if "server_error" in str(run.last_error).lower():
            print("        Warning: Policy KB search unavailable — running without KB.")
            await agents_client.threads.delete(thread_id=run.thread_id)
            return record
        raise RuntimeError(f"Policy agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    result_text = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    _parse_policy_response(record, result_text)
    return record


def _parse_policy_response(record: ClaimRecord, text: str) -> None:
    """Lightweight parser for the structured agent response."""
    record.coverage_assessment = text  # keep full text for traceability

    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("FRAUD_INDICATORS:"):
            val = line.split(":", 1)[1].strip()
            record.fraud_indicators = (
                [] if val.lower().startswith("none")
                else [val]
            )
        elif line.upper().startswith("CITATIONS:"):
            val = line.split(":", 1)[1].strip()
            record.policy_citations = [v.strip() for v in val.split(",") if v.strip()]
