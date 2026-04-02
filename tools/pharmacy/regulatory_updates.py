#!/usr/bin/env python3
"""
Nexadose Pharmacy – Regulatory Updates Tracker
================================================
Tracks USP compounding standards and Texas State Board of Pharmacy (TSBP)
regulatory requirements relevant to the Nexadose sterile compounding pharmacy
at 1360 S Main St, Mansfield TX.

Deterministic: reads existing workspace docs and work items only.
No external API calls, no network access.
Output: logs/regulatory_updates.json
"""

import csv
import json
from datetime import datetime, UTC
from pathlib import Path


# ---------------------------------------------------------------------------
# Known regulatory framework — hardcoded from USP and TSBP sources
# These represent the standards Nexadose must comply with as a sterile
# compounding pharmacy in Texas.
# ---------------------------------------------------------------------------

REGULATORY_FRAMEWORK: list[dict] = [
    {
        "standard": "USP <797>",
        "full_name": "USP Chapter <797> Pharmaceutical Compounding — Sterile Preparations",
        "issuing_body": "United States Pharmacopeia (USP)",
        "current_revision": "November 2023",
        "effective_date": "2023-11-01",
        "summary": (
            "Governs the environment, personnel training, testing, and quality assurance "
            "for sterile compounding. Defines ISO classification for cleanrooms, beyond-use "
            "dates (BUDs), environmental monitoring, and sterility testing requirements. "
            "The November 2023 revision significantly updated BUD limits and introduced "
            "new cleaning and disinfection requirements."
        ),
        "nexadose_relevance": "HIGH — directly governs IV room and sterile compounding cleanroom design and operations.",
        "key_requirements": [
            "ISO 5 primary engineering control (PEC) for all sterile compounding",
            "ISO 7 buffer area surrounding the PEC",
            "ISO 8 ante-area (or ISO 7 for high-risk compounding)",
            "Positive pressure for non-hazardous sterile compounding (ISO 7 buffer room)",
            "Environmental monitoring (viable and non-viable particulate)",
            "Personnel training, competency assessment, and gloved fingertip testing",
            "Master formulation records and compounding records",
            "Beyond-use dates (BUDs) based on compounding category and sterility testing",
        ],
        "workspace_docs": [
            "USP797 summary nov2023.pdf",
            "USP797 Clean Room Design.pdf",
            "Nexadose USA Pharmacy Clean Rooom Design V3 -DERSION.pdf",
            "Nexadose USA Pharmacy Clean Rooom Design V4(2).pdf",
            "Nexadose Pharmacy Design Drawing-V5.pdf",
            "Nexadose USA Pharmacy Clean Rooom Design V6.pdf",
            "Airkey NE25222-2501 America USP797_800 modular cleanroom",
        ],
        "work_item_keywords": ["usp797", "usp 797", "cleanroom", "clean room", "iv room",
                               "sterile", "iso 5", "iso 7"],
    },
    {
        "standard": "USP <800>",
        "full_name": "USP Chapter <800> Hazardous Drugs — Handling in Healthcare Settings",
        "issuing_body": "United States Pharmacopeia (USP)",
        "current_revision": "December 2019 (aligned update November 2023)",
        "effective_date": "2020-12-01",
        "summary": (
            "Mandates safe handling of hazardous drugs (HDs) including receipt, storage, "
            "compounding, dispensing, and waste disposal. Requires negative-pressure ISO 7 "
            "buffer rooms for HD compounding, C-PECs (containment PECs), and a negative-pressure "
            "ante-area or ante-room. Applies to all HD compounding regardless of quantity."
        ),
        "nexadose_relevance": "HIGH — governs hazardous drug handling if Nexadose handles any NIOSH-listed HDs.",
        "key_requirements": [
            "Negative-pressure ISO 7 buffer room for HD sterile compounding",
            "Containment primary engineering control (C-PEC) — Class II BSC or CACI",
            "Negative-pressure ante-area (ISO 7 or ISO 8)",
            "Full personal protective equipment (PPE) including chemo-rated gloves",
            "Hazardous drug list assessment and risk categorization",
            "Decontamination and deactivation procedures",
            "Medical surveillance for personnel regularly handling HDs",
            "HD receipt and storage in negative-pressure area or ventilated cabinet",
        ],
        "workspace_docs": [
            "USP800 summary dec2019.pdf",
            "USP800 Clean Room Design.pdf",
            "NEGATIVE.PRESSURE.ROOM.jpeg",
        ],
        "work_item_keywords": ["usp800", "usp 800", "hazardous", "negative pressure", "hd"],
    },
    {
        "standard": "USP <71>",
        "full_name": "USP Chapter <71> Sterility Tests",
        "issuing_body": "United States Pharmacopeia (USP)",
        "current_revision": "Current edition",
        "effective_date": None,
        "summary": (
            "Defines sterility testing requirements for compounded sterile preparations "
            "with extended BUDs. Required under USP <797> for Category 3 preparations."
        ),
        "nexadose_relevance": "MEDIUM — required if Nexadose compounds Category 3 preparations with extended BUDs.",
        "key_requirements": [
            "Membrane filtration or direct inoculation method",
            "7-day and 14-day incubation at specified temperatures",
            "Must be performed in an appropriately controlled environment",
        ],
        "workspace_docs": [],
        "work_item_keywords": ["sterility test", "usp 71", "category 3"],
    },
    {
        "standard": "TSBP – Pharmacy Act",
        "full_name": "Texas Pharmacy Act (Texas Occupations Code, Chapter 558)",
        "issuing_body": "Texas State Board of Pharmacy (TSBP)",
        "current_revision": "Ongoing legislative updates",
        "effective_date": None,
        "summary": (
            "Establishes requirements for pharmacist and pharmacy licensure in Texas. "
            "Governs pharmacist-in-charge (PIC) responsibilities, pharmacy permit types, "
            "and disciplinary authority of the TSBP."
        ),
        "nexadose_relevance": "HIGH — foundational requirement for operating a pharmacy in Texas.",
        "key_requirements": [
            "Licensed pharmacist-in-charge (PIC) required",
            "Pharmacy permit (Class A retail or Class C-S for sterile compounding)",
            "PIC notification to TSBP within 10 days of change",
            "Annual pharmacy permit renewal",
            "Pharmacist license verification for all dispensing pharmacists",
        ],
        "workspace_docs": [
            "Pharmacy and BOP Questionnaire PM 458 0325.docx",
        ],
        "work_item_keywords": ["board of pharmacy", "bop", "tsbp", "pharmacy act", "pharmacist license",
                               "pic", "pharmacist-in-charge"],
    },
    {
        "standard": "TSBP – 22 TAC §291 (Class C-S)",
        "full_name": "Texas Administrative Code, Title 22, Part 15, Chapter 291 — Pharmacies (Class C-S: Sterile Compounding)",
        "issuing_body": "Texas State Board of Pharmacy (TSBP)",
        "current_revision": "Current promulgated rules",
        "effective_date": None,
        "summary": (
            "Specific operational requirements for Texas Class C-S pharmacies (non-resident "
            "or institutional pharmacies compounding sterile preparations). Incorporates "
            "USP <797> and USP <800> by reference. Addresses facility, personnel, "
            "equipment, policies and procedures, and recordkeeping."
        ),
        "nexadose_relevance": "HIGH — Nexadose must obtain a Class C-S permit and comply with these rules.",
        "key_requirements": [
            "Class C-S pharmacy permit application to TSBP",
            "Physical facility must meet USP <797> and <800> requirements",
            "Written policies and procedures (P&P manual) required",
            "Environmental monitoring records retained ≥3 years",
            "Compounding records retained ≥3 years",
            "Annual self-inspection using TSBP checklist",
            "TSBP inspection prior to commencing operations",
        ],
        "workspace_docs": [
            "Pharmacy and BOP Questionnaire PM 458 0325.docx",
        ],
        "work_item_keywords": ["class c", "sterile compounding", "tsbp inspection", "pharmacy permit"],
    },
    {
        "standard": "DEA Registration",
        "full_name": "DEA Pharmacy Registration (21 CFR Part 1301)",
        "issuing_body": "Drug Enforcement Administration (DEA)",
        "current_revision": "Ongoing",
        "effective_date": None,
        "summary": (
            "Required for any pharmacy that handles Schedule II–V controlled substances. "
            "Separate DEA registration may be required for each physical location."
        ),
        "nexadose_relevance": "MEDIUM — required if Nexadose dispenses or compounds controlled substances.",
        "key_requirements": [
            "DEA Form 224 (new pharmacy registration)",
            "Biennial registration renewal",
            "DEA Schedule II vault or safe requirements if applicable",
            "CSOS (Controlled Substance Ordering System) for Schedule II ordering",
            "Theft/loss reporting (DEA Form 106)",
        ],
        "workspace_docs": [],
        "work_item_keywords": ["dea", "controlled substance", "schedule ii", "narcotic"],
    },
    {
        "standard": "CMS / Medicare Part D",
        "full_name": "Centers for Medicare & Medicaid Services — Part D Pharmacy Accreditation",
        "issuing_body": "CMS",
        "current_revision": "Ongoing",
        "effective_date": None,
        "summary": (
            "If billing Medicare or Medicaid, Nexadose must enroll as a Medicare Part D network "
            "pharmacy and comply with applicable CMS regulations, including DMEPOS accreditation "
            "if billing for home infusion."
        ),
        "nexadose_relevance": "LOW–MEDIUM — depends on whether Nexadose plans to bill Medicare/Medicaid.",
        "key_requirements": [
            "NPI (National Provider Identifier) enrollment",
            "Medicare Part D network agreement through a PDP sponsor",
            "URAC or ACHC accreditation for home infusion (if applicable)",
        ],
        "workspace_docs": [],
        "work_item_keywords": ["medicare", "medicaid", "cms", "npi", "accreditation"],
    },
]

# Work item categories that relate to regulatory/compliance tracking
REGULATORY_CATEGORIES = {"PERMITS_INSPECTIONS", "CLEANROOM_USP", "LICENSING_BOARD", "LEGAL"}

# Filing directories to scan for regulatory documents
FILING_SCAN_DIRS = [
    "filing/01_LICENSING_BOARD",
    "filing/06_CLEANROOM_USP_797_800",
    "filing/02_CITY_PERMITS_INSPECTIONS",
    "organized_attachments/Cleanroom_Equipment",
    "organized",
]


def load_work_items(csv_path: Path) -> list[dict]:
    if not csv_path.exists() or not csv_path.is_file():
        return []
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def is_regulatory_related(row: dict) -> bool:
    categories = {c.strip() for c in (row.get("categories_seen") or "").split(";") if c.strip()}
    return bool(categories & REGULATORY_CATEGORIES)


def row_mentions_keywords(row: dict, keywords: list[str]) -> bool:
    haystack = " | ".join([
        row.get("canonical_key", ""),
        row.get("representative_subject", ""),
        row.get("next_step_current", ""),
        row.get("categories_seen", ""),
    ]).lower()
    return any(kw.lower() in haystack for kw in keywords)


def find_workspace_docs(workspace: Path, expected_docs: list[str]) -> dict[str, str | None]:
    """
    For each expected doc name, search the workspace filing directories.
    Returns {doc_name: relative_path_if_found_else_None}.
    """
    result: dict[str, str | None] = {}
    for doc_name in expected_docs:
        found_path = None
        for scan_dir in FILING_SCAN_DIRS:
            search_root = workspace / scan_dir
            if not search_root.exists():
                continue
            for candidate in search_root.rglob("*"):
                if doc_name.lower() in candidate.name.lower():
                    found_path = str(candidate.relative_to(workspace))
                    break
            if found_path:
                break
        result[doc_name] = found_path
    return result


def build_regulation_report(reg: dict, workspace: Path, work_items: list[dict]) -> dict:
    # Find relevant work items
    related_items = [
        {
            "work_item_id": r.get("work_item_id", ""),
            "status": r.get("status_current", ""),
            "subject": r.get("representative_subject", ""),
            "last_activity_date": r.get("last_activity_date", ""),
            "next_step": r.get("next_step_current", ""),
        }
        for r in work_items
        if row_mentions_keywords(r, reg.get("work_item_keywords", []))
    ]

    # Find workspace documents
    doc_presence = find_workspace_docs(workspace, reg.get("workspace_docs", []))
    docs_found = {k: v for k, v in doc_presence.items() if v is not None}
    docs_missing = [k for k, v in doc_presence.items() if v is None]

    doc_coverage = (
        len(docs_found) / len(doc_presence)
        if doc_presence else None
    )

    # Compliance readiness signal
    if reg.get("workspace_docs") and doc_coverage is not None:
        if doc_coverage >= 0.8:
            doc_status = "DOCUMENTED"
        elif doc_coverage >= 0.4:
            doc_status = "PARTIAL"
        else:
            doc_status = "NEEDS_DOCUMENTATION"
    else:
        doc_status = "NO_DOCS_EXPECTED" if not reg.get("workspace_docs") else "UNKNOWN"

    return {
        "standard": reg["standard"],
        "full_name": reg["full_name"],
        "issuing_body": reg["issuing_body"],
        "current_revision": reg.get("current_revision"),
        "effective_date": reg.get("effective_date"),
        "nexadose_relevance": reg["nexadose_relevance"],
        "summary": reg["summary"],
        "key_requirements": reg.get("key_requirements", []),
        "documentation": {
            "status": doc_status,
            "coverage_ratio": round(doc_coverage, 2) if doc_coverage is not None else None,
            "docs_found": docs_found,
            "docs_missing": docs_missing,
        },
        "related_work_items": related_items,
        "related_work_item_count": len(related_items),
    }


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    output_path = logs_dir / "regulatory_updates.json"

    csv_path = workspace / "WORK_ITEMS_REGISTER.canonical.v4.csv"
    all_rows = load_work_items(csv_path)
    regulatory_rows = [r for r in all_rows if is_regulatory_related(r)]

    # Build per-standard reports
    reports = [
        build_regulation_report(reg, workspace, all_rows)
        for reg in REGULATORY_FRAMEWORK
    ]

    # Summary
    high_relevance = [r for r in reports if r["nexadose_relevance"].startswith("HIGH")]
    needs_docs = [r for r in reports if r["documentation"]["status"] == "NEEDS_DOCUMENTATION"]
    partial_docs = [r for r in reports if r["documentation"]["status"] == "PARTIAL"]
    documented = [r for r in reports if r["documentation"]["status"] == "DOCUMENTED"]

    # Collect all work items tagged as regulatory/compliance
    regulatory_open = [
        {
            "work_item_id": r.get("work_item_id", ""),
            "status": r.get("status_current", ""),
            "subject": r.get("representative_subject", ""),
            "categories": r.get("categories_seen", ""),
            "last_activity_date": r.get("last_activity_date", ""),
            "next_step": r.get("next_step_current", ""),
        }
        for r in regulatory_rows
        if r.get("status_current") not in ("DONE", "CLOSED")
    ]

    payload = {
        "task": "pharmacy_regulatory_updates",
        "facility": "Nexadose – 1360 S Main St, Mansfield TX",
        "status": "ok",
        "mode": "workspace_regulatory_status",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "work_items_csv": str(csv_path.relative_to(workspace)),
            "csv_exists": csv_path.exists(),
            "total_work_items": len(all_rows),
            "regulatory_tagged_items": len(regulatory_rows),
        },
        "summary": {
            "standards_tracked": len(REGULATORY_FRAMEWORK),
            "high_relevance_standards": len(high_relevance),
            "documented_standards": len(documented),
            "partial_documentation": len(partial_docs),
            "needs_documentation": len(needs_docs),
            "open_regulatory_work_items": len(regulatory_open),
            "attention_required": len(needs_docs) > 0 or len(regulatory_open) > 0,
            "high_relevance_standards_list": [r["standard"] for r in high_relevance],
        },
        "regulatory_standards": reports,
        "open_regulatory_work_items": regulatory_open,
        "notes": [
            "Regulatory framework is hardcoded from USP and TSBP sources as of 2025.",
            "USP <797> November 2023 revision is the current applicable standard.",
            "USP <800> effective December 2020; November 2023 harmonization with USP <797>.",
            "TSBP Class C-S permit required for sterile compounding operations in Texas.",
            "Documentation status is based on workspace file presence only — not legal compliance.",
            "Deterministic read-only — no external API calls.",
            "Re-run weekly to check for newly added documents and work items.",
        ],
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")

    # Print brief console summary
    print(f"\n=== Nexadose Regulatory Updates ===")
    print(f"Standards tracked: {len(REGULATORY_FRAMEWORK)} | High relevance: {len(high_relevance)}")
    print(f"Documentation: {len(documented)} documented, {len(partial_docs)} partial, {len(needs_docs)} needs docs")
    print(f"Open regulatory work items: {len(regulatory_open)}")
    for r in reports:
        doc_flag = {
            "DOCUMENTED": "✓",
            "PARTIAL": "~",
            "NEEDS_DOCUMENTATION": "✗",
            "NO_DOCS_EXPECTED": "-",
            "UNKNOWN": "?",
        }.get(r["documentation"]["status"], "?")
        print(f"  [{doc_flag}] {r['standard']:20s} {r['nexadose_relevance'][:6]}  {r['documentation']['status']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
