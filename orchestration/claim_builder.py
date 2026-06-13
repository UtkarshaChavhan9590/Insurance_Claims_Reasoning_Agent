"""
orchestration/claim_builder.py

Interactive guided claim submission form.

Invoked by workflow.py when --interactive is passed:
  python -m orchestration.workflow --interactive
  python -m orchestration.workflow --interactive --category auto

Steps:
  1. Category + subtype picker (skipped if --category passed)
  2. Policyholder + policy details
  3. Incident-specific fields (from claim_templates)
  4. Document checklist (required docs per category → y/n per item)
  5. Review summary → confirm

Saves the claim JSON to claims/<claim_id>.json and returns the dict.
"""

import json
import random
import string
from datetime import date, datetime
from pathlib import Path

from orchestration.claim_templates import TEMPLATES, POLICE_REPORT_DOC_KEYS

CLAIMS_DIR = Path("claims")
CLAIMS_DIR.mkdir(exist_ok=True)

_SEP = "━" * 60


def _prompt(message: str, choices: list[str] | None = None, required: bool = True) -> str:
    """Read a line of input, optionally constrained to choices. Accepts number or text."""
    while True:
        if choices:
            options = " / ".join(f"[{c}]" for c in choices)
            raw = input(f"  {message}  {options}: ").strip()
        else:
            raw = input(f"  {message}: ").strip()

        if raw == "" and not required:
            return ""
        if raw == "" and required:
            print("    \u21b3 This field is required.")
            continue
        if choices:
            # Accept numeric shortcut (1-based)
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]
            if raw.lower() in [c.lower() for c in choices]:
                return choices[[c.lower() for c in choices].index(raw.lower())]
            print(f"    \u21b3 Enter 1\u2013{len(choices)} or one of: {', '.join(choices)}")
            continue
        return raw


def _prompt_bool(message: str) -> bool:
    return _prompt(message, choices=["yes", "no"]).lower() == "yes"


def _prompt_float(message: str) -> float:
    while True:
        raw = input(f"  {message}: ").strip()
        try:
            val = float(raw.replace(",", "").replace("\u20b9", "").replace("$", ""))
            if val < 0:
                print("    \u21b3 Amount must be 0 or greater.")
                continue
            return val
        except ValueError:
            print("    \u21b3 Please enter a numeric amount (e.g. 15000 or 15000.00).")


def _prompt_date(message: str) -> str:
    while True:
        raw = input(f"  {message} (YYYY-MM-DD): ").strip()
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            print("    ↳ Please use the format YYYY-MM-DD (e.g. 2026-01-15).")


def _generate_claim_id() -> str:
    year = date.today().year
    suffix = "".join(random.choices(string.digits, k=5))
    return f"CLM-{year}-{suffix}"


def build_claim_interactive(category: str | None = None) -> dict:
    """
    Run the interactive guided claim form.
    Returns a validated claim dict ready for pipeline processing.
    """
    print(f"\n{_SEP}")
    print("  INSURANCE CLAIMS — New Claim Submission")
    print(_SEP)

    # ── Step 1: Category + Subtype ────────────────────────────────────────────
    if category and category.lower() in TEMPLATES:
        cat_key = category.lower()
        template = TEMPLATES[cat_key]
        print(f"\n  Category: {template['display_name']}")
    else:
        print("\n  Step 1 of 5 — Select Claim Category")
        cat_options = list(TEMPLATES.keys())
        for i, k in enumerate(cat_options, 1):
            print(f"    {i}. {TEMPLATES[k]['display_name']}")
        while True:
            raw = input("  Enter number or name: ").strip().lower()
            # accept number or name
            if raw.isdigit() and 1 <= int(raw) <= len(cat_options):
                cat_key = cat_options[int(raw) - 1]
                break
            elif raw in TEMPLATES:
                cat_key = raw
                break
            else:
                print(f"    ↳ Enter 1–{len(cat_options)} or one of: {', '.join(cat_options)}")
        template = TEMPLATES[cat_key]

    print(f"\n  Sub-type options:")
    for i, s in enumerate(template["subtypes"], 1):
        print(f"    {i}. {s}")
    subtype = _prompt("Claim sub-type (enter number or name)", choices=template["subtypes"])

    # ── Step 2: Policyholder + Policy Details ─────────────────────────────────
    print(f"\n  {'Step 2 of 5' if not category else 'Step 1 of 4'} — Policyholder & Policy Details")
    policyholder_name = _prompt("Full name of policyholder")
    policy_number = _prompt("Policy number (e.g. POL-12345)")
    policy_inception_date = _prompt_date("Policy inception date")
    claim_filed_date = date.today().isoformat()
    print(f"  Claim filed date: {claim_filed_date}  (today)")

    # ── Step 3: Claim-Specific Details ────────────────────────────────────────
    step = "Step 3 of 5" if not category else "Step 2 of 4"
    print(f"\n  {step} — Incident Details  [{template['display_name']} / {subtype}]")

    incident_data: dict = {}
    for field_spec in template["incident_fields"]:
        if field_spec["type"] == "bool":
            incident_data[field_spec["name"]] = _prompt_bool(field_spec["prompt"])
        elif field_spec["choices"]:
            incident_data[field_spec["name"]] = _prompt(
                field_spec["prompt"], choices=field_spec["choices"]
            )
        else:
            incident_data[field_spec["name"]] = _prompt(field_spec["prompt"])

    estimated_repair_cost = _prompt_float("Estimated claim amount / repair cost (\u20b9 INR)")
    deductible_amount = _prompt_float("Your policy deductible for this claim (\u20b9 INR)")
    prior_claims = int(
        _prompt("Number of prior claims in the last 6 months", choices=[str(i) for i in range(6)])
    )

    # ── Step 4: Document Checklist ────────────────────────────────────────────
    step = "Step 4 of 5" if not category else "Step 3 of 4"
    print(f"\n  {step} — Document Checklist")
    print("  Indicate which documents you are attaching to this claim:\n")

    documents_attached: list[str] = []
    police_report_attached = False

    for doc in template["required_documents"]:
        attached = _prompt_bool(f"  Attaching: {doc}?")
        if attached:
            # Normalise the doc name for storage
            doc_key = doc.replace(" (theft/vandalism)", "").replace(" (if applicable)", "").strip()
            documents_attached.append(doc_key)
            if doc.lower() in POLICE_REPORT_DOC_KEYS:
                police_report_attached = True

    # ── Step 5: Review & Confirm ───────────────────────────────────────────────
    claim_id = _generate_claim_id()
    step = "Step 5 of 5" if not category else "Step 4 of 4"
    print(f"\n  {step} — Review Your Claim")
    print(_SEP)
    print(f"  Claim ID          : {claim_id}")
    print(f"  Category          : {template['display_name']}  [{subtype}]")
    print(f"  Policyholder      : {policyholder_name}")
    print(f"  Policy Number     : {policy_number}")
    print(f"  Policy Inception  : {policy_inception_date}")
    print(f"  Filed Date        : {claim_filed_date}")
    print(f"  Claim Amount      : \u20b9{estimated_repair_cost:,.2f}")
    print(f"  Deductible        : \u20b9{deductible_amount:,.2f}")
    print(f"  Prior Claims (6m) : {prior_claims}")
    print(f"  Documents         : {', '.join(documents_attached) or 'none attached'}")
    print(f"  Police Report     : {'yes' if police_report_attached else 'no'}")
    print("  Incident details  :")
    for field_spec in template["incident_fields"]:
        val = incident_data.get(field_spec["name"], "")
        print(f"    {field_spec['name']}: {val}")
    print(_SEP)

    confirm = _prompt_bool("Confirm and submit this claim?")
    if not confirm:
        print("\n  Claim cancelled. No file saved.")
        raise SystemExit(0)

    # ── Build final dict ───────────────────────────────────────────────────────
    claim_dict: dict = {
        "claim_id": claim_id,
        "claim_category": cat_key,
        "claim_type": subtype,
        "policyholder_name": policyholder_name,
        "policy_number": policy_number,
        "policy_inception_date": policy_inception_date,
        "claim_filed_date": claim_filed_date,
        "estimated_repair_cost": estimated_repair_cost,
        "deductible_amount": deductible_amount,
        "documents_attached": documents_attached,
        "police_report_attached": police_report_attached,
        "prior_claims_last_6_months": prior_claims,
        **incident_data,
    }

    # Persist for audit / re-run
    out_path = CLAIMS_DIR / f"{claim_id}.json"
    with open(out_path, "w") as f:
        json.dump(claim_dict, f, indent=2, default=str)
    print(f"\n  Claim saved → {out_path}")

    return claim_dict
