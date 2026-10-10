METADATA_EXTRACTION_PROMPT = """
Extract the metadata for the following tender document.
Use only the information supported by the document. Return null for missing or unestablished values.
Preserve original date and monetary expressions. Normalize only when reliable.
Do not fabricate details.
Omit empty or null fields instead of emitting explicit nulls.

CRITICAL OUTPUT RULES:
- Output ONLY a single valid raw JSON object. Start with {{ and end with }}.
- No preamble, no markdown fences (no ```json), no conversational filler, no YAML.
- Use EXACTLY these snake_case field names when present: tender_title, tender_reference,
  issuing_authority, submission_deadline, bid_opening_date, pre_bid_meeting_date,
  clarification_deadline, emd_amount, tender_fee, currency, contract_duration,
  delivery_deadline, other_metadata.

Document Content:
{content}
"""

REQUIREMENTS_EXTRACTION_PROMPT = """
Identify all material requirements relevant to preparing and submitting a bid from the following chunk of a tender document.
Preserve the original wording.
Determine if it is mandatory, optional, or conditional.
Identify thresholds, deadlines, or requested evidence.
Include the page and section supporting the interpretation.
If contradictory or ambiguous, note it in "condition".
If the chunk contains garbled or unreadable characters, transcribe only the readable words — never copy strange symbols.
When a passage is mixed-script, mojibake, or garbled non-English text followed by an English translation, use ONLY the English wording and skip the garbled script entirely.
Normalize clear expressions (e.g. ₹5 crore -> 50000000 INR) in threshold_text.
Copy the threshold phrase VERBATIM including comparison words (minimum, up to, exactly, within) — never drop them.
For exemptions, relaxations, or waivers put the relief phrase in condition (e.g. "Partial relaxation for MSE bidders"),
NOT in threshold_text — a relief is not a threshold the bidder must satisfy.
Classify each clause with semantic_type (one of BIDDER_OBLIGATION, ELIGIBILITY_CONDITION,
EXEMPTION, BUYER_DISCRETION, PROHIBITION, RESTRICTION, CONTRACTUAL_TERM, INFORMATIONAL):
use PROHIBITION for "must not"/"not allowed" (keep the negation in the text), EXEMPTION for
relaxations/waivers/MSE-startup relief, BUYER_DISCRETION for "buyer may"/"reserves the right",
INFORMATIONAL for references/uploads with no bidder duty. Never recast a conditional or
negative clause as an unconditional obligation.
Keep the response SMALL: at most 10 requirements per chunk, omit empty fields (use null).

CRITICAL OUTPUT RULES:
- Output ONLY a single valid raw JSON object of the form {{"requirements": [...]}}. Start with {{ and end with }}.
- No preamble, no markdown fences (no ```json), no conversational filler, no YAML.
- Each requirement is a FLAT object with ONLY these keys, all values plain strings, numbers, booleans or null.
  NEVER use nested objects or arrays:
  requirement_id (string, e.g. "R1"), category (one of FINANCIAL_ELIGIBILITY,
  TECHNICAL_ELIGIBILITY, EXPERIENCE, LEGAL_AND_STATUTORY, MANDATORY_DOCUMENTS,
  TECHNICAL_SPECIFICATIONS, COMMERCIAL, DEADLINES, SUBMISSION_INSTRUCTIONS,
  CONTRACTUAL, OTHER), semantic_type (one of the 8 classes above), title (short label),
  original_text (verbatim excerpt),
  mandatory (boolean true/false, or null when conditional — NEVER the strings
  "mandatory"/"conditional"), condition (optional string for conditional notes and
  deadlines), threshold_text (optional plain string such as "minimum 115637000 INR" or "up to 25 percent" —
  NEVER an object), evidence (optional plain string), deadline (optional plain string),
  page (integer), section (string).

Example of ONE requirement (copy this shape exactly):
{{"requirement_id": "R1", "category": "DEADLINES", "semantic_type": "ELIGIBILITY_CONDITION", "title": "Bid end date", "original_text": "Bid ends on 24-10-2026 16:00:00", "mandatory": true, "condition": null, "threshold_text": null, "evidence": null, "deadline": "24-10-2026 16:00:00", "page": 1, "section": "Bid Details"}}

Document Name: {document_name}

Chunk Content:
{chunk_content}
"""
