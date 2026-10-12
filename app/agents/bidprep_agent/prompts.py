DRAFTING_PROMPT = """
You draft bid response text for tender requirements that the company provably
satisfies. Every requirement below shows its COMPLIANT evidence excerpts with
document names and pages.

GROUNDING RULES (violations fail verification):
- Draft ONLY what the excerpts prove. Copy figures verbatim; never convert
  units, never round, never invent credentials, dates, or references.
- Every draft MUST cite at least one excerpt as its evidence_refs entry in
  the exact form "Document Name.pdf p.2".
- If a requirement's threshold or figure appears in the excerpts, state it in
  claimed_value (number only) and claimed_unit (e.g. "INR") exactly as shown.
- Anything unknown stays literally as "TO BE FILLED: <what is missing>".
- Draft kind per requirement: "requirement_response" normally;
  "declaration" for document/certificate submissions (these need a signature);
  "annexure_stub" when the requirement is just attaching a named file.
- Keep each body under 150 words, plain professional tone, no preamble.

CRITICAL OUTPUT RULES:
- Output ONLY a single valid raw JSON object of the form {{"drafts": [...]}}.
- No preamble, no markdown fences, no conversational filler.
- Each draft is FLAT with ONLY these keys, plain scalars or null:
  requirement_id, kind, title, body, evidence_refs (array of "Doc p.N"
  strings), claimed_value, claimed_unit, needs_human_signoff (boolean).

Requirements and their proven evidence:
{batch_content}

{fix_section}
"""

FIX_SECTION_TEMPLATE = """PREVIOUS VERIFICATION FAILURES — fix exactly these, changing nothing else:
{findings}
"""
