#!/usr/bin/env python3
"""
Sanity + unit tests for the mailbox pipeline (Brick A).

Three layers:
  1. pure-helper unit tests (fast, no db)
  2. a synthetic end-to-end run over tiny in-memory mbox files (proves dedupe,
     date filter, threading, contacts, and body-free report deterministically)
  3. real-db sanity checks (the brief's required suite) — skipped if the ingested
     db is absent, so a fresh clone collects cleanly.
"""
from __future__ import annotations

import mailbox
import sqlite3
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))
import pipeline as P  # noqa: E402

REAL_DB = SKILL / "db" / "mail.db"
REPORT = SKILL / "ingest_report.md"


# --------------------------------------------------------------------------- #
# Layer 1 — pure helpers
# --------------------------------------------------------------------------- #
def test_normalize_msgid():
    assert P.normalize_msgid("  <abc@x.com> ") == "abc@x.com"
    assert P.normalize_msgid("abc@x.com") == "abc@x.com"
    assert P.normalize_msgid("") == ""


def test_normalize_subject_strips_prefixes():
    assert P.normalize_subject("Re: Fwd:  RE: Hello World") == "hello world"
    assert P.normalize_subject("FW: Quote") == "quote"
    assert P.normalize_subject("   Plain  Subject ") == "plain subject"
    assert P.normalize_subject("") == ""


def test_to_utc_iso_window():
    dt = P.to_utc_iso("Mon, 03 Feb 2025 10:00:00 -0500")
    assert dt is not None and dt >= P.WINDOW_START
    old = P.to_utc_iso("Wed, 01 Jan 2020 00:00:00 +0000")
    assert old is not None and old < P.WINDOW_START
    assert P.to_utc_iso("not a date") is None
    assert P.to_utc_iso("") is None


def test_synthesize_msgid_stable():
    a = P.synthesize_msgid("d", "f", "s")
    b = P.synthesize_msgid("d", "f", "s")
    c = P.synthesize_msgid("d", "f", "OTHER")
    assert a == b and a != c and a.startswith("synth:")


def test_parse_reference_ids():
    assert P._parse_reference_ids("<a@x> <b@y>") == ["a@x", "b@y"]
    assert P._parse_reference_ids("") == []


def test_strip_html_no_tags():
    out = P._strip_html("<p>Hello <b>there</b></p><script>evil()</script>")
    assert "<" not in out and "evil" not in out and "Hello" in out


def test_unionfind():
    uf = P._UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("c")
    assert uf.find("a") != uf.find("z")


# --------------------------------------------------------------------------- #
# Layer 2 — synthetic end-to-end
# --------------------------------------------------------------------------- #
def _msg(mid, date, frm, to, subject, body, in_reply_to=None, references=None):
    m = EmailMessage()
    if mid is not None:
        m["Message-ID"] = mid
    if date:
        m["Date"] = date
    m["From"] = frm
    m["To"] = to
    m["Subject"] = subject
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    m.set_content(body)
    return m


def _write_mbox(path, messages):
    box = mailbox.mbox(str(path))
    box.lock()
    for m in messages:
        box.add(m)
    box.flush()
    box.unlock()
    box.close()


@pytest.fixture()
def synthetic(tmp_path):
    mail = tmp_path / "Mail"
    mail.mkdir()
    d1 = "Mon, 03 Feb 2025 10:00:00 -0500"
    d2 = "Mon, 03 Feb 2025 11:00:00 -0500"
    d3 = "Tue, 04 Feb 2025 09:00:00 -0500"
    old = "Wed, 01 Jan 2020 00:00:00 +0000"

    # Inbox: a 2-message reference thread + an old (excluded) message.
    inbox = [
        _msg("<m1@x>", d1, "Alice <alice@vendor.com>", "op@me.com", "Quote request",
             "Please send a quote body one."),
        _msg("<m2@x>", d2, "op@me.com", "alice@vendor.com", "Re: Quote request",
             "Sure here is the quote body two.", in_reply_to="<m1@x>"),
        _msg("<old@x>", old, "spam@old.com", "op@me.com", "Ancient",
             "This is an old excluded message."),
    ]
    # Important: m1 appears again (dedupe) + a subject-fallback sibling (no refs).
    important = [
        _msg("<m1@x>", d1, "Alice <alice@vendor.com>", "op@me.com", "Quote request",
             "Please send a quote body one."),
        _msg("<m3@x>", d3, "alice@vendor.com", "op@me.com", "RE: Quote request",
             "Following up on the quote thread."),
    ]
    # Category Promotions: a noise-only sender + a no-reply sender.
    promos = [
        _msg("<p1@x>", d1, "Deals <deals@promo.com>", "op@me.com", "Sale!",
             "Big promo body."),
        _msg("<p2@x>", d1, "no-reply@service.com", "op@me.com", "Receipt",
             "Auto receipt body."),
    ]
    # A message with no Message-ID (synthesized key).
    starred = [
        _msg(None, d1, "bob@partner.org", "op@me.com", "No id here",
             "Body of a message lacking a message id header."),
    ]
    _write_mbox(mail / "Inbox.mbox", inbox)
    _write_mbox(mail / "Important.mbox", important)
    _write_mbox(mail / "Category Promotions.mbox", promos)
    _write_mbox(mail / "Starred.mbox", starred)
    # Drafts + Trash MUST be skipped entirely.
    _write_mbox(mail / "Drafts.mbox", [
        _msg("<draft@x>", d1, "op@me.com", "x@y.com", "Draft", "should be skipped")])
    _write_mbox(mail / "Trash.mbox", [
        _msg("<trash@x>", d1, "op@me.com", "x@y.com", "Trash", "should be skipped")])

    db = tmp_path / "mail.db"
    conn = P.connect(db)
    P.init_db(conn)
    nolog = lambda *a, **k: None  # noqa: E731
    ing = P.ingest(conn, mail, log=nolog)
    thr = P.thread(conn, log=nolog)
    con = P.contacts(conn, tmp_path / "contacts_draft.yaml", log=nolog)
    P.report(conn, con["candidates"], tmp_path / "ingest_report.md", log=nolog)
    return {"conn": conn, "ing": ing, "thr": thr, "con": con, "tmp": tmp_path}


def test_synth_dedupe_and_date_filter(synthetic):
    conn = synthetic["conn"]
    # m1,m2,m3,p1,p2,synth(no-id) = 6 unique in-window; old excluded; drafts/trash skipped.
    n = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert n == 6, f"expected 6 unique in-window rows, got {n}"
    assert synthetic["ing"]["excluded_by_date"] == 1
    # No draft/trash content ever entered.
    assert conn.execute(
        "SELECT COUNT(*) FROM emails WHERE message_id IN ('draft@x','trash@x')"
    ).fetchone()[0] == 0


def test_synth_source_mboxes_accumulate(synthetic):
    conn = synthetic["conn"]
    src = conn.execute(
        "SELECT source_mboxes FROM emails WHERE message_id='m1@x'").fetchone()[0]
    assert set(src.split(",")) == {"Inbox", "Important"}


def test_synth_no_message_id_synthesized(synthetic):
    conn = synthetic["conn"]
    n = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE message_id LIKE 'synth:%'").fetchone()[0]
    assert n == 1


def test_synth_threading(synthetic):
    conn = synthetic["conn"]
    # m1,m2 join by references; m3 joins by subject+participant fallback -> one thread.
    tids = {mid: tid for mid, tid in conn.execute(
        "SELECT message_id, thread_id FROM emails WHERE message_id IN "
        "('m1@x','m2@x','m3@x')").fetchall()}
    assert tids["m1@x"] == tids["m2@x"] == tids["m3@x"]
    # Every email has a thread_id.
    assert conn.execute(
        "SELECT COUNT(*) FROM emails WHERE thread_id IS NULL").fetchone()[0] == 0


def test_synth_contacts_noise(synthetic):
    cands = {c["email"]: c for c in synthetic["con"]["candidates"]}
    assert cands["no-reply@service.com"]["noise"] is True
    assert cands["deals@promo.com"]["noise"] is True   # only in Category Promotions
    assert cands["alice@vendor.com"]["noise"] is False  # in Inbox/Important
    assert cands["alice@vendor.com"]["message_count"] >= 1


def test_synth_report_has_no_body(synthetic):
    report = (synthetic["tmp"] / "ingest_report.md").read_text(encoding="utf-8")
    for phrase in ("quote body one", "promo body", "lacking a message id"):
        assert phrase not in report


# --------------------------------------------------------------------------- #
# Layer 3 — real-db sanity suite (skipped if not yet ingested)
# --------------------------------------------------------------------------- #
real_db = pytest.mark.skipif(not REAL_DB.exists(),
                             reason="real mail.db not ingested yet")


@real_db
def test_real_db_nonempty():
    conn = sqlite3.connect(str(REAL_DB))
    assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] > 0
    conn.close()


@real_db
def test_real_no_duplicate_message_ids():
    conn = sqlite3.connect(str(REAL_DB))
    dupes = conn.execute(
        "SELECT COUNT(*) FROM (SELECT message_id FROM emails "
        "GROUP BY message_id HAVING COUNT(*) > 1)").fetchone()[0]
    conn.close()
    assert dupes == 0


@real_db
def test_real_every_email_has_thread():
    conn = sqlite3.connect(str(REAL_DB))
    n = conn.execute("SELECT COUNT(*) FROM emails WHERE thread_id IS NULL").fetchone()[0]
    conn.close()
    assert n == 0


@real_db
def test_real_date_filter_held():
    conn = sqlite3.connect(str(REAL_DB))
    # Every stored date is >= 2025-01-01 (ISO strings compare lexicographically).
    bad = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE date IS NOT NULL AND date < '2025-01-01'"
    ).fetchone()[0]
    conn.close()
    assert bad == 0


@real_db
def test_real_report_has_no_body():
    """Grep the report for distinctive substrings sampled from real bodies."""
    assert REPORT.exists(), "ingest_report.md missing"
    report = REPORT.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(REAL_DB))
    rows = conn.execute(
        "SELECT body_text FROM emails WHERE body_text IS NOT NULL "
        "AND length(body_text) > 120 ORDER BY message_id LIMIT 25").fetchall()
    conn.close()
    checked = 0
    for (body,) in rows:
        mid = len(body) // 2
        slice_ = body[mid:mid + 40].strip()
        if len(slice_) < 20:
            continue
        checked += 1
        assert slice_ not in report, f"body substring leaked into report: {slice_!r}"
    assert checked > 0, "no bodies sampled — cannot assert body-free report"
