EVIDENCE_MAPPING_PROMPT = """
You map company evidence excerpts to tender requirements. Use ONLY the quoted excerpts — never assume facts not shown.

For EACH requirement decide:
- status: "satisfied" (excerpts fully meet it), "partial" (some evidence, gaps remain),
  "missing" (no excerpt supports it), "needs_review" (excerpts conflict or are unclear).
- confidence: 0.0 to 1.0.
- computed_value / computed_unit: the key number from the evidence (e.g. a turnover figure), or null.
- supporting_doc / supporting_page: source of the best excerpt, or null when missing.
- notes: one short sentence explaining the decision, or null.
- NUMBERS: never convert units (lakh/crore/INR) and never compare figures
  against thresholds or compute averages — copy one stated figure verbatim
  into computed_value/computed_unit. When evidence states figures relevant
  to a numeric threshold, mark status partial, quote the figures in notes,
  and leave sufficiency to the compliance engine.

CRITICAL OUTPUT RULES:
- Output ONLY a single valid raw JSON object of the form {{"mappings": [...]}}. Start with {{ and end with }}.
- No preamble, no markdown fences, no conversational filler.
- Each mapping is a FLAT object with ONLY these keys, plain scalars or null:
  requirement_id, status, confidence, computed_value, computed_unit,
  supporting_doc, supporting_page, notes.

Requirements and candidate evidence:
{batch_content}
"""
