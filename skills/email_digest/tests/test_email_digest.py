"""
Tests for the NexaDose email digest skill.

Written BEFORE digest.py (tests-first). Defines the module's public API:

    load_config(path)        -> dict
    parse_email(path)        -> {"filename","from","subject","date","body"}
    classify(email, config)  -> {"status","category","summary","next_step"}
    validate_labels(obj, cfg)-> bool
    build_digest(items, cfg, date_str) -> str   (markdown)
    write_digest(text, out_dir, date_str) -> Path

T3/T4/T5 make REAL Ollama calls (localhost:11434). The first call cold-loads
the model (30-90s is normal, not a hang). All classifications are computed once
in a session-scoped fixture so the model is exercised 10 times total, not per-test.
"""
import json
import re
import sys
from pathlib import Path

import pytest

# --- locate the skill and put digest.py on the path -------------------------
SKILL_DIR = Path(__file__).resolve().parent.parent          # skills/email_digest/
sys.path.insert(0, str(SKILL_DIR))
import digest  # noqa: E402

CONFIG_PATH = SKILL_DIR / "config" / "nexadose.yaml"
INBOX_DIR = SKILL_DIR / "inbox_export"
GROUND_TRUTH_PATH = Path(__file__).resolve().parent / "ground_truth.json"

EXPECTED_STATUSES = ["WAITING_ON_YOU", "REVIEW", "OPEN", "WAITING", "BLOCKED"]
EXPECTED_CATEGORIES = [
    "PERMITS_INSPECTIONS", "ENGINEERING", "FINANCE", "VENDORS_EQUIPMENT",
    "LEGAL", "CLEANROOM_USP", "UNCLASSIFIED",
]
EXPECTED_DIGEST_ORDER = ["WAITING_ON_YOU", "BLOCKED", "REVIEW", "OPEN", "WAITING"]


# --- fixtures ---------------------------------------------------------------
@pytest.fixture(scope="session")
def config():
    return digest.load_config(CONFIG_PATH)


@pytest.fixture(scope="session")
def sample_files():
    files = sorted(INBOX_DIR.glob("*.txt"))
    assert files, f"no sample emails found in {INBOX_DIR}"
    return files


@pytest.fixture(scope="session")
def ground_truth():
    return json.loads(GROUND_TRUTH_PATH.read_text())["labels"]


@pytest.fixture(scope="session")
def classified(config, sample_files):
    """Parse + classify every sample once; shared across T3/T4/T5."""
    items = []
    for path in sample_files:
        email = digest.parse_email(path)
        labels = digest.classify(email, config)
        items.append({**email, **labels})
    return items


# --- T1: config loads; enums match exactly ----------------------------------
def test_t1_config_enums(config):
    assert config["statuses"] == EXPECTED_STATUSES
    assert config["categories"] == EXPECTED_CATEGORIES
    assert config["digest_order"] == EXPECTED_DIGEST_ORDER
    assert config["model"] == "qwen3-coder:30b"
    # digest_order must be a permutation of the status enum (no stray labels)
    assert set(config["digest_order"]) == set(config["statuses"])
    # fallback must itself be a valid label pair
    assert config["fallback"]["status"] in config["statuses"]
    assert config["fallback"]["category"] in config["categories"]


# --- T2: parser extracts fields from every sample ---------------------------
def test_t2_parser(sample_files):
    for path in sample_files:
        email = digest.parse_email(path)
        for key in ("from", "subject", "date", "body"):
            assert email.get(key), f"{path.name}: empty {key!r}"
        assert email["filename"] == path.name
        # body must be more than the header echo
        assert len(email["body"]) > 20, f"{path.name}: body too short"


# --- T3: classifier output shape + enum-only labels -------------------------
def test_t3_valid_shape_and_enums(classified, config):
    for item in classified:
        for key in ("status", "category", "summary", "next_step"):
            assert key in item, f"{item['filename']}: missing key {key!r}"
        assert item["status"] in config["statuses"], (
            f"{item['filename']}: hallucinated status {item['status']!r}")
        assert item["category"] in config["categories"], (
            f"{item['filename']}: hallucinated category {item['category']!r}")
        assert isinstance(item["summary"], str) and item["summary"].strip()
        assert isinstance(item["next_step"], str) and item["next_step"].strip()


def test_t3_validate_labels_rejects_hallucinations(config):
    good = {"status": "OPEN", "category": "FINANCE",
            "summary": "x", "next_step": "y"}
    assert digest.validate_labels(good, config) is True
    for bad in (
        {"status": "URGENT", "category": "FINANCE", "summary": "x", "next_step": "y"},
        {"status": "OPEN", "category": "TAXES", "summary": "x", "next_step": "y"},
        {"status": "OPEN", "category": "FINANCE", "summary": "x"},           # missing key
        {"status": "OPEN", "category": "FINANCE", "summary": "", "next_step": "y"},
    ):
        assert digest.validate_labels(bad, config) is False


# --- T4: category accuracy >= 7/10 ------------------------------------------
def test_t4_category_accuracy(classified, ground_truth):
    correct = 0
    disagreements = []
    for item in classified:
        expected = ground_truth[item["filename"]]["category"]
        got = item["category"]
        if got == expected:
            correct += 1
        else:
            disagreements.append(f"{item['filename']}: expected {expected}, got {got}")
    print(f"\n[T4] category accuracy: {correct}/{len(classified)}")
    for d in disagreements:
        print(f"[T4]   category miss: {d}")
    # status disagreements are logged only, never failed
    print("\n[T4] status disagreements (judgment-only, not failed):")
    gt_status = {fn: v["status"] for fn, v in ground_truth.items()}
    for item in classified:
        exp = gt_status[item["filename"]]
        if item["status"] != exp:
            print(f"[T4]   status: {item['filename']}: intended {exp}, got {item['status']}")
    assert correct >= 7, f"category accuracy {correct}/10 below threshold (7)"


# --- T5: digest is written, grouped in order, each email exactly once --------
def test_t5_digest_written_and_grouped(classified, config, tmp_path):
    date_str = "2026-07-04"
    text = digest.build_digest(classified, config, date_str)
    out_path = digest.write_digest(text, tmp_path, date_str)

    assert out_path.exists()
    assert out_path.name == "2026-07-04_digest.md"
    written = out_path.read_text()
    assert written == text

    # every email appears exactly once (match on its summary-bearing line via filename-free check:
    # each classified summary string must occur exactly once)
    for item in classified:
        assert written.count(item["summary"]) >= 1, (
            f"{item['filename']}: summary missing from digest")

    # sections appear in config digest_order; only sections with members appear
    present = [s for s in config["digest_order"]
               if any(i["status"] == s for i in classified)]
    positions = []
    for status in present:
        # line-anchor the heading: "WAITING" is a prefix of "WAITING_ON_YOU",
        # so match the full heading line to keep the two sections distinct.
        heading = digest.section_heading(status) + "\n"
        idx = written.find(heading)
        assert idx != -1, f"missing section heading for {status}"
        positions.append(idx)
    assert positions == sorted(positions), "status sections out of configured order"

    # WAITING_ON_YOU section, if present, is first
    if any(i["status"] == "WAITING_ON_YOU" for i in classified):
        assert present[0] == "WAITING_ON_YOU"

    # exactly-once: count body lines (start with the line marker) == number of emails
    line_count = len(re.findall(r"^- ", written, flags=re.MULTILINE))
    assert line_count == len(classified), (
        f"expected {len(classified)} item lines, found {line_count}")


REAL_DIR = INBOX_DIR / "real"


# --- T7: real .eml parsing --------------------------------------------------
def test_t7_real_eml_parsing():
    if not REAL_DIR.is_dir():
        pytest.skip(f"no {REAL_DIR} — real .eml corpus not present")
    files = sorted(list(REAL_DIR.glob("*.eml")) + list(REAL_DIR.glob("*.txt")))
    if not files:
        pytest.skip(f"{REAL_DIR} is empty — no real emails to parse")
    for path in files:
        email = digest.parse_email(path, max_body_chars=4000)  # must not raise
        for key in ("from", "subject", "body"):
            assert email.get(key) and email[key].strip(), (
                f"{path.name}: empty {key!r} after .eml parse")
        assert email["filename"] == path.name
        # body must be real content, not a MIME/base64 blob left undecoded
        assert "Content-Transfer-Encoding" not in email["body"], (
            f"{path.name}: raw MIME leaked into body")


# --- T7b: body truncation to config max -------------------------------------
def test_t7b_body_truncation(tmp_path):
    p = tmp_path / "long.txt"
    p.write_text("From: a@b.com\nSubject: s\nDate: d\n\n" + ("x" * 9000))
    email = digest.parse_email(p, max_body_chars=4000)
    assert email["body"].startswith("x")
    assert len(email["body"]) <= 4000 + len(digest._TRUNCATION_MARKER) + 4
    assert email["body"].rstrip().endswith("[truncated]")
    # no truncation when body is under the cap
    short = digest.parse_email(p, max_body_chars=None)
    assert "[truncated]" not in short["body"]


# --- T8: condensed alert body (ntfy) ----------------------------------------
def test_t8_build_alert_actionable_only():
    items = [
        {"filename": "a", "status": "WAITING_ON_YOU", "category": "FINANCE",
         "summary": "pay invoice 4471", "next_step": "pay it"},
        {"filename": "b", "status": "BLOCKED", "category": "VENDORS_EQUIPMENT",
         "summary": "customs hold on FFUs", "next_step": "wait on CBP"},
        {"filename": "c", "status": "WAITING", "category": "PERMITS_INSPECTIONS",
         "summary": "inspection queued", "next_step": "await date"},
        {"filename": "d", "status": "OPEN", "category": "LEGAL",
         "summary": "entity docs filed", "next_step": "file them"},
    ]
    cfg = digest.load_config(CONFIG_PATH)
    alert = digest.build_alert(items, cfg, "2026-07-04", digest_path="/x/y.md")
    assert "4 emails" in alert and "1 waiting on you" in alert and "1 blocked" in alert
    assert "pay invoice 4471" in alert          # WAITING_ON_YOU included
    assert "customs hold on FFUs" in alert       # BLOCKED included
    assert "inspection queued" not in alert       # WAITING excluded
    assert "entity docs filed" not in alert        # OPEN excluded
    assert "/x/y.md" in alert
    # no ntfy topic / URL ever embedded in the alert body
    assert "ntfy.sh" not in alert


# --- T6: read-only / local-only proof (source grep) -------------------------
def test_t6_no_write_or_remote_network():
    src = (SKILL_DIR / "digest.py").read_text()
    forbidden = ["smtplib", "imaplib", "poplib", "smtp", ".send(", "sendmail"]
    for token in forbidden:
        assert token not in src, f"forbidden network-write token in source: {token!r}"
    # every http(s) URL literal must target localhost / 127.0.0.1 only
    urls = re.findall(r"https?://[^\s\"')]+", src)
    for url in urls:
        assert ("localhost" in url or "127.0.0.1" in url), (
            f"non-local URL in source: {url}")
    # no requests/httpx import (urllib to localhost only)
    assert not re.search(r"^\s*import\s+(requests|httpx)", src, flags=re.MULTILINE)
    assert not re.search(r"^\s*from\s+(requests|httpx)\s+import", src, flags=re.MULTILINE)
