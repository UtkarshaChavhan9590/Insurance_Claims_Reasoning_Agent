"""
orchestration/claim_templates.py

Per-category claim configuration used by:
  - orchestration/claim_builder.py  (interactive field prompts + doc checklist)
  - agents/document_agent.py        (required-document validation)
"""

from typing import TypedDict, Literal


class FieldSpec(TypedDict):
    name: str           # key in the output dict
    prompt: str         # text shown to the user
    type: str           # "str" | "float" | "bool" | "date"
    choices: list[str]  # if non-empty, restrict input to these values


class CategoryTemplate(TypedDict):
    display_name: str
    subtypes: list[str]                     # sub-categories the user picks from
    required_documents: list[str]           # deterministic doc checklist
    incident_fields: list[FieldSpec]        # category-specific incident prompts
    fraud_hints: list[str]                  # signals the Fraud Agent checks
    approval_limit: float                   # auto-approve ceiling (INR)


TEMPLATES: dict[str, CategoryTemplate] = {
    "auto": {
        "display_name": "Auto / Vehicle",
        "subtypes": ["collision", "comprehensive", "liability", "uninsured motorist"],
        "required_documents": [
            "police report",
            "damage photos",
            "repair estimate",
            "driver statement",
        ],
        "incident_fields": [
            {
                "name": "incident_description",
                "prompt": "Describe the incident (what happened, where, when)",
                "type": "str",
                "choices": [],
            },
            {
                "name": "other_party_involved",
                "prompt": "Was another party involved? (yes/no)",
                "type": "bool",
                "choices": ["yes", "no"],
            },
            {
                "name": "rideshare_active_at_incident",
                "prompt": "Was a rideshare app active at the time? (yes/no)",
                "type": "bool",
                "choices": ["yes", "no"],
            },
        ],
        "fraud_hints": [
            "claim filed within 14 days of policy inception",
            "multiple prior claims in last 6 months",
            "rideshare active without endorsement",
            "repair estimate exceeds vehicle market value",
        ],
        "approval_limit": 2000.0,
    },

    "home": {
        "display_name": "Home / Property",
        "subtypes": ["fire", "water damage", "theft", "vandalism", "storm damage"],
        "required_documents": [
            "police report (theft/vandalism)",
            "damage photos",
            "contractor repair estimate",
            "proof of ownership",
            "incident report",
        ],
        "incident_fields": [
            {
                "name": "incident_description",
                "prompt": "Describe the incident (type of damage, cause, date discovered)",
                "type": "str",
                "choices": [],
            },
            {
                "name": "property_address",
                "prompt": "Property address",
                "type": "str",
                "choices": [],
            },
            {
                "name": "temporary_repairs_done",
                "prompt": "Have temporary repairs been made to prevent further damage? (yes/no)",
                "type": "bool",
                "choices": ["yes", "no"],
            },
        ],
        "fraud_hints": [
            "claim filed within 30 days of policy inception",
            "multiple property claims in 12 months",
            "repair estimate from unlicensed contractor",
            "no third-party corroboration of incident",
        ],
        "approval_limit": 2000.0,
    },

    "health": {
        "display_name": "Health / Medical",
        "subtypes": ["hospitalization", "outpatient", "emergency", "surgery", "diagnostic"],
        "required_documents": [
            "hospital/clinic invoice",
            "attending physician report",
            "prescription receipts",
            "discharge summary",
        ],
        "incident_fields": [
            {
                "name": "incident_description",
                "prompt": "Describe the medical event (diagnosis, treatment, dates)",
                "type": "str",
                "choices": [],
            },
            {
                "name": "hospital_name",
                "prompt": "Name of hospital or clinic",
                "type": "str",
                "choices": [],
            },
            {
                "name": "pre_existing_condition",
                "prompt": "Is this related to a pre-existing condition? (yes/no)",
                "type": "bool",
                "choices": ["yes", "no"],
            },
        ],
        "fraud_hints": [
            "claim filed within 14 days of policy inception",
            "treatment dates outside policy active period",
            "invoice from non-accredited facility",
            "multiple claims for same diagnosis in 6 months",
        ],
        "approval_limit": 2000.0,
    },

    "travel": {
        "display_name": "Travel",
        "subtypes": ["trip cancellation", "trip interruption", "baggage loss", "travel medical", "flight delay"],
        "required_documents": [
            "airline/hotel cancellation confirmation",
            "booking receipts",
            "baggage loss report (airline)",
            "medical certificate (if applicable)",
            "proof of travel dates",
        ],
        "incident_fields": [
            {
                "name": "incident_description",
                "prompt": "Describe what happened (reason for claim, dates, destination)",
                "type": "str",
                "choices": [],
            },
            {
                "name": "destination",
                "prompt": "Travel destination (city, country)",
                "type": "str",
                "choices": [],
            },
            {
                "name": "travel_dates",
                "prompt": "Travel dates (e.g. 2026-06-01 to 2026-06-10)",
                "type": "str",
                "choices": [],
            },
        ],
        "fraud_hints": [
            "trip booked after policy inception for near-future travel",
            "claim filed for pre-existing medical condition",
            "no third-party cancellation documentation",
            "multiple travel claims in 12 months",
        ],
        "approval_limit": 2000.0,
    },
}

# Required documents that are always satisfied if police_report_attached=True
POLICE_REPORT_DOC_KEYS = {"police report", "police report (theft/vandalism)"}
