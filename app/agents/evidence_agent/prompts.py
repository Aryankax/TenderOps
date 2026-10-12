EVIDENCE_MAPPING_PROMPT = """
You verify tender requirements against company evidence, one requirement at a time. Excerpts are grouped by DOCUMENT — first pick the document that actually speaks to the requirement, then compare the requirement against THAT document's content. Use ONLY the quoted excerpts — never assume facts not shown.

For EACH requirement decide:
- status: "satisfied" (excerpts fully meet it), "partial" (some evidence, gaps remain),
  "missing" (no excerpt supports it), "needs_review" (excerpts conflict or are unclear).
- confidence: 0.0 to 1.0.
- computed_value / computed_unit: the company's key figure COPIED VERBATIM
  from an excerpt (e.g. "5 crore", "1156.37 Lakh") — never converted.
- supporting_doc / supporting_page: source of the best excerpt, or null when missing.
- notes: REQUIRED, one or two sentences: name the deciding document and page, quote the decisive figure or passage, and for partial verdicts state exactly what is still missing. Never null, never blank.
- NUMBERS: each requirement shows a "Target:" line with its threshold as
  "operator value unit" (e.g. ">= 115637000 INR"). When an excerpt states
  the company's own figure for the same measure in a compatible unit,
  compare it against the Target and set status accordingly — "satisfied"
  ONLY if the figure passes the operator test. Quote BOTH figures in notes
  ("company 12 crore vs required >= 115637000 INR"). If no excerpt states
  a comparable figure, say so in notes and use partial/missing/needs_review.
  Never convert lakh/crore, never compute averages, never invent figures.

CRITICAL OUTPUT RULES:
- Output ONLY a single valid raw JSON object of the form {{"mappings": [...]}}. Start with {{ and end with }}.
- No preamble, no markdown fences, no conversational filler.
- Each mapping is a FLAT object with ONLY these keys, plain scalars or null:
  requirement_id, status, confidence, computed_value, computed_unit,
  supporting_doc, supporting_page, notes.

Requirements and candidate evidence:
{batch_content}
"""
