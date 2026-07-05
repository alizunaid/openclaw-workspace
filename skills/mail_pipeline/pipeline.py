#!/usr/bin/env python3
"""
Mailbox pipeline — Brick A: ingest, dedupe, thread, and contact-model a full
Gmail Takeout mbox export into a local SQLite database.

FULLY DETERMINISTIC — this module makes ZERO model / network calls. Everything
is Python stdlib (`mailbox`, `email`, `sqlite3`, `hashlib`) plus PyYAML for the
contacts draft.

Pipeline stages (see run_pipeline.py for the CLI that chains them):
  STEP 1  ingest()   raw mbox files  -> deduped `emails` rows (Message-ID PK)
  STEP 2  thread()   `emails`        -> `threads` + emails.thread_id
  STEP 3  contacts() `emails`        -> contacts_draft.yaml
  STEP 4  report()   the db          -> ingest_report.md

PII: the mbox export and the SQLite db are RAW PERSONAL DATA and are gitignored.
The report and contacts draft may carry addresses / names / domains / counts but
NEVER message bodies. Body-bearing helpers here are reused from the email_digest
brick's extraction logic.
"""
from __future__ import annotations

import hashlib
import html
import mailbox
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration (deterministic constants — no external config for Brick A)
# --------------------------------------------------------------------------- #
WINDOW_START = datetime(2025, 1, 1, tzinfo=timezone.utc)  # inclusive lower bound
BODY_TRUNC = 8000                                          # chars stored per body
BATCH_SIZE = 2000                                          # executemany flush size

# mbox files that are skipped entirely (per brief).
SKIP_MBOXES = {"Drafts", "Trash"}

# Operator-made labels: strong project-relevance hints.
OPERATOR_LABELS = ["1360 documents", "Leasing", "ESA", "Sourcing agent"]

# Category-* labels used as the noise-prefill set (a contact seen ONLY inside
# these gets noise:true). Note: Category Bills / Category Purchases are NOT in
# this set — bills and purchases are frequently operationally relevant.
NOISE_CATEGORY_LABELS = {
    "Category Promotions",
    "Category Updates",
    "Category Personal",
    "Category Travel",
}

# Sender local-parts that are auto-noise regardless of label.
NOISE_LOCALPARTS = ("no-reply", "noreply", "notifications", "donotreply",
                    "do-not-reply", "mailer-daemon")

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fwd|fw)\s*:\s*", re.IGNORECASE)
_ANGLE_RE = re.compile(r"<([^>]+)>")


# --------------------------------------------------------------------------- #
# Body extraction (reused from skills/email_digest/digest.py)
# --------------------------------------------------------------------------- #
def _strip_html(text: str) -> str:
    """Reduce an HTML body to readable plain text (drop script/style, tags, entities)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)          # remaining tags
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _safe_get_content(part) -> str:
    """Decode a MIME part to text, tolerant of odd/declared-wrong charsets."""
    try:
        return part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg) -> str:
    """Best-effort text body: first text/plain part, else stripped text/html."""
    plain = None
    html_body = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _safe_get_content(part)
            elif ctype == "text/html" and html_body is None:
                html_body = _safe_get_content(part)
    else:
        content = _safe_get_content(msg)
        if msg.get_content_type() == "text/html":
            html_body = content
        else:
            plain = content

    if plain and plain.strip():
        return plain.strip()
    if html_body and html_body.strip():
        return _strip_html(html_body)
    return ""


# --------------------------------------------------------------------------- #
# Header helpers
# --------------------------------------------------------------------------- #
def _hdr(msg, name: str) -> str:
    val = msg[name]
    return "" if val is None else str(val).strip()


def normalize_msgid(raw: str) -> str:
    """Return the canonical Message-ID (inside angle brackets, whitespace-stripped)."""
    if not raw:
        return ""
    m = _ANGLE_RE.search(raw)
    return (m.group(1) if m else raw).strip()


def _parse_reference_ids(raw: str) -> list[str]:
    """Extract all <id> tokens from a References / In-Reply-To header."""
    if not raw:
        return []
    ids = [m.strip() for m in _ANGLE_RE.findall(raw)]
    if not ids:  # no angle brackets — treat whitespace-split tokens as ids
        ids = [tok.strip() for tok in raw.split() if tok.strip()]
    return ids


def synthesize_msgid(date_raw: str, from_raw: str, subject_raw: str) -> str:
    """Stable synthetic key for a message with no Message-ID."""
    h = hashlib.sha1(f"{date_raw}\x1f{from_raw}\x1f{subject_raw}".encode("utf-8",
                     errors="replace")).hexdigest()
    return f"synth:{h}"


def to_utc_iso(date_raw: str):
    """Parse an RFC-2822 Date header to a UTC ISO-8601 string.

    Returns None when the header is missing or unparseable (caller counts these
    as undated). Naive datetimes (no tz / -0000) are assumed to be UTC.
    """
    if not date_raw:
        return None
    try:
        dt = parsedate_to_datetime(date_raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def normalize_subject(subject: str) -> str:
    """Strip repeated Re:/Fwd:/Fw: prefixes, lowercase, collapse whitespace."""
    s = subject or ""
    prev = None
    while prev != s:
        prev = s
        s = _SUBJECT_PREFIX_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip().lower()


# --------------------------------------------------------------------------- #
# SQLite schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id     TEXT PRIMARY KEY,
    date           TEXT,   -- UTC ISO-8601
    from_addr      TEXT,
    from_name      TEXT,
    to_addrs       TEXT,   -- comma-joined addresses
    cc_addrs       TEXT,   -- comma-joined addresses
    subject        TEXT,
    body_text      TEXT,   -- text/plain preferred, HTML stripped, <= 8000 chars
    source_mboxes  TEXT,   -- comma-joined sorted labels the msg appeared in
    in_reply_to    TEXT,
    references_ids TEXT,   -- comma-joined
    thread_id      INTEGER
);
CREATE TABLE IF NOT EXISTS threads (
    thread_id       INTEGER PRIMARY KEY,
    root_message_id TEXT,
    subject_norm    TEXT,
    size            INTEGER,
    first_date      TEXT,
    last_date       TEXT
);
CREATE TABLE IF NOT EXISTS ingest_stats (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def set_stat(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO ingest_stats(key, value) VALUES (?, ?)",
                 (key, str(value)))


def get_stat(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM ingest_stats WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


# --------------------------------------------------------------------------- #
# STEP 1 — Ingest
# --------------------------------------------------------------------------- #
def _mbox_label(path: Path) -> str:
    return path.stem  # "Category Promotions.mbox" -> "Category Promotions"


def _open_mbox(path: Path) -> mailbox.mbox:
    """Open an mbox with a modern-policy factory so get_content() decodes MIME."""
    return mailbox.mbox(
        str(path),
        factory=lambda f: BytesParser(policy=policy.default).parse(f),
        create=False,
    )


def _row_from_msg(msg, label: str) -> tuple:
    """Build an emails row tuple from a parsed message. May raise on bad messages."""
    raw_mid = _hdr(msg, "Message-ID")
    date_raw = _hdr(msg, "Date")
    from_raw = _hdr(msg, "From")
    subject = _hdr(msg, "Subject")

    mid = normalize_msgid(raw_mid)
    if not mid:
        mid = synthesize_msgid(date_raw, from_raw, subject)

    dt = to_utc_iso(date_raw)  # datetime or None
    from_name, from_addr = parseaddr(from_raw)
    to_pairs = getaddresses(msg.get_all("To", []))
    cc_pairs = getaddresses(msg.get_all("Cc", []))
    to_addrs = ",".join(a for _, a in to_pairs if a)
    cc_addrs = ",".join(a for _, a in cc_pairs if a)

    body = _extract_body(msg)[:BODY_TRUNC]
    in_reply_to = normalize_msgid(_hdr(msg, "In-Reply-To"))
    references = ",".join(_parse_reference_ids(_hdr(msg, "References")))

    return (
        mid,
        dt.isoformat() if dt else None,
        (from_addr or "").lower().strip(),
        (from_name or "").strip(),
        to_addrs.lower(),
        cc_addrs.lower(),
        subject,
        body,
        label,               # single label at insert time; merged at end
        in_reply_to,
        references,
        dt,                  # extra: datetime for the window check (not stored)
    )


def ingest(conn: sqlite3.Connection, mail_dir, log=print) -> dict:
    """STEP 1: parse every mbox (minus SKIP_MBOXES) into deduped `emails` rows.

    Dedupe is by Message-ID (synthesized for headerless messages). A message
    kept once accumulates every label it appeared under in `source_mboxes`. Only
    messages dated >= 2025-01-01 UTC enter the db.
    """
    mail_dir = Path(mail_dir)
    paths = sorted(p for p in mail_dir.glob("*.mbox") if p.stem not in SKIP_MBOXES)
    if not paths:
        raise FileNotFoundError(f"no mbox files found under {mail_dir}")

    seen_labels: dict[str, set] = defaultdict(set)   # kept msgid -> labels
    excluded_ids: set[str] = set()                    # out-of-window (dedup count)
    undated_ids: set[str] = set()                     # no parseable date (dedup)

    per_mbox_parsed: dict[str, int] = {}
    per_mbox_malformed: dict[str, int] = {}
    total_parsed = 0
    total_malformed = 0
    batch: list[tuple] = []

    def flush():
        if batch:
            conn.executemany(
                "INSERT OR IGNORE INTO emails "
                "(message_id, date, from_addr, from_name, to_addrs, cc_addrs, "
                " subject, body_text, source_mboxes, in_reply_to, references_ids) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
            conn.commit()
            batch.clear()

    for path in paths:
        label = _mbox_label(path)
        parsed = 0
        malformed = 0
        log(f"[ingest] opening {path.name} ({path.stat().st_size/1e9:.2f} GB) ...",
            flush=True)
        try:
            mbox = _open_mbox(path)
        except Exception as exc:  # noqa: BLE001 - unreadable mbox is a blocker per caller
            raise RuntimeError(f"cannot open mbox {path.name}: {exc}") from exc

        keys = mbox.keys()
        for key in keys:
            try:
                msg = mbox[key]
                row = _row_from_msg(msg, label)
            except Exception as exc:  # noqa: BLE001 - malformed msg: skip+count, never crash
                malformed += 1
                total_malformed += 1
                if malformed <= 5 or malformed % 500 == 0:
                    log(f"[ingest]   ! malformed {path.name}#{key}: "
                        f"{type(exc).__name__}: {exc}", flush=True)
                continue

            parsed += 1
            total_parsed += 1
            mid = row[0]
            dt = row[11]  # datetime or None (extra field)

            if mid in seen_labels:
                seen_labels[mid].add(label)          # duplicate: just add label
                continue
            if mid in excluded_ids or mid in undated_ids:
                continue                             # already decided, skip
            if dt is None:
                undated_ids.add(mid)
                continue
            if dt < WINDOW_START:
                excluded_ids.add(mid)
                continue

            seen_labels[mid].add(label)
            batch.append(row[:11])                    # drop the datetime extra
            if len(batch) >= BATCH_SIZE:
                flush()

            if parsed % 20000 == 0:
                log(f"[ingest]   {path.name}: {parsed} parsed, "
                    f"{len(seen_labels)} unique kept so far ...", flush=True)

        flush()
        mbox.close()
        per_mbox_parsed[label] = parsed
        per_mbox_malformed[label] = malformed
        log(f"[ingest] done {path.name}: {parsed} parsed, {malformed} malformed, "
            f"{len(seen_labels)} unique in-window kept cumulatively", flush=True)

    # Merge accumulated labels into source_mboxes for every kept row.
    log(f"[ingest] writing source_mboxes for {len(seen_labels)} unique rows ...",
        flush=True)
    updates = [(",".join(sorted(labels)), mid) for mid, labels in seen_labels.items()]
    for i in range(0, len(updates), BATCH_SIZE):
        conn.executemany("UPDATE emails SET source_mboxes=? WHERE message_id=?",
                         updates[i:i + BATCH_SIZE])
    conn.commit()

    stats = {
        "unique_in_window": len(seen_labels),
        "total_parsed": total_parsed,
        "total_malformed": total_malformed,
        "excluded_by_date": len(excluded_ids),
        "undated_excluded": len(undated_ids),
        "per_mbox_parsed": per_mbox_parsed,
        "per_mbox_malformed": per_mbox_malformed,
    }
    for k in ("unique_in_window", "total_parsed", "total_malformed",
              "excluded_by_date", "undated_excluded"):
        set_stat(conn, k, stats[k])
    import json
    set_stat(conn, "per_mbox_parsed", json.dumps(per_mbox_parsed))
    set_stat(conn, "per_mbox_malformed", json.dumps(per_mbox_malformed))
    conn.commit()
    return stats


# --------------------------------------------------------------------------- #
# STEP 2 — Threading (deterministic)
# --------------------------------------------------------------------------- #
class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def add(self, x):
        self.parent.setdefault(x, x)

    def find(self, x):
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:      # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _participants(from_addr, to_addrs, cc_addrs) -> set:
    s = set()
    if from_addr:
        s.add(from_addr)
    for field in (to_addrs, cc_addrs):
        if field:
            s.update(a for a in field.split(",") if a)
    return s


def thread(conn: sqlite3.Connection, log=print) -> dict:
    """STEP 2: assign every in-window email a thread_id.

    Primary join: In-Reply-To / References chains (only to message-ids present in
    the db). Fallback: normalized subject + >=1 shared participant + within 90
    days. Singletons become their own thread.
    """
    rows = conn.execute(
        "SELECT message_id, date, from_addr, to_addrs, cc_addrs, subject, "
        "in_reply_to, references_ids FROM emails"
    ).fetchall()
    log(f"[thread] loaded {len(rows)} emails", flush=True)

    ids = {r[0] for r in rows}
    uf = _UnionFind()
    for mid in ids:
        uf.add(mid)

    # --- Primary: reference-chain joining ---
    ref_links = 0
    for mid, _date, _fa, _to, _cc, _subj, irt, refs in rows:
        parents = []
        if irt:
            parents.append(irt)
        if refs:
            parents.extend(refs.split(","))
        for pid in parents:
            pid = pid.strip()
            if pid and pid in ids:
                uf.union(mid, pid)
                ref_links += 1
    log(f"[thread] primary: {ref_links} reference links joined", flush=True)

    # --- Fallback: subject + participant overlap within 90 days ---
    from datetime import timedelta
    window = timedelta(days=90)
    by_subject: dict[str, list] = defaultdict(list)
    for mid, date, fa, to, cc, subj, _irt, _refs in rows:
        nsubj = normalize_subject(subj)
        if not nsubj:
            continue  # never merge on empty subject
        dt = None
        if date:
            try:
                dt = datetime.fromisoformat(date)
            except ValueError:
                dt = None
        if dt is None:
            continue
        by_subject[nsubj].append((dt, mid, _participants(fa, to, cc)))

    fb_links = 0
    for nsubj, group in by_subject.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda t: t[0])
        active: list = []  # sliding 90-day window of (dt, mid, participants)
        for dt, mid, parts in group:
            active = [a for a in active if dt - a[0] <= window]
            for a_dt, a_mid, a_parts in active:
                if parts & a_parts:
                    uf.union(mid, a_mid)
                    fb_links += 1
                    break
            active.append((dt, mid, parts))
    log(f"[thread] fallback: {fb_links} subject/participant links joined", flush=True)

    # --- Assign sequential thread ids per union-find root ---
    root_to_tid: dict[str, int] = {}
    assignments: list[tuple] = []
    for mid in ids:
        root = uf.find(mid)
        tid = root_to_tid.get(root)
        if tid is None:
            tid = len(root_to_tid) + 1
            root_to_tid[root] = tid
        assignments.append((tid, mid))

    for i in range(0, len(assignments), BATCH_SIZE):
        conn.executemany("UPDATE emails SET thread_id=? WHERE message_id=?",
                         assignments[i:i + BATCH_SIZE])
    conn.commit()

    # --- Build threads table aggregates ---
    conn.execute("DELETE FROM threads")
    agg = conn.execute(
        "SELECT thread_id, COUNT(*), MIN(date), MAX(date) "
        "FROM emails GROUP BY thread_id"
    ).fetchall()
    # Root message = earliest-dated member (NULL dates sort last).
    root_rows = conn.execute(
        "SELECT thread_id, message_id, subject FROM emails "
        "ORDER BY thread_id, (date IS NULL), date"
    ).fetchall()
    thread_root: dict[int, tuple] = {}
    for tid, mid, subj in root_rows:
        if tid not in thread_root:
            thread_root[tid] = (mid, normalize_subject(subj))

    thread_rows = []
    for tid, size, first_date, last_date in agg:
        root_mid, subj_norm = thread_root.get(tid, (None, ""))
        thread_rows.append((tid, root_mid, subj_norm, size, first_date, last_date))
    conn.executemany(
        "INSERT INTO threads(thread_id, root_message_id, subject_norm, size, "
        "first_date, last_date) VALUES (?,?,?,?,?,?)", thread_rows)
    conn.commit()

    stats = {"threads": len(root_to_tid), "ref_links": ref_links, "fb_links": fb_links}
    set_stat(conn, "thread_count", stats["threads"])
    conn.commit()
    log(f"[thread] {stats['threads']} threads assigned", flush=True)
    return stats


# --------------------------------------------------------------------------- #
# STEP 3 — Contact candidates
# --------------------------------------------------------------------------- #
def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def _is_noise_localpart(addr: str) -> bool:
    local = addr.split("@", 1)[0].lower()
    return any(local == n or local.startswith(n) for n in NOISE_LOCALPARTS)


def aggregate_contacts(conn: sqlite3.Connection, log=print) -> list[dict]:
    """Aggregate a per-address contact model from the emails table."""
    rows = conn.execute(
        "SELECT from_addr, from_name, to_addrs, cc_addrs, date, source_mboxes "
        "FROM emails"
    ).fetchall()
    log(f"[contacts] scanning {len(rows)} emails", flush=True)

    contacts: dict[str, dict] = {}

    def _touch(addr: str, name: str):
        addr = (addr or "").lower().strip()
        if not addr or "@" not in addr:
            return None
        c = contacts.get(addr)
        if c is None:
            c = {
                "email": addr, "domain": _domain(addr), "names": set(),
                "message_count": 0, "count_received_from": 0, "count_sent_to": 0,
                "first_seen": None, "last_seen": None, "labels": set(),
            }
            contacts[addr] = c
        if name and name.strip():
            c["names"].add(name.strip())
        return c

    for from_addr, from_name, to_addrs, cc_addrs, date, source_mboxes in rows:
        labels = set(source_mboxes.split(",")) if source_mboxes else set()
        involved: dict[str, dict] = {}

        c = _touch(from_addr, from_name)
        if c is not None:
            c["count_received_from"] += 1
            involved[c["email"]] = c

        # Recipients on this message, deduped so a repeated address counts once.
        recip_addrs = set()
        for field in (to_addrs, cc_addrs):
            if not field:
                continue
            recip_addrs.update(a for a in field.split(",") if a)
        for addr in recip_addrs:
            c = _touch(addr, "")
            if c is not None:
                c["count_sent_to"] += 1
                involved[c["email"]] = c

        for addr, c in involved.items():
            c["message_count"] += 1
            c["labels"] |= labels
            if date:
                if c["first_seen"] is None or date < c["first_seen"]:
                    c["first_seen"] = date
                if c["last_seen"] is None or date > c["last_seen"]:
                    c["last_seen"] = date

    result = []
    for addr, c in contacts.items():
        labels = c["labels"]
        noise = _is_noise_localpart(addr)
        # Only-in-noise-category => noise hint.
        if not noise and labels and labels <= NOISE_CATEGORY_LABELS:
            noise = True
        result.append({
            "email": addr,
            "domain": c["domain"],
            "names": sorted(c["names"]),
            "message_count": c["message_count"],
            "count_received_from": c["count_received_from"],
            "count_sent_to": c["count_sent_to"],
            "first_seen": c["first_seen"],
            "last_seen": c["last_seen"],
            "labels": sorted(labels),
            "noise": noise,
            "role": "",
            "org": "",
        })
    result.sort(key=lambda d: (-d["message_count"], d["email"]))
    return result


def write_contacts_yaml(contacts: list[dict], out_path) -> Path:
    """Write contacts_draft.yaml (sorted by message count desc) with operator fields."""
    import yaml
    out_path = Path(out_path)
    payload = []
    for c in contacts:
        payload.append({
            "email": c["email"],
            "names": c["names"],
            "domain": c["domain"],
            "message_count": c["message_count"],
            "count_received_from": c["count_received_from"],
            "count_sent_to": c["count_sent_to"],
            "first_seen": c["first_seen"],
            "last_seen": c["last_seen"],
            "labels": c["labels"],
            "noise": c["noise"],
            "role": "",
            "org": "",
        })
    header = ("# Contact candidates — DRAFT for operator review (Brick A).\n"
              "# Auto-generated; deterministic. Fill in `role:` and `org:`; flip\n"
              "# `noise:` as needed. `noise:true` was auto-prefilled for no-reply\n"
              "# style senders and addresses seen ONLY inside Category "
              "Promotions/Updates/Personal/Travel.\n"
              "# Sorted by message_count desc.\n")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True,
                       default_flow_style=False, width=1000)
    return out_path


def contacts(conn: sqlite3.Connection, out_path, log=print) -> dict:
    """STEP 3: build contacts_draft.yaml. Returns summary stats."""
    cands = aggregate_contacts(conn, log=log)
    write_contacts_yaml(cands, out_path)
    noise_n = sum(1 for c in cands if c["noise"])
    log(f"[contacts] wrote {len(cands)} contacts ({noise_n} noise) -> {out_path}",
        flush=True)
    return {"contacts": len(cands), "noise": noise_n, "candidates": cands}


# --------------------------------------------------------------------------- #
# STEP 4 — Report
# --------------------------------------------------------------------------- #
def _thread_size_buckets(conn: sqlite3.Connection) -> dict:
    sizes = [r[0] for r in conn.execute("SELECT size FROM threads").fetchall()]
    buckets = {"singletons": 0, "2-5": 0, "6-20": 0, "20+": 0}
    for s in sizes:
        if s <= 1:
            buckets["singletons"] += 1
        elif s <= 5:
            buckets["2-5"] += 1
        elif s <= 20:
            buckets["6-20"] += 1
        else:
            buckets["20+"] += 1
    return buckets


def _label_signal_counts(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Return (operator-label -> msg count, category-label -> msg count)."""
    op_counts = {}
    for label in OPERATOR_LABELS:
        n = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE source_mboxes LIKE ? OR "
            "source_mboxes LIKE ? OR source_mboxes LIKE ? OR source_mboxes = ?",
            (f"{label},%", f"%,{label},%", f"%,{label}", label)).fetchone()[0]
        op_counts[label] = n
    cat_counts = {}
    for label in sorted(NOISE_CATEGORY_LABELS):
        n = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE source_mboxes LIKE ? OR "
            "source_mboxes LIKE ? OR source_mboxes LIKE ? OR source_mboxes = ?",
            (f"{label},%", f"%,{label},%", f"%,{label}", label)).fetchone()[0]
        cat_counts[label] = n
    return op_counts, cat_counts


def build_report(conn: sqlite3.Connection, contact_cands: list[dict]) -> str:
    """Render ingest_report.md. NEVER contains message bodies."""
    import json
    g = lambda k, d=0: get_stat(conn, k, d)  # noqa: E731
    per_parsed = json.loads(get_stat(conn, "per_mbox_parsed", "{}"))
    per_malformed = json.loads(get_stat(conn, "per_mbox_malformed", "{}"))
    unique = int(g("unique_in_window"))
    total_parsed = int(g("total_parsed"))
    total_malformed = int(g("total_malformed"))
    excluded = int(g("excluded_by_date"))
    undated = int(g("undated_excluded"))
    thread_count = int(g("thread_count", 0))
    buckets = _thread_size_buckets(conn)
    op_counts, cat_counts = _label_signal_counts(conn)

    out = ["# Mailbox ingest report — Brick A", ""]
    out.append("_Deterministic ingest of the Gmail Takeout export. Counts and "
               "addresses/domains only — no message bodies._")
    out.append("")

    out.append("## Totals")
    out.append("")
    out.append(f"- Messages parsed across all mboxes (with duplication): **{total_parsed}**")
    out.append(f"- Unique in-window messages kept (deduped): **{unique}**")
    if total_parsed:
        dupe_rate = 100.0 * (1 - unique / total_parsed) if total_parsed else 0
        out.append(f"- Apparent duplication / out-of-window rate: **{dupe_rate:.1f}%**")
    out.append(f"- Excluded by date (< 2025-01-01): **{excluded}**")
    out.append(f"- Excluded as undated (no parseable Date): **{undated}**")
    out.append(f"- Malformed / undecodable messages skipped: **{total_malformed}**")
    out.append("")

    out.append("## Per-mbox parsed counts")
    out.append("")
    out.append("| mbox (label) | parsed | malformed |")
    out.append("|---|---:|---:|")
    for label in sorted(per_parsed, key=lambda k: -per_parsed[k]):
        out.append(f"| {label} | {per_parsed[label]} | {per_malformed.get(label, 0)} |")
    out.append("")

    out.append("## Threads")
    out.append("")
    out.append(f"- Total threads: **{thread_count}**")
    out.append(f"- Singletons: **{buckets['singletons']}**")
    out.append(f"- 2–5 messages: **{buckets['2-5']}**")
    out.append(f"- 6–20 messages: **{buckets['6-20']}**")
    out.append(f"- 20+ messages: **{buckets['20+']}**")
    out.append("")

    out.append("## Label signals")
    out.append("")
    out.append("Operator-made labels (project-relevance hints):")
    out.append("")
    out.append("| operator label | messages |")
    out.append("|---|---:|")
    for label, n in op_counts.items():
        out.append(f"| {label} | {n} |")
    out.append("")
    out.append("Category-* labels (noise hints):")
    out.append("")
    out.append("| category label | messages |")
    out.append("|---|---:|")
    for label, n in cat_counts.items():
        out.append(f"| {label} | {n} |")
    out.append("")

    out.append("## Top 40 contacts")
    out.append("")
    out.append("| # | email | names | msgs | recv_from | sent_to | first_seen | last_seen | noise |")
    out.append("|---:|---|---|---:|---:|---:|---|---|:--:|")
    for i, c in enumerate(contact_cands[:40], 1):
        names = "; ".join(c["names"][:2])[:60]
        fs = (c["first_seen"] or "")[:10]
        ls = (c["last_seen"] or "")[:10]
        out.append(f"| {i} | {c['email']} | {names} | {c['message_count']} | "
                   f"{c['count_received_from']} | {c['count_sent_to']} | {fs} | {ls} | "
                   f"{'Y' if c['noise'] else ''} |")
    out.append("")

    # Top 20 unknown domains: domains of non-noise contacts, by message volume.
    dom_counts: dict[str, int] = defaultdict(int)
    for c in contact_cands:
        if c["noise"]:
            continue
        if c["domain"]:
            dom_counts[c["domain"]] += c["message_count"]
    top_domains = sorted(dom_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    out.append("## Top 20 domains (non-noise contacts, by message volume)")
    out.append("")
    out.append("| # | domain | messages |")
    out.append("|---:|---|---:|")
    for i, (dom, n) in enumerate(top_domains, 1):
        out.append(f"| {i} | {dom} | {n} |")
    out.append("")

    out.append(f"_Contacts modeled: {len(contact_cands)} unique addresses._")
    out.append("")
    return "\n".join(out)


def report(conn: sqlite3.Connection, contact_cands: list[dict], out_path,
           log=print) -> Path:
    """STEP 4: write ingest_report.md."""
    text = build_report(conn, contact_cands)
    out_path = Path(out_path)
    out_path.write_text(text, encoding="utf-8")
    log(f"[report] wrote {out_path}", flush=True)
    return out_path
