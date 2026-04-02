#!/usr/bin/env python3
"""
Deterministic upgrade layer: v4 -> v4.1
- Reads canonical v4 CSV
- Replaces generic next_step_current with extracted request from email bodies
- Thread resolution is deterministic, derived from refs/in_reply_to/message_id
- No LLM usage
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple


# ----------------------------
# Config: generic detection
# ----------------------------

GENERIC_EXACT = {
    "respond with requested info",
    "respond with requested information",
    "await response",
    "awaiting response",
    "waiting on response",
    "follow up as needed",
    "follow up",
    "follow-up",
    "pending response",
    "tbd",
    "n/a",
    "na",
    "none",
}

GENERIC_PREFIX = (
    "await ",
    "awaiting ",
    "waiting ",
    "follow up",
    "follow-up",
    "respond ",
    "reconnect ",
    "touch base",
)
_FALLBACK_PATTERNS = [
    r"\bcan you please\b",
    r"\bcould you please\b",
    r"\bplease provide\b",
    r"\bplease send\b",
    r"\bplease share\b",
    r"\bplease review\b",
    r"\bwhat is\b",
    r"\bwhen is\b",
    r"\bwhat(?:'s| is) the\b",
]

def fallback_extract_request(text: str) -> str:
    """
    Deterministic fallback: extract up to 2 request-like sentences from the body
    when the extractor returns empty. Evidence-backed from body text only.
    """
    if not text:
        return ""

    # Normalize whitespace but keep line breaks for sentence boundaries.
    t = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split into simple sentence-like units.
    parts = re.split(r"(?<=[\.\?\!])\s+|\n+", t)
    hits = []
    for p in parts:
        s = _norm_ws(p)
        if not s:
            continue
        ls = s.lower()
        if any(re.search(pat, ls) for pat in _FALLBACK_PATTERNS):
            hits.append(s)
        if len(hits) >= 2:
            break

    # Check for attachment-related asks
    attachment_keywords = ["attach", "attachment", "attached", "please send", "provide the"]
    for p in parts:
        s = _norm_ws(p)
        if not s:
            continue
        ls = s.lower()
        if any(keyword in ls for keyword in attachment_keywords) and ('?' in ls or re.search(r"\bplease\b", ls)):
            hits.append(s)
            if len(hits) >= 2:
                break

    # If we got 2, join them; if 1, return it; else empty.
    out = " ".join(hits)
    return out[:300].strip()  # hard cap to avoid long rambles


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def is_generic_next_step(s: str) -> bool:
    t = _norm_ws(s).lower()
    if not t:
        return True
    if t in GENERIC_EXACT:
        return True
    for p in GENERIC_PREFIX:
        if t.startswith(p):
            # guard: if it contains a concrete deliverable noun, don't treat as generic
            # (deterministic heuristic)
            if re.search(r"\b(submit|file|pay|schedule|order|install|deliver|sign|approve|permit|inspection|invoice|quote)\b", t):
                return False
            return True
    # ultra-short vague
    if len(t.split()) <= 2 and t in {"follow up", "respond", "waiting", "awaiting"}:
        return True
    return False


# ----------------------------
# Subject normalization
# ----------------------------

_RE_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.IGNORECASE)

def normalize_subject(subj: str) -> str:
    s = _norm_ws(subj)
    # strip repeated Re:/Fwd: prefixes deterministically
    while True:
        ns = _RE_PREFIX.sub("", s)
        if ns == s:
            break
        s = _norm_ws(ns)
    return s.lower()


# ----------------------------
# Email threading (deterministic)
# ----------------------------

@dataclass(frozen=True)
class EmailRow:
    id: int
    date_raw: str
    date_dt: datetime
    subject: str
    subject_norm: str
    from_addr: str
    body_text_path: Optional[str]
    message_id: Optional[str]
    in_reply_to: Optional[str]
    refs: Optional[str]
    thread_root: str  # derived deterministically

_MSGID_RE = re.compile(r"<[^>]+>")

def parse_date(date_str: str) -> datetime:
    # deterministic parse with fallback
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        # fallback: treat as epoch
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

def extract_msgids(refs_or_inreply: str) -> List[str]:
    if not refs_or_inreply:
        return []
    return _MSGID_RE.findall(refs_or_inreply)

def compute_thread_root(message_id: Optional[str], in_reply_to: Optional[str], refs: Optional[str]) -> str:
    """
    Deterministic:
    - If refs has msg-ids, root = first msg-id in refs
    - Else if in_reply_to has msg-id, root = that msg-id
    - Else if message_id exists, root = message_id
    - Else root = "NO_MSGID"
    """
    refs_ids = extract_msgids(refs or "")
    if refs_ids:
        return refs_ids[0]
    irt_ids = extract_msgids(in_reply_to or "")
    if irt_ids:
        return irt_ids[0]
    if message_id and _MSGID_RE.search(message_id):
        m = _MSGID_RE.search(message_id)
        return m.group(0) if m else message_id
    return "NO_MSGID"


# ----------------------------
# Extractor import (no subprocess)
# ----------------------------

def load_extractor():
    """
    We don't assume the exact function name; we resolve deterministically.
    Your extractor file is: ~/.openclaw/workspace/scripts/extract_request_from_body.py

    Expected callable signature:
        (body_text_path: str) -> (request: str, confidence: float, strong_entity: int)
    OR returns dict with those keys.
    """
    import importlib.util

    path = os.path.expanduser("~/.openclaw/workspace/scripts/extract_request_from_body.py")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Extractor not found at: {path}")

    spec = importlib.util.spec_from_file_location("extract_request_from_body", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    # candidates in priority order
    candidates = [
        "extract_best",
        "extract_request_from_body",
        "extract_request",
        "extract",
        "run_extraction",
    ]
    fn = None
    for name in candidates:
        if hasattr(mod, name) and callable(getattr(mod, name)):
            fn = getattr(mod, name)
            break

    if fn is None:
        public = [k for k in dir(mod) if not k.startswith("_")]
        raise RuntimeError(
            "Could not find extractor function. "
            f"Tried {candidates}. Available: {public}"
        )

    def wrapper(body_text_path: str) -> Tuple[str, float, int]:
        # Deterministic: always feed raw text to the extractor callable.
        path = os.path.expanduser(body_text_path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()

        out = fn(txt)

        # Normalize output to: (request: str, confidence: float, strong_entity: int)
        if isinstance(out, dict):
            req = out.get("request") or ""
            conf = float(out.get("confidence") or 0.0)
            se = int(out.get("strong_entity") or 0)
            return req, conf, se

        if isinstance(out, (list, tuple)):
            # Your extract_best returns: (sentence, score, strong_entity_bool)
            if len(out) >= 3:
                req = str(out[0] or "")
                conf = float(out[1] or 0.0)
                se_raw = out[2]
                se = 1 if (se_raw is True or str(se_raw).lower() in {"1", "true", "yes"}) else 0
                return req, conf, se
            if len(out) == 2:
                req = str(out[0] or "")
                conf = float(out[1] or 0.0)
                return req, conf, 0
            if len(out) == 1:
                return str(out[0] or ""), 0.0, 0

        if isinstance(out, str):
            return out, 0.0, 0

        raise RuntimeError(f"Extractor returned unsupported format: {type(out)} => {out}")

    return wrapper
    return wrapper


# ----------------------------
# Replacement policy
# ----------------------------

def qualifies(conf: float, strong_entity: int) -> bool:
    if conf >= 0.70:
        return True
    if conf >= 0.60 and int(strong_entity) == 1:
        return True
    return False


# ----------------------------
# Core upgrade logic
# ----------------------------

def load_emails(db_path: str) -> List[EmailRow]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT id, date, subject, from_addr, body_text_path, message_id, in_reply_to, refs
        FROM emails
        WHERE subject IS NOT NULL
    """).fetchall()
    con.close()

    out: List[EmailRow] = []
    for r in rows:
        date_raw = r["date"] or ""
        dt = parse_date(date_raw)
        subj = r["subject"] or ""
        subj_norm = normalize_subject(subj)
        msgid = r["message_id"]
        irt = r["in_reply_to"]
        refs = r["refs"]
        root = compute_thread_root(msgid, irt, refs)
        out.append(
            EmailRow(
                id=int(r["id"]),
                date_raw=date_raw,
                date_dt=dt,
                subject=subj,
                subject_norm=subj_norm,
                from_addr=r["from_addr"] or "",
                body_text_path=r["body_text_path"],
                message_id=msgid,
                in_reply_to=irt,
                refs=refs,
                thread_root=root,
            )
        )

    # newest first (deterministic tie-break: id)
    out.sort(key=lambda e: (e.date_dt, e.id), reverse=True)
    return out


def build_subject_index(emails: List[EmailRow]) -> Dict[str, List[EmailRow]]:
    idx: Dict[str, List[EmailRow]] = {}
    for e in emails:
        if not e.subject_norm:
            continue
        idx.setdefault(e.subject_norm, []).append(e)
    # each list already in newest-first order due to global sort
    return idx


def pick_thread_for_subject(candidate_emails: List[EmailRow]) -> Optional[str]:
    """
    Deterministic: choose the thread_root whose newest email is newest.
    """
    if not candidate_emails:
        return None
    best_root = None
    best_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
    best_id = -1
    for e in candidate_emails:
        if e.date_dt > best_dt or (e.date_dt == best_dt and e.id > best_id):
            best_dt = e.date_dt
            best_id = e.id
            best_root = e.thread_root
    return best_root


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upgrade(
    canonical_csv: str,
    db_path: str,
    out_csv: str,
    log_jsonl: str,
    limit: Optional[int] = None,
) -> None:
    extractor = load_extractor()
    emails = load_emails(db_path)
    subj_index = build_subject_index(emails)

    # pre-build thread index (root -> emails newest first)
    thread_index: Dict[str, List[EmailRow]] = {}
    for e in emails:
        thread_index.setdefault(e.thread_root, []).append(e)

    # ensure deterministic order inside each thread list (newest first)
    for root in thread_index:
        thread_index[root].sort(key=lambda e: (e.date_dt, e.id), reverse=True)

    canonical_csv = os.path.expanduser(canonical_csv)
    out_csv = os.path.expanduser(out_csv)
    log_jsonl = os.path.expanduser(log_jsonl)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    os.makedirs(os.path.dirname(log_jsonl), exist_ok=True)

    with open(canonical_csv, "r", newline="", encoding="utf-8") as f_in, \
         open(out_csv, "w", newline="", encoding="utf-8") as f_out, \
         open(log_jsonl, "w", encoding="utf-8") as f_log:

        reader = csv.DictReader(f_in)
        fieldnames = list(reader.fieldnames or [])

        # required columns
        required = ["work_item_id", "next_step_current", "representative_subject"]
        for c in required:
            if c not in fieldnames:
                raise RuntimeError(f"Missing required column in canonical v4: {c}")

        # add v4.1 columns (audit-first)
        add_cols = [
            "next_step_original",
            "next_step_extracted",
            "next_step_confidence",
            "strong_entity",
            "request_email_id",
            "next_step_source",
            "next_step_upgrade_status",
            "next_step_upgrade_timestamp",
        ]
        for c in add_cols:
            if c not in fieldnames:
                fieldnames.append(c)

        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

        processed = 0
        replaced = 0

        for row in reader:
            processed += 1
            if limit and processed > limit:
                break

            wid = row.get("work_item_id", "")
            rep_subj = row.get("representative_subject", "") or ""
            subj_norm = normalize_subject(rep_subj)
            original_next = row.get("next_step_current", "") or ""

            row["next_step_original"] = original_next
            row["next_step_upgrade_timestamp"] = now_utc_iso()

            was_generic = is_generic_next_step(original_next)
            if not was_generic:
                row["next_step_upgrade_status"] = "unchanged_not_generic"
                writer.writerow(row)
                f_log.write(json.dumps({
                    "work_item_id": wid,
                    "status": "unchanged_not_generic",
                    "was_generic": False,
                    "representative_subject": rep_subj,
                    "subject_norm": subj_norm,
                }) + "\n")
                continue

            # Find candidate emails by normalized subject
            candidates = subj_index.get(subj_norm, [])
            if not candidates:
                row["next_step_upgrade_status"] = "unchanged_no_subject_match"
                writer.writerow(row)
                f_log.write(json.dumps({
                    "work_item_id": wid,
                    "status": "unchanged_no_subject_match",
                    "was_generic": True,
                    "representative_subject": rep_subj,
                    "subject_norm": subj_norm,
                }) + "\n")
                continue

            # Choose the most recent thread_root for this subject.
            # If we cannot resolve a thread, deterministically fall back to scanning
            # the subject-matched candidates directly (still evidence-backed).
            root = pick_thread_for_subject(candidates)

            if root and root in thread_index:
                scan_list = thread_index[root]
                thread_mode = True
            else:
                scan_list = candidates
                thread_mode = False
            tried: List[int] = []
            chosen = None

            # Deterministic preference: prioritize non-self sender emails first.
            self_markers = ["admin@nexadose.com", "@nexadose.com", "alizunaid@hotmail.com"]
            non_self = []
            self_sent = []
            seen_body = set()
            for _e in scan_list:
                fa = (_e.from_addr or "").lower()
                if any(m in fa for m in self_markers):
                    self_sent.append(_e)
                else:
                    non_self.append(_e)
            scan_list = non_self + self_sent

            # Scan newest -> oldest (thread if available, else subject-matched fallback)
            scan_list = sorted(scan_list, key=lambda x: x.id, reverse=True)
            seen_body = set()
            for e in scan_list:
                tried.append(e.id)

                bp = (e.body_text_path or "")
                if bp:
                    if bp in seen_body:
                        continue
                    seen_body.add(bp)

                if not e.body_text_path:
                    continue

                path = os.path.expanduser(e.body_text_path)
                if not os.path.exists(path):
                    continue


                try:
                    req, conf, se = extractor(path)
                except Exception as ex:
                    # deterministic: skip on extractor error, but log
                    f_log.write(json.dumps({
                        "work_item_id": wid,
                        "status": "error_extractor",
                        "email_id": e.id,
                        "error": str(ex),
                    }) + "\n")
                    continue

                # Track source mode for reliable log output
                source_mode = "extractor"
                if not qualifies(conf, se) or not _norm_ws(req):
                    # Read body_text from path
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        body_text = f.read()

                    # Call fallback_extract_request
                    fallback_req = fallback_extract_request(body_text)

                    # Check if fallback_req is non-empty and matches conditions
                    if fallback_req and (
                        "?" in fallback_req or
                        re.match(r"^(can you|could you|please|what is|when is)", fallback_req, re.IGNORECASE)
                    ):
                        req = fallback_req
                        conf = 0.72
                        se = 1
                        source_mode = "fallback"

                # Only evidence path in the canonical for next_step_source
                row["next_step_source"] = e.body_text_path or ""
                if qualifies(conf, se) and _norm_ws(req):
                    chosen = (e, req, float(conf), int(se), source_mode)
                    break

            if chosen is None:
                row["next_step_upgrade_status"] = "unchanged_no_qualifying_request"
                writer.writerow(row)
                f_log.write(json.dumps({
                    "root": root,
                    "thread_mode": thread_mode,
                    "tried": tried,
                    "work_item_id": wid,
                    "status": "unchanged_no_qualifying_request",
                    "was_generic": True,
                    "representative_subject": rep_subj,
                    "subject_norm": subj_norm,
                    "thread_root": root,
                    "candidate_email_ids_tried": tried,
                }) + "\n")
                continue

            e, req, conf, se, source_mode = chosen
            row["next_step_current"] = req
            row["next_step_extracted"] = req
            row["next_step_confidence"] = f"{conf:.2f}"
            row["strong_entity"] = str(se)
            row["request_email_id"] = str(e.id)
            row["next_step_source"] = os.path.expanduser(e.body_text_path or "")
            row["next_step_upgrade_status"] = "replaced"

            replaced += 1
            writer.writerow(row)

            next_step_source_mode = source_mode
            f_log.write(json.dumps({
                "root": root,
                "thread_mode": thread_mode,
                "tried": tried,
                "chosen_email_id": e.id,
                "confidence": conf,
                "strong_entity": se,
                "extracted_request": req,
                "body_text_path": row["next_step_source"],
                "next_step_confidence": f"{conf:.2f}",
                "next_step_source_mode": next_step_source_mode,
                "next_step_current": req[:300],
                "work_item_id": wid,
                "status": "replaced",
                "was_generic": True,
                "representative_subject": rep_subj,
                "subject_norm": subj_norm,
                "thread_root": root,
                "candidate_email_ids_tried": tried,
                "chosen_email_id": e.id,
                "confidence": conf,
                "strong_entity": se,
                "extracted_request": req,
                "body_text_path": row["next_step_source"],
            }) + "\n")

    print(f"Processed: {processed}")
    print(f"Replaced:  {replaced}")
    print(f"Output:    {out_csv}")
    print(f"Log:       {log_jsonl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", default="~/.openclaw/workspace/WORK_ITEMS_REGISTER.canonical.v4.csv")
    ap.add_argument("--db", default="~/.openclaw/workspace/email_db/email.sqlite")
    ap.add_argument("--out", default="~/.openclaw/workspace/WORK_ITEMS_REGISTER.upgraded.v4_1.csv")
    ap.add_argument("--log", default="~/.openclaw/workspace/logs/next_step_upgrades.v4_1.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="Process only N rows (0 = all)")
    args = ap.parse_args()

    upgrade(
        canonical_csv=args.canonical,
        db_path=os.path.expanduser(args.db),
        out_csv=args.out,
        log_jsonl=args.log,
        limit=(args.limit if args.limit and args.limit > 0 else None),
    )


if __name__ == "__main__":
    main()
