"""
agents/document_agent.py

Document Validation Agent: deterministically checks which required
documents are present for the claim's category.

No LLM call — this is a pure rules-based check using the per-category
required-document list from orchestration/claim_templates.py.

Runs after the Intake Agent, before the Policy Reasoning Agent, so
the Policy Agent receives accurate missing_documents without relying
on KB-parsed prose.
"""

from agents.models import ClaimRecord
from orchestration.claim_templates import TEMPLATES, POLICE_REPORT_DOC_KEYS


def run_document_validation(record: ClaimRecord) -> ClaimRecord:
    """
    Compare documents attached to the claim against the required list
    for this category. Populates:
      - record.document_requirements  (full required list)
      - record.missing_documents       (items not found in attached list)

    Matching is case-insensitive substring: "police report" matches
    "Police_Report_2026.pdf".
    """
    cat_key = record.claim_category.lower() if record.claim_category else "auto"
    template = TEMPLATES.get(cat_key, TEMPLATES["auto"])

    required: list[str] = template["required_documents"]
    record.document_requirements = list(required)

    attached_lower = " ".join(record.documents_attached).lower()

    missing: list[str] = []
    for req in required:
        # Police report may be flagged by the dedicated boolean field
        if req in POLICE_REPORT_DOC_KEYS and record.police_report_attached:
            continue
        # Substring match — flexible enough for filenames like "damage_photos_v2.jpg"
        keyword = req.lower().replace(" (theft/vandalism)", "").replace(" (if applicable)", "").strip()
        if keyword not in attached_lower:
            # For police report docs: only flag if not explicitly attached
            if req in POLICE_REPORT_DOC_KEYS:
                # Only flag as missing if police_report_attached is False AND
                # no matching filename found
                if not record.police_report_attached:
                    missing.append(req)
            else:
                missing.append(req)

    record.missing_documents = missing
    return record
