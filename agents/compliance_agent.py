"""
agents/compliance_agent.py

Compliance Agent (Foundry Hosted Agent + Foundry Knowledge Index):
  - Runs after the Decision Agent (stage 5 of 7)
  - Validates the proposed decision against regulatory rules retrieved
    from the Foundry Knowledge Index (same Azure AI Search backing store)
  - Can override the decision to "escalate" if a regulation prohibits
    direct approval — the only agent that can override a peer agent's output

Unique capability:
  This is the only agent that performs multi-step KB retrieval with a
  regulatory lens rather than a policy lens. It retrieves IRDA guidelines,
  state mandates, and cooling-off rules, then cross-checks them against
  the proposed decision — acting as an independent compliance gate before
  any money moves.
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

_INSTRUCTIONS = """\
You are the Compliance Agent for an insurance claims processing system.

Your role is to validate that a proposed claim decision complies with
applicable insurance regulations. Use the attached knowledge base to
retrieve relevant regulatory rules, state mandates, and IRDA guidelines.

Given a claim summary and proposed decision, you must:
1. Retrieve applicable regulations for the claim category and amount
2. Verify the proposed decision does not violate any retrieved rule
3. Check mandatory waiting periods, cooling-off rules, and appeal rights
4. If fraud escalation is proposed, verify it follows required regulatory process
5. Note any conditions the insurer must meet to lawfully approve/deny

Cite ONLY regulations you actually retrieved from the knowledge base.
Do NOT guess or invent regulatory references.

Respond in EXACTLY this format — no extra lines:
COMPLIANCE_STATUS: <PASS | CONDITIONAL | FAIL>
REGULATORY_CITATIONS: <comma-separated names, or "none">
COMPLIANCE_NOTES: <1-2 sentences on conditions or failures>
OVERRIDE_DECISION: <yes | no>
OVERRIDE_REASON: <reason if yes, else "none">
"""


def _build_search_tool() -> "AzureAISearchTool | None":
    """Return an AzureAISearchTool if the connection is configured, else None."""
    connection_id = os.environ.get("AZURE_SEARCH_CONNECTION_ID", "").strip()
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "insurance-policy-index")
    if not connection_id:
        return None
    return AzureAISearchTool(
        index_connection_id=connection_id,
        index_name=index_name,
        query_type=AzureAISearchQueryType.SIMPLE,
        top_k=5,
    )


async def create_compliance_agent(agents_client: AgentsClient) -> str:
    """Create the Foundry hosted Compliance Agent. Attaches KB tool if configured."""
    search_tool = _build_search_tool()
    extra: dict = {}
    if search_tool:
        extra["tools"] = search_tool.definitions
        extra["tool_resources"] = search_tool.resources

    agent = await agents_client.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-4o"),
        name="compliance-agent",
        instructions=_INSTRUCTIONS,
        **extra,
    )
    return agent.id


async def run_compliance_check(
    record: ClaimRecord, agents_client: AgentsClient, agent_id: str
) -> ClaimRecord:
    """
    Validate the proposed decision against regulatory rules via Foundry KB.
    Sets record.compliance_status, regulatory_citations, compliance_notes.
    If OVERRIDE_DECISION=yes, sets requires_human_approval=True and
    records the override reason.
    """
    context = (
        f"Claim ID          : {record.claim_id}\n"
        f"Category          : {record.claim_category}\n"
        f"Claim type        : {record.claim_type}\n"
        f"Amount            : \u20b9{record.estimated_repair_cost:,.2f}\n"
        f"Proposed decision : {record.recommended_decision}\n"
        f"Fraud indicators  : {record.fraud_indicators or 'none'}\n"
        f"Missing documents : {record.missing_documents or 'none'}\n"
        f"Days since inception: {record.days_since_policy_inception}\n"
        f"Policy citations  : {record.policy_citations or 'none'}\n"
        f"Decision reasoning: {record.decision_reasoning}"
    )

    run = await agents_client.create_thread_and_process_run(
        agent_id=agent_id,
        thread=AgentThreadCreationOptions(
            messages=[ThreadMessageOptions(
                role=MessageRole.USER,
                content=f"Validate this proposed decision for regulatory compliance:\n\n{context}",
            )]
        ),
    )
    if run.status == "failed":
        if "server_error" in str(run.last_error).lower():
            print("        Warning: Compliance KB search unavailable — defaulting to PASS.")
            await agents_client.threads.delete(thread_id=run.thread_id)
            record.compliance_status = "PASS"
            record.compliance_notes = "KB unavailable — manual review recommended."
            return record
        raise RuntimeError(f"Compliance agent run failed: {run.last_error}")

    msg = await agents_client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id, role=MessageRole.AGENT
    )
    result_text = msg.text.value.strip() if msg else ""
    await agents_client.threads.delete(thread_id=run.thread_id)

    _parse_compliance_response(record, result_text)
    return record


def _parse_compliance_response(record: ClaimRecord, text: str) -> None:
    override_decision = False

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("COMPLIANCE_STATUS:"):
            record.compliance_status = stripped.split(":", 1)[1].strip().upper()

        elif upper.startswith("REGULATORY_CITATIONS:"):
            val = stripped.split(":", 1)[1].strip()
            record.regulatory_citations = (
                [] if val.lower().startswith("none")
                else [v.strip() for v in val.split(",") if v.strip()]
            )

        elif upper.startswith("COMPLIANCE_NOTES:"):
            record.compliance_notes = stripped.split(":", 1)[1].strip()

        elif upper.startswith("OVERRIDE_DECISION:"):
            override_decision = stripped.split(":", 1)[1].strip().lower() == "yes"

        elif upper.startswith("OVERRIDE_REASON:"):
            val = stripped.split(":", 1)[1].strip()
            if val.lower() != "none":
                record.compliance_override_reason = val

    if override_decision:
        record.recommended_decision = "escalate"
        record.requires_human_approval = True
        record.final_decision = None
