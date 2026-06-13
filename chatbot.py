"""
chatbot.py

Terminal chatbot entry point for the Insurance Claims Intelligence Agent.

Usage:
  python chatbot.py                          # guided interactive claim form
  python chatbot.py --claim <file.json>      # submit a pre-built claim JSON
  python chatbot.py --category auto|home|health|travel
  python chatbot.py --resume <CLM-ID>        # resume a paused HITL claim
  python chatbot.py --appeal <CLM-ID>        # appeal a denied/escalated claim

Features:
  - Box-drawing terminal UI (╔═╗║╚╝) for clear information hierarchy
  - HITL (Human-in-the-Loop): full claim context box before decision prompt
  - Appeals flow: multiline evidence input, counterfactual routing
  - Executive summary display from Claims Summarizer Agent
  - 8 Foundry hosted agents initialised once at startup

Run `az login` first, or set AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID.
"""

import asyncio
import json
import sys
import textwrap
from typing import Optional

from dotenv import load_dotenv

from orchestration.claim_builder import build_claim_interactive
from orchestration.workflow import (
    AgentRegistry,
    load_checkpoint,
    process_appeal,
    process_claim,
    resume_claim,
    save_checkpoint,
)

load_dotenv()

# ── UI constants ──────────────────────────────────────────────────────────────
_W = 66       # inner content width inside box
_BAR = "━" * 70
_HITL_OPTIONS = ("approve", "deny", "request_more_info")


# ── Box-drawing helpers ───────────────────────────────────────────────────────

def _banner() -> None:
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  ◆  Insurance Claims Intelligence Agent".ljust(68) + "║")
    print("║" + "     Powered by Microsoft Foundry  ·  8 Hosted Agents".ljust(68) + "║")
    print("║" + " " * 68 + "║")
    print("╠" + "═" * 68 + "╣")
    print("║" + "  ◈  FOUNDRY IQ  ─  Agentic Knowledge Retrieval".ljust(68) + "║")
    print("║" + "     Grounded multi-step reasoning  ·  Citation-enforced answers".ljust(68) + "║")
    print("║" + "     Policy KB  +  Regulatory KB  ·  Reduces hallucination".ljust(68) + "║")
    print("╠" + "═" * 68 + "╣")
    for line in [
        "  Pipeline  ▶  [1] Intake  ▶  [2] Docs  ▶  [3] Policy ⇄ Fraud",
        "            ▶  [4] Decision + HITL  ▶  [5] Compliance ◈",
        "            ▶  [6] Reimbursement   ▶  [7] Executive Summary",
    ]:
        print("║" + line.ljust(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")
    print()


def _box_lines(rows: list[tuple[str, str]], title: str = "") -> None:
    """Print a labelled 2-column box."""
    print("┌" + ("─ " + title + " ").ljust(68, "─") + "┐")
    for label, value in rows:
        wrapped = textwrap.wrap(str(value), width=_W - len(label) - 4) or [""]
        first = True
        for chunk in wrapped:
            if first:
                line = f"  {label:<22} {chunk}"
                first = False
            else:
                line = f"  {' ':<22} {chunk}"
            print("│" + line.ljust(68) + "│")
    print("└" + "─" * 68 + "┘")


def _section_header(text: str) -> None:
    print(f"\n{_BAR}")
    print(f"  ◆  {text}")
    print(_BAR)


# ── Missing document update ───────────────────────────────────────────────────

def _offer_document_update(record) -> None:
    """
    If missing documents exist, let the reviewer mark any as now provided
    before recording their HITL decision.
    Updates record in-place and saves the checkpoint.
    """
    if not record.missing_documents:
        return

    print()
    print("\u250c\u2500 Missing Documents " + "\u2500" * 49 + "\u2510")
    for i, doc in enumerate(record.missing_documents, 1):
        print(f"\u2502  [{i}] {doc.ljust(63)}\u2502")
    print("\u2514" + "\u2500" * 68 + "\u2518")

    ans = input("  Have any of these documents now been provided? (yes/no): ").strip().lower()
    if ans not in ("yes", "y"):
        return

    print(f"  Enter document numbers (comma-separated, e.g. 1,2), or ALL: ")
    raw = input("  > ").strip()

    missing = record.missing_documents
    if raw.strip().upper() == "ALL":
        provided_indices = set(range(len(missing)))
    else:
        provided_indices = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(missing):
                    provided_indices.add(idx)

    if not provided_indices:
        print("  No valid selections made.")
        return

    newly_provided = [missing[i] for i in sorted(provided_indices)]
    record.documents_attached = list(record.documents_attached) + newly_provided
    record.missing_documents = [d for i, d in enumerate(missing) if i not in provided_indices]

    print(f"\n  \u2713  Documents marked as provided: {', '.join(newly_provided)}")
    if not record.missing_documents:
        print("  All previously missing documents are now on file.")
    else:
        print(f"  Still missing: {', '.join(record.missing_documents)}")

    save_checkpoint(record)
    print()


# ── HITL display ──────────────────────────────────────────────────────────────

def _display_hitl_pause(record) -> None:
    _section_header(f"HUMAN REVIEW REQUIRED  ─  {record.claim_id}")
    _box_lines([
        ("Policyholder",     record.policyholder_name or "n/a"),
        ("Category",         f"{record.claim_category} / {record.claim_subtype}"),
        ("Claimed Amount",   f"\u20b9{record.estimated_repair_cost:,.2f}"),
        ("Deductible",       f"\u20b9{record.deductible_amount:,.2f}"),
        ("Days since start", str(record.days_since_policy_inception or "n/a")),
        ("Prior claims",     str(record.prior_claims_last_6_months)),
    ], title="Claim Identity")
    print()
    _box_lines([
        ("AI Recommendation",  record.recommended_decision or "n/a"),
        ("Reasoning",          record.decision_reasoning or "n/a"),
        ("Compliance status",  record.compliance_status or "not checked"),
        ("Compliance notes",   record.compliance_notes or "none"),
        ("Override reason",    record.compliance_override_reason or "none"),
    ], title="AI Decision Summary")
    print()
    _box_lines([
        ("Missing documents", ", ".join(record.missing_documents) if record.missing_documents else "none"),
        ("Fraud indicators",  ", ".join(record.fraud_indicators) if record.fraud_indicators else "none"),
        ("Policy citations",  ", ".join(record.policy_citations) if record.policy_citations else "none"),
        ("Reg. citations",    ", ".join(record.regulatory_citations) if record.regulatory_citations else "none"),
    ], title="Risk Signals")
    print()


def _get_hitl_decision() -> str:
    print("┌─ Your Decision " + "─" * 52 + "┐")
    opts = [
        ("1", "✓", "approve",           "Approve and proceed to reimbursement"),
        ("2", "✗", "deny",              "Deny the claim"),
        ("3", "◉", "request_more_info", "Request more information from claimant"),
    ]
    for num, icon, key, desc in opts:
        print(f"│  [{num}] {icon}  {key:<20} {desc:<38}│")
    print("└" + "─" * 68 + "┘")
    key_map = {"1": "approve", "2": "deny", "3": "request_more_info"}
    while True:
        raw = input("  Enter 1 / 2 / 3 or full option name: ").strip().lower()
        if raw in key_map:
            return key_map[raw]
        if raw in ("approve", "deny", "request_more_info"):
            return raw
        print("  ✗  Invalid — enter 1, 2, or 3")


# ── Appeals display ───────────────────────────────────────────────────────────

def _display_appeal_context(record) -> None:
    _section_header(f"APPEAL REVIEW  ─  {record.claim_id}")
    _box_lines([
        ("Policyholder",    record.policyholder_name or "n/a"),
        ("Category",        f"{record.claim_category} / {record.claim_subtype}"),
        ("Claimed Amount",  f"\u20b9{record.estimated_repair_cost:,.2f}"),
        ("Original outcome",record.final_decision or record.recommended_decision or "n/a"),
        ("Decision reason", record.decision_reasoning or "n/a"),
        ("Fraud flags",     ", ".join(record.fraud_indicators) if record.fraud_indicators else "none"),
        ("Missing docs",    ", ".join(record.missing_documents) if record.missing_documents else "none"),
    ], title="Original Claim Context")
    print()


def _get_evidence_input() -> str:
    print("  Enter new evidence / additional information.")
    print("  Type each line and press Enter. Type DONE on its own line to finish.\n")
    lines = []
    while True:
        line = input("  > ")
        if line.strip().upper() == "DONE":
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ── Outcome display ───────────────────────────────────────────────────────────

def _decision_badge(decision: str) -> str:
    """Return a symbol-prefixed decision label for section headers."""
    d = decision.upper()
    if "APPROV" in d:
        return f"✓  {d}"
    if "DENY" in d or "DENIED" in d:
        return f"✗  {d}"
    if "ESCALAT" in d:
        return f"◉  {d}"
    return f"▶  {d}"


def _display_outcome(record) -> None:
    decision = (record.final_decision or record.recommended_decision or "pending").upper()
    _section_header(f"OUTCOME  ─  {record.claim_id}  ─  {_decision_badge(decision)}")

    if record.reimbursement_status == "initiated":
        _box_lines([
            ("Final Decision",   decision),
            ("Approved Amount",  f"\u20b9{record.reimbursement_amount:,.2f}"),
            ("Reference",        record.reimbursement_reference),
            ("Timeline",         f"{record.payment_timeline_days} business days"),
            ("Compliance",       record.compliance_status or "n/a"),
        ], title="Financial Result")
    else:
        _box_lines([
            ("Final Decision",  decision),
            ("Compliance",      record.compliance_status or "n/a"),
        ], title="Result")

    if record.notice_letter:
        print()
        print("┌─ Notice Letter " + "─" * 52 + "┐")
        for para in record.notice_letter.splitlines():
            for chunk in (textwrap.wrap(para, _W) if para.strip() else [""]):
                print("│  " + chunk.ljust(_W + 2) + "│")
        print("└" + "─" * 68 + "┘")

    if record.executive_summary:
        print()
        print("┌─ Executive Summary " + "─" * 48 + "┐")
        for para in record.executive_summary.splitlines():
            for chunk in (textwrap.wrap(para, _W) if para.strip() else [""]):
                print("│  " + chunk.ljust(_W + 2) + "│")
        print("└" + "─" * 68 + "┘")
    print()


# ── Flow handlers ─────────────────────────────────────────────────────────────

async def _handle_new_claim(registry: AgentRegistry, claim_input: dict) -> None:
    record = await process_claim(claim_input, registry)

    if record.requires_human_approval:
        _display_hitl_pause(record)
        _offer_document_update(record)
        decision = _get_hitl_decision()
        print(f"\n  ◉  Decision recorded: {decision.upper()}\n")
        record = await resume_claim(record.claim_id, decision, registry)

        # After HITL, compliance may have re-triggered another HITL
        if record.requires_human_approval:
            print("\n  ◉  Compliance override requires a second review.\n")
            _display_hitl_pause(record)
            _offer_document_update(record)
            decision = _get_hitl_decision()
            record = await resume_claim(record.claim_id, decision, registry)

    _display_outcome(record)


async def _handle_resume(registry: AgentRegistry, claim_id: str) -> None:
    try:
        record = load_checkpoint(claim_id)
    except FileNotFoundError:
        print(f"\n  ✗  No checkpoint found for claim {claim_id}")
        return
    _display_hitl_pause(record)
    _offer_document_update(record)
    decision = _get_hitl_decision()
    print(f"\n  ◉  Decision recorded: {decision.upper()}\n")
    record = await resume_claim(claim_id, decision, registry)
    _display_outcome(record)


async def _handle_appeal(registry: AgentRegistry, claim_id: str) -> None:
    try:
        record = load_checkpoint(claim_id)
    except FileNotFoundError:
        print(f"\n  ✗  No checkpoint found for claim {claim_id}")
        return
    _display_appeal_context(record)
    evidence = _get_evidence_input()
    if not evidence:
        print("\n  ✗  No evidence provided — appeal cancelled.")
        return
    print()
    record = await process_appeal(claim_id, evidence, registry)

    if record.requires_human_approval:
        print("\n  Appeals Agent recommends human review for this case.")
        _display_hitl_pause(record)
        decision = _get_hitl_decision()
        record = await resume_claim(claim_id, decision, registry)

    _display_outcome(record)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    args = sys.argv[1:]

    # Parse flags
    claim_file: Optional[str] = None
    resume_id: Optional[str] = None
    appeal_id: Optional[str] = None
    category: Optional[str] = None

    if "--claim" in args:
        idx = args.index("--claim")
        claim_file = args[idx + 1] if idx + 1 < len(args) else None

    if "--resume" in args:
        idx = args.index("--resume")
        resume_id = args[idx + 1] if idx + 1 < len(args) else None

    if "--appeal" in args:
        idx = args.index("--appeal")
        appeal_id = args[idx + 1] if idx + 1 < len(args) else None

    if "--category" in args:
        idx = args.index("--category")
        category = args[idx + 1] if idx + 1 < len(args) else None

    interactive = not (claim_file or resume_id or appeal_id)

    _banner()

    registry = AgentRegistry()
    try:
        await registry.initialise()

        if resume_id:
            await _handle_resume(registry, resume_id)

        elif appeal_id:
            await _handle_appeal(registry, appeal_id)

        else:
            if claim_file:
                with open(claim_file) as f:
                    claim_input = json.load(f)
            else:
                claim_input = build_claim_interactive(category=category)

            await _handle_new_claim(registry, claim_input)

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
    except Exception as exc:
        print(f"\n  ✗  Error: {exc}")
        raise
    finally:
        print("  ◈  Cleaning up Foundry agents...")
        await registry.cleanup()
        print("  ✓  Session complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
