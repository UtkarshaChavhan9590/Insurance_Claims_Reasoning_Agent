# Insurance Claims Intelligence Agent
### Multi-agent claims processing system built on Microsoft Foundry

A fully interactive insurance claims processing system covering **Auto, Home, Health, and Travel** categories. Demonstrates **knowledge-grounded multi-step reasoning**, **agentic retrieval (Azure AI Search)**, **human-in-the-loop approval gates**, **compliance checking**, **appeals handling**, and **concurrent multi-agent orchestration** — all using the **Foundry Agents API** exclusively. Pure terminal chatbot backed by eight Foundry hosted agents.

---

## Architecture — 7-stage, 8-agent pipeline

```
  python chatbot.py               python chatbot.py --claim file.json
       |                                         |
       v                                         v
 Claim Builder (guided prompts)         Claim JSON (4 categories)
       |                                         |
       +--------------------+-------------------+
                            | raw claim dict
                            v
               AgentRegistry (startup)
               ONE AgentsClient
               EIGHT Foundry hosted agents
               created once, reused per claim
                            |
                            v
        [1/7]  Intake Agent           Foundry: extract + LLM summary
                            |
        [2/7]  Document Validation    deterministic per-category check
                            |
              +-------------+-------------+
   [3/7]      |                           |   CONCURRENT (asyncio.gather)
              v                           v
     Policy Reasoning Agent     Fraud Detection Agent
     Foundry + AzureAISearch    Foundry: 5-step indicator check
              |                           |
              +-------------+-------------+
                            | merged ClaimRecord
        [4/7]  Decision Agent + HITL gate
                            |
         +------------------+------------------+
         v                                     v
   auto-approve (amount<2000,           HITL pause (terminal)
    no fraud, no missing docs)          adjuster: approve/deny/request_more_info
         |                                     |
         +------------------+------------------+
                            |
        [5/7]  Compliance Agent    Foundry + KB: regulatory gate
                                   CAN override decision to escalate
                            |
        [6/7]  Reimbursement Agent  deductible calc + notice letter
                            |
        [7/7]  Claims Summarizer    executive summary -> checkpoints/
```

**Orchestration patterns:**
| Pattern | Where |
|---|---|
| Sequential | Stages 1 -> 2 -> 4 -> 5 -> 6 -> 7 |
| Concurrent | Stage 3: Policy Reasoning + Fraud Detection via asyncio.gather |
| HITL checkpoint/resume | Stage 4: pauses to terminal; --resume continues from checkpoint |
| Agent override | Stage 5: Compliance Agent can escalate a previously approved decision |
| Handoff | Stage 6: Reimbursement Agent receives final decision, drafts claimant letter |
| Appeals | --appeal flag: counterfactual re-evaluation by dedicated Appeals Agent |

---

## Agent roster

| Agent | Stage | LLM | KB | Reasoning style |
|---|---|---|---|---|
| Intake Agent | 1 | yes | - | Summarise + flag anomalies |
| Document Validation | 2 | - | - | Deterministic rules (no LLM) |
| Policy Reasoning | 3 concurrent | yes | Azure AI Search | KB-grounded coverage check |
| Fraud Detection | 3 concurrent | yes | - | 5-step indicator checklist |
| Decision Agent | 4 | yes | - | Chain-of-thought recommendation |
| Compliance Agent | 5 | yes | Azure AI Search | Regulatory citation + override |
| Reimbursement Agent | 6 | yes | - | Deductible calc + notice letter |
| Claims Summarizer | 7 | yes | - | Full-pipeline audit trail |
| Appeals Agent | appeal flow | yes | - | Counterfactual re-evaluation |

---

## Foundry agent pattern (every LLM agent)

```python
# Created ONCE at startup -- reused across claims
agent_id = await agents_client.create_agent(
    model="gpt-4o", name="...", instructions="..."
)

# Fresh thread per claim invocation
run = await agents_client.create_thread_and_process_run(
    agent_id=agent_id,
    thread=AgentThreadCreationOptions(messages=[
        ThreadMessageOptions(role=MessageRole.USER, content=prompt)
    ])
)

reply = await agents_client.messages.get_last_message_text_by_role(
    thread_id=run.thread_id, role=MessageRole.AGENT
)
await agents_client.threads.delete(thread_id=run.thread_id)
```

The **Policy Reasoning** and **Compliance** agents additionally have `AzureAISearchTool` attached --
Foundry automatically retrieves grounding context before reasoning.

---

## Setup

### Prerequisites
- Azure subscription with a **Microsoft Foundry project** (model deployment, e.g. `gpt-4o`)
- An **Azure AI Search service** (Basic tier+)
- The AI Search service registered as a **Connected Resource** in your Foundry project
- Azure CLI logged in (`az login`)
- Python 3.10+

### Install dependencies
```bash
pip install -r requirements.txt
```

### Configure environment
```bash
cp .env.example .env
# Edit .env -- fill in your Foundry endpoint, model name, and Search details
```

**Environment variables:**

| Variable | Description |
|---|---|
| FOUNDRY_PROJECT_ENDPOINT | Your Foundry project endpoint URL |
| FOUNDRY_MODEL_DEPLOYMENT | Model deployment name, e.g. gpt-4o |
| AZURE_SEARCH_ENDPOINT | Your Azure AI Search service URL |
| AZURE_SEARCH_CONNECTION_ID | Foundry portal -> Project settings -> Connected resources |
| AZURE_SEARCH_INDEX_NAME | Search index name (default: insurance-policy-index) |
| AZURE_SEARCH_ADMIN_KEY | Search admin key for document upload |
| CLAIM_AUTO_APPROVE_LIMIT | INR threshold for auto-approval (default: 2000) |

### Set up the knowledge base
Indexes `sample_data/auto_policy_manual.md` and `sample_data/claims_precedents.md` into Azure AI Search.
```bash
python foundry_iq/setup_knowledge_base.py
```

> **Note:** If `AZURE_SEARCH_CONNECTION_ID` is not set or invalid, the Policy Reasoning and Compliance
> agents run without KB grounding -- they degrade gracefully instead of crashing.

---

## Running the chatbot

### Interactive guided form
```bash
python chatbot.py
python chatbot.py --category auto
python chatbot.py --category health
python chatbot.py --category home
python chatbot.py --category travel
```

### Submit a pre-built claim JSON
```bash
# Auto claim -- auto-approve path (all docs present)
python chatbot.py --claim sample_data/sample_claim_auto.json

# Health claim -- auto-approve path
python chatbot.py --claim sample_data/sample_claim_health.json

# Home claim -- HITL path (missing contractor estimate)
python chatbot.py --claim sample_data/sample_claim_home.json

# Travel claim -- HITL path (missing cancellation confirmation)
python chatbot.py --claim sample_data/sample_claim_travel.json
```

### Resume a paused HITL claim
```bash
python chatbot.py --resume CLM-2026-XXXXX
# Enter: approve | deny | request_more_info
```

### File an appeal on a denied claim
```bash
python chatbot.py --appeal CLM-2026-XXXXX
# Describe new evidence; Appeals Agent performs counterfactual re-evaluation
```

---

## Demo script

1. **Auto-approve path**: `--claim sample_data/sample_claim_auto.json` -- all 7 stages, KB citations, reimbursement notice with PAY reference
2. **HITL path**: `--claim sample_data/sample_claim_home.json` -- pipeline pauses; enter `deny` -- see denial letter with policy citation
3. **Resume**: `--resume CLM-2026-XXXXX` -- show checkpoint/resume pattern
4. **Appeals**: `--appeal CLM-2026-XXXXX` -- counterfactual reasoning: *"Does this new evidence change the outcome?"*
5. **Interactive**: `--category health` -- fill in a claim live; show sub-type picker and doc checklist
6. **Foundry portal**: show all 8 hosted agents created under your project

---

## Project structure

```
chatbot.py                    <- terminal entry point
agents/
    models.py                 <- ClaimRecord dataclass (all pipeline fields)
    intake_agent.py           <- Foundry agent: extract + summarise
    document_agent.py         <- deterministic doc validation (no LLM)
    policy_agent.py           <- Foundry agent + AzureAISearchTool
    fraud_agent.py            <- Foundry agent: 5-step fraud assessment
    decision_agent.py         <- Foundry agent: chain-of-thought + HITL gate
    compliance_agent.py       <- Foundry agent + KB: regulatory gate (can override)
    reimbursement_agent.py    <- Foundry agent: deductible calc + notice letter
    summarizer_agent.py       <- Foundry agent: executive summary to checkpoints/
    appeals_agent.py          <- Foundry agent: counterfactual appeal review
orchestration/
    workflow.py               <- AgentRegistry + 7-stage pipeline
    claim_builder.py          <- interactive guided terminal form
    claim_templates.py        <- per-category doc checklists + fields
foundry_iq/
    setup_knowledge_base.py   <- index policy docs into Azure AI Search
sample_data/
    auto_policy_manual.md     <- policy knowledge (all 4 categories)
    claims_precedents.md      <- historical claim precedents
    sample_claim_auto.json
    sample_claim_health.json
    sample_claim_home.json
    sample_claim_travel.json
checkpoints/                  <- checkpoint JSON per claim (HITL pause + summary)
```
