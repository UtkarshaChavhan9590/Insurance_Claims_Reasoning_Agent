"""
foundry_iq/setup_knowledge_base.py

One-time setup: create the Azure AI Search index and upload policy documents.
Run this BEFORE starting the chatbot for the first time.

Steps:
  1. Create (or update) the search index with semantic configuration "default"
  2. Upload chunked content from sample_data/ (auto_policy_manual.md,
     claims_precedents.md) into the index

The Policy Reasoning Agent and Compliance Agent use AzureAISearchTool attached
at agent creation time — Foundry handles retrieval automatically at runtime.
No KnowledgeBase or KnowledgeSource objects are needed in the agents.

After running this script:
  1. In the Azure AI Foundry portal, navigate to your project → Connected Resources
  2. Add your Azure AI Search service as a connected resource
  3. Copy the connection ID into .env as AZURE_SEARCH_CONNECTION_ID

Required .env variables:
  AZURE_SEARCH_ENDPOINT   — e.g. https://my-search.search.windows.net
  AZURE_SEARCH_INDEX_NAME — default: insurance-policy-index
  AZURE_SEARCH_ADMIN_KEY  — (optional) if omitted, uses DefaultAzureCredential
                            which requires Search Index Data Contributor role

Future upgrade path:
  KBaaS (Knowledge Bases as a Service) announced at Microsoft Build 2026 for
  Azure Logic Apps will provide a managed alternative to the manual Azure
  AI Search index lifecycle managed here. Migrate to KBaaS once it reaches GA.
"""

import asyncio
import os
import pathlib
import uuid

from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
)
from dotenv import load_dotenv

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME", "insurance-policy-index")
_ADMIN_KEY: str | None = os.environ.get("AZURE_SEARCH_ADMIN_KEY") or None

SAMPLE_DATA_DIR = pathlib.Path(__file__).parent.parent / "sample_data"
DOCS = [
    ("auto_policy_manual.md", "Auto Policy Manual"),
    ("claims_precedents.md", "Claims Precedents"),
]
CHUNK_SIZE = 800    # characters per chunk
CHUNK_OVERLAP = 0.10  # 10 % overlap


def _chunk_text(text: str, source: str, title: str) -> list[dict]:
    """Split text into overlapping chunks for semantic search."""
    chunks = []
    step = max(1, int(CHUNK_SIZE * (1 - CHUNK_OVERLAP)))
    for i in range(0, len(text), step):
        chunk = text[i: i + CHUNK_SIZE].strip()
        if chunk:
            chunks.append({
                "id": uuid.uuid4().hex,
                "content": chunk,
                "source": source,
                "title": title,
                "chunk_index": i // step,
            })
    return chunks


async def main() -> None:
    admin_key = _ADMIN_KEY
    if not admin_key:
        print(
            "  AZURE_SEARCH_ADMIN_KEY not set — using DefaultAzureCredential.\n"
            "  Ensure you have the 'Search Index Data Contributor' role on the service."
        )

    async with DefaultAzureCredential() as credential:
        async with SearchIndexClient(
            endpoint=SEARCH_ENDPOINT, credential=credential
        ) as index_client:

            # ── 1. Create / update index ──────────────────────────────────────
            print(f"  Creating search index '{INDEX_NAME}'...")
            index = SearchIndex(
                name=INDEX_NAME,
                fields=[
                    SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                    SearchableField(
                        name="content",
                        type=SearchFieldDataType.String,
                        analyzer_name="en.microsoft",
                    ),
                    SimpleField(
                        name="source",
                        type=SearchFieldDataType.String,
                        filterable=True,
                        facetable=True,
                    ),
                    SearchableField(name="title", type=SearchFieldDataType.String),
                    SimpleField(
                        name="chunk_index",
                        type=SearchFieldDataType.Int32,
                        sortable=True,
                    ),
                ],
                semantic_search=SemanticSearch(
                    configurations=[
                        SemanticConfiguration(
                            name="default",
                            prioritized_fields=SemanticPrioritizedFields(
                                content_fields=[SemanticField(field_name="content")],
                                keywords_fields=[SemanticField(field_name="title")],
                            ),
                        )
                    ],
                    default_configuration_name="default",
                ),
            )
            await index_client.create_or_update_index(index)
            print("  Index ready.")

            # ── 2. Upload document chunks ─────────────────────────────────────
            print("  Uploading policy documents...")
            all_chunks: list[dict] = []
            for filename, title in DOCS:
                doc_path = SAMPLE_DATA_DIR / filename
                if not doc_path.exists():
                    print(f"  Warning: {filename} not found — skipping.")
                    continue
                text = doc_path.read_text(encoding="utf-8")
                chunks = _chunk_text(text, filename, title)
                all_chunks.extend(chunks)
                print(f"    {filename}: {len(chunks)} chunks")

            data_credential: AzureKeyCredential | DefaultAzureCredential = (
                AzureKeyCredential(admin_key) if admin_key else credential
            )
            async with SearchClient(
                endpoint=SEARCH_ENDPOINT,
                index_name=INDEX_NAME,
                credential=data_credential,
            ) as search_client:
                await search_client.upload_documents(documents=all_chunks)
            print(f"  Uploaded {len(all_chunks)} chunks total.")

    print(f"\n  Done. Index '{INDEX_NAME}' is ready.")
    print()
    print("  Next steps:")
    print("    1. Open Azure AI Foundry portal → your project → Connected Resources")
    print("    2. Add your Azure AI Search service as a connected resource")
    print("    3. Copy the connection ID to .env as AZURE_SEARCH_CONNECTION_ID")
    print()
    print("  Future: migrate to KBaaS (Azure Logic Apps) when it reaches GA.")


if __name__ == "__main__":
    asyncio.run(main())
