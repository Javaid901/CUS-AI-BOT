"""
backend/app/ingest/prompts.py

Prompt templates for the RAG generator.

Design goals:
  - Ground the LLM strictly in the provided context.
  - Prevent fabrication: never answer outside retrieved context.
  - Prefer newest documents when multiple sources conflict.
  - Produce concise, citation-aware answers suitable for a university assistant.
  - Always cite sources by document title and page/section.
  - Structured evidence grouping by document.
"""

from __future__ import annotations

import re

SYSTEM_PROMPT = (
    "You are CUS AI Assistant, the official help desk for Cluster University Srinagar. "
    "Answer ONLY using the provided knowledge-base excerpts. "
    "You must follow these rules:\n"
    "1. NEVER use your prior knowledge or information outside the excerpts.\n"
    "2. If the answer is not contained in the excerpts, respond exactly with: "
    "\"I couldn't find this information in the Cluster University Srinagar knowledge base.\"\n"
    "3. ALWAYS cite the source document title and page/section when you provide information.\n"
    "4. If multiple sources provide different information, prefer the most recent document.\n"
    "5. Be concise, factual, and friendly. Use bullet points when listing items.\n"
    "6. Answer the user's question directly. Do NOT mention 'excerpts', 'context', "
    "or quote the source labels in your reply.\n"
    "7. Do not invent dates, names, phone numbers, fees, or links that are not in the excerpts.\n"
    "8. If the information in the excerpts is outdated, note the year of the source.\n"
    "9. When citing, format as: [Source: Document Title, Page X, Section: Y].\n"
    "10. If the question asks for fee, amount, or numbers and the excerpts do not contain "
    "exact numbers, do not guess. Say the information is not available in the documents.\n"
    "11. For admission-related queries, prioritize excerpts from admission prospectus "
    "or admission notices over general documents.\n"
    "12. For result-related queries, prioritize excerpts from result notifications "
    "or examination notices.\n"
    "13. When listing items, keep the exact text from the source — do not paraphrase numbers.\n"
    "14. NEVER generate fictional fee amounts, dates, eligibility criteria, or course names."
)

CONTEXT_TEMPLATE = (
    "Below are excerpts from official Cluster University Srinagar documents, "
    "grouped by source document:\n\n"
    "{context}\n\n"
    "Question: {question}\n\n"
    "Answer using ONLY the excerpts above. If the information is missing or "
    "not fully supported, reply with the exact fallback sentence."
)

FALLBACK_MESSAGE = "I couldn't find this information in the Cluster University Srinagar knowledge base."


def display_title(title: str) -> str:
    """Human-readable document label for prompts and citations.

    Stored titles are raw filenames ("7affc4405141_84e0ab6e600b_CUS_
    Complete_Knowledge_Base.pdf"). Strip the extension and any
    downloaded-file id prefix so neither the LLM citations nor the chat
    UI expose raw artifact names.
    """
    t = (title or "").strip()
    if not t:
        return "Document"
    t = re.sub(r"\.(pdf|docx?|txt|csv|xlsx?)$", "", t, flags=re.I)
    t = re.sub(r"^[0-9a-fA-F]+(?:_[0-9a-fA-F]+)*_(?=[A-Za-z])", "", t)
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)
    t = t.replace("_", " ").strip()
    return t or "Document"


def format_context(chunks: list[dict]) -> str:
    """Render retrieved chunks into a structured context block grouped by document."""
    from collections import OrderedDict

    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for c in chunks:
        title = display_title(c.get("document_title") or c.get("source") or "Document")
        groups.setdefault(title, []).append(c)

    parts = []
    src_idx = 1
    for doc_title, doc_chunks in groups.items():
        chunk_texts = []
        for c in doc_chunks:
            page = c.get("page_number")
            heading = c.get("heading") or ""
            loc = f" (Page {page})" if page else ""
            heading_label = f" — Section: {heading}" if heading else ""
            chunk_texts.append(f"  {loc}{heading_label}\n  {c.get('content', '')}")
        combined = "\n\n".join(chunk_texts)
        parts.append(f"[Source {src_idx}: {doc_title}]\n{combined}")
        src_idx += 1

    return "\n\n".join(parts)


def format_context_flat(chunks: list[dict]) -> str:
    """Original flat format (backward compatibility)."""
    parts = []
    for i, c in enumerate(chunks, start=1):
        title = display_title(c.get("document_title") or c.get("source") or "Document")
        page = c.get("page_number")
        heading = c.get("heading") or ""
        loc = f" (page {page})" if page else ""
        heading_label = f" — Section: {heading}" if heading else ""
        parts.append(f"[{i}] {title}{loc}{heading_label}\n{c.get('content', '')}")
    return "\n\n".join(parts)
