"""Generate the sample company evidence bundle for P2/E2E demos.

Crafted against docs/mock-tender.pdf (GEM/2026/B/8111208) to force every
EvidenceMap status:
- financials FY22-23..FY24-25: turnover 11.2 / 12.1 / 12.8 Cr
  (avg ~12.03 Cr >= 11.56 Cr -> turnover SATISFIED)
- 2 completion certificates (~21 / 22 Cr each): only 2 of the required
  3 x 40% projects -> experience PARTIAL
- ISO 9001 + ISO 27001 valid till 2027 -> SATISFIED
- company profile (incorporated 2018 -> 8 yrs >= 7 -> SATISFIED)
- deliberately MISSING: OEM authorization, ATC-referenced docs -> MISSING

Usage: .venv/bin/python scripts/make_sample_company_docs.py
Output: data/sample-company/*.pdf (committed fixtures for reproducible demos)
"""

import os

import pymupdf

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "sample-company")

FINANCIALS = [
    ("FY 2022-23", "11,20,00,000", "11.20"),
    ("FY 2023-24", "12,10,00,000", "12.10"),
    ("FY 2024-25", "12,80,00,000", "12.80"),
]

DOCS = {
    "acme-financials-fy23-25.pdf": (
        "ACME INFOTECH PVT LTD — Audited Financial Summary\n"
        "Chartered Accountant certified annual turnover:\n"
        + "\n".join(f"- {fy}: INR {amt} ({cr} crore)" for fy, amt, cr in FINANCIALS)
        + "\nNet worth positive in all three years. UDIN: 24ACME0117."
    ),
    "acme-completion-cert-railways.pdf": (
        "COMPLETION CERTIFICATE — Indian Railways, Centre for Railway Information Systems\n"
        "M/s Acme Infotech Pvt Ltd has successfully completed the work "
        "\"Development and maintenance of passenger information portal\" "
        "for a contract value of INR 21,00,00,000 (21 crore). "
        "Period: April 2022 to March 2024. Performance: Satisfactory. "
        "Issued by the Office of the General Manager, CRIS, New Delhi."
    ),
    "acme-completion-cert-psu-bank.pdf": (
        "COMPLETION CERTIFICATE — Nationalized Bank IT Division\n"
        "M/s Acme Infotech Pvt Ltd has successfully completed "
        "\"Core banking helpdesk and facility management services\" "
        "for a contract value of INR 22,00,00,000 (22 crore). "
        "Period: June 2021 to May 2024. Performance: Good. "
        "Issued by the Chief Manager (IT), Mumbai."
    ),
    "acme-iso-certificates.pdf": (
        "ISO 9001:2015 Certificate No. QMS-22114 — Acme Infotech Pvt Ltd. "
        "Scope: Design, development and maintenance of IT systems. "
        "Valid from 01-04-2024 to 31-03-2027.\n"
        "ISO/IEC 27001:2022 Certificate No. ISMS-08831 — Acme Infotech Pvt Ltd. "
        "Scope: Information security management for software services. "
        "Valid from 01-04-2024 to 31-03-2027."
    ),
    "acme-company-profile.pdf": (
        "ACME INFOTECH PVT LTD — Company Profile\n"
        "Incorporated: 15-06-2018 under the Companies Act 2013. "
        "Registered office: New Delhi. 240 employees. "
        "Business: IT services, system integration, application development "
        "and operations and maintenance for government and PSU clients. "
        "GSTIN: 07ACME1234F1Z5. PAN: ACME1234F."
    ),
}


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for fname, text in DOCS.items():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(56, 56, 540, 780), text, fontsize=11)
        doc.save(os.path.join(OUT_DIR, fname))
        doc.close()
        print("wrote", fname)


if __name__ == "__main__":
    main()
