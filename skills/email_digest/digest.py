#!/usr/bin/env python3
"""
NexaDose email digest — a READ-ONLY, LOCAL-ONLY morning triage tool.

Pipeline:  read exported .eml/.txt files from a directory
        -> classify each with a local Ollama model (status + category + summary + next step)
        -> write a mobile-scannable markdown digest grouped by status.

HARD GUARANTEES (enforced by tests/test_email_digest.py::T6):
  * READ-ONLY  — never sends, deletes, labels, moves, or replies to any email.
                 v1 reads files only; there is no mailbox connection of any kind.
  * LOCAL-ONLY — the sole network call is to the local Ollama endpoint on
                 localhost:11434. No cloud APIs.
  * CONFIG-DRIVEN — the taxonomy (status/category enums, ordering, model, paths)
                 is loaded from a YAML config; nothing tenant-specific is hardcoded.
                 The machinery is tenant-agnostic; point it at a different config
                 to serve a different tenant.

Cold start: the first Ollama call loads the model into VRAM (30-90s is normal,
not a hang).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from email import policy
from email.parser import BytesParser
from pathlib import Path

import yaml

# The ONLY network endpoint this module may ever contact. Local Ollama only.
OLLAMA_URL = "http://localhost:11434/api/chat"

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]  # skills/email_digest/ -> workspace


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path) -> dict:
    """Load and lightly validate the tenant config YAML."""
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("statuses", "categories", "digest_order", "fallback", "model",
                "input_dir", "output_dir"):
        if key not in cfg:
            raise ValueError(f"config missing required key: {key!r}")
    if set(cfg["digest_order"]) != set(cfg["statuses"]):
        raise ValueError("digest_order must be a permutation of statuses")
    if cfg["fallback"]["status"] not in cfg["statuses"]:
        raise ValueError("fallback.status is not a valid status")
    if cfg["fallback"]["category"] not in cfg["categories"]:
        raise ValueError("fallback.category is not a valid category")
    return cfg


def _resolve_dir(path_str: str) -> Path:
    """Resolve a config path: absolute as-is, else relative to the workspace root."""
    p = Path(path_str)
    return p if p.is_absolute() else (WORKSPACE_ROOT / p)


# --------------------------------------------------------------------------- #
# Parsing (READ-ONLY: opens files, never a mailbox)
# --------------------------------------------------------------------------- #
_HEADER_KEYS = ("from", "subject", "date")
_TRUNCATION_MARKER = "\n…[truncated]"


def _truncate_body(body: str, max_body_chars) -> str:
    """Bound the body length before it reaches the classifier."""
    if max_body_chars and len(body) > max_body_chars:
        return body[:max_body_chars].rstrip() + _TRUNCATION_MARKER
    return body


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


def _extract_eml_body(msg) -> str:
    """Pull the best-effort text body from a parsed email message.

    Prefers the first text/plain part; falls back to a stripped text/html part.
    Skips attachments. Relies on the stdlib default policy to decode base64 /
    quoted-printable and the declared charset for us.
    """
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


def _safe_get_content(part) -> str:
    """Decode a MIME part to text, tolerant of odd charsets."""
    try:
        return part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def _parse_eml(path: Path, max_body_chars) -> dict:
    """Parse an RFC-822 .eml file via the stdlib email module."""
    with open(path, "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)

    def _hdr(name: str) -> str:
        val = msg[name]
        return "" if val is None else str(val).strip()

    body = _truncate_body(_extract_eml_body(msg), max_body_chars)
    return {
        "filename": path.name,
        "from": _hdr("From"),
        "subject": _hdr("Subject"),
        "date": _hdr("Date"),
        "body": body,
    }


def _parse_txt(path: Path, max_body_chars) -> dict:
    """Parse a simple exported .txt file: leading `Key: value` headers + body."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    headers = {"from": "", "subject": "", "date": ""}
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "":
            body_start = i + 1
            break
        if ":" in line:
            key, _, val = line.partition(":")
            k = key.strip().lower()
            if k in headers:
                headers[k] = val.strip()
        body_start = i + 1

    body = "\n".join(lines[body_start:]).strip()
    if not body:  # no blank-line separator; treat whole file as body
        body = raw.strip()

    return {
        "filename": path.name,
        "from": headers["from"],
        "subject": headers["subject"],
        "date": headers["date"],
        "body": _truncate_body(body, max_body_chars),
    }


def parse_email(path, max_body_chars=None) -> dict:
    """Extract from/subject/date/body from an exported .eml or .txt file.

    ``.eml`` files are parsed as RFC-822 (decoded headers, text/plain preferred,
    text/html stripped as fallback, multipart + base64/quoted-printable handled).
    Anything else is treated as a simple header/blank-line/body .txt export.
    If ``max_body_chars`` is set, the body is truncated to that length.
    """
    path = Path(path)
    if path.suffix.lower() == ".eml":
        return _parse_eml(path, max_body_chars)
    return _parse_txt(path, max_body_chars)


def load_emails(input_dir, max_body_chars=None) -> list[dict]:
    """Parse every .eml/.txt file in a directory, sorted by name."""
    input_dir = Path(input_dir)
    files = sorted(list(input_dir.glob("*.txt")) + list(input_dir.glob("*.eml")))
    return [parse_email(p, max_body_chars) for p in files]


# --------------------------------------------------------------------------- #
# Classification (LOCAL-ONLY: localhost Ollama, strict JSON, one retry, fallback)
# --------------------------------------------------------------------------- #
def _taxonomy_block(config: dict) -> str:
    """Render the config taxonomy into the prompt (never hardcoded).

    Status labels prefer the fuller ``definitions`` block (which draws the hard
    BLOCKED-vs-WAITING / REVIEW-vs-WAITING_ON_YOU lines) and fall back to the
    short ``status_descriptions`` glosses.
    """
    defs = config.get("definitions", {})
    sd = config.get("status_descriptions", {})
    cd = config.get("category_descriptions", {})
    st = "\n".join(
        f"  - {s}: {defs.get(s) or sd.get(s, '')}".rstrip() for s in config["statuses"])
    ct = "\n".join(f"  - {c}: {cd.get(c, '')}".rstrip() for c in config["categories"])
    return f"STATUS values (pick exactly one):\n{st}\n\nCATEGORY values (pick exactly one):\n{ct}"


def build_prompt(email: dict, config: dict) -> tuple[str, str]:
    """Return (system, user) messages for the classifier."""
    system = (
        "You are an email triage assistant for a pharmacy facility build project. "
        "Classify one email into a project management STATUS and CATEGORY, using "
        "ONLY the labels provided. Respond with a single JSON object and nothing "
        "else. Do not invent labels outside the provided lists."
    )
    user = (
        f"{_taxonomy_block(config)}\n\n"
        "Return a JSON object with EXACTLY these keys:\n"
        '  "status": one STATUS label from the list above\n'
        '  "category": one CATEGORY label from the list above\n'
        '  "summary": a single concise sentence describing the email\n'
        '  "next_step": a short suggested next action for the owner\n\n'
        "EMAIL:\n"
        f"From: {email['from']}\n"
        f"Subject: {email['subject']}\n"
        f"Date: {email['date']}\n"
        f"Body:\n{email['body']}\n"
    )
    return system, user


def _ollama_chat(model: str, system: str, user: str, timeout: int = 300) -> str:
    """POST one chat completion to the LOCAL Ollama endpoint; return content string."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # localhost only
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "")


def validate_labels(obj, config: dict) -> bool:
    """True iff obj has all four keys, valid enum labels, and non-empty text."""
    if not isinstance(obj, dict):
        return False
    for key in ("status", "category", "summary", "next_step"):
        if key not in obj or not isinstance(obj[key], str) or not obj[key].strip():
            return False
    return (obj["status"] in config["statuses"]
            and obj["category"] in config["categories"])


def _parse_json_object(text: str):
    """Defensive parse: whole string, else the first {...} span."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def classify(email: dict, config: dict) -> dict:
    """Classify one email. Invalid model output -> retry once -> UNCLASSIFIED/OPEN fallback."""
    system, user = build_prompt(email, config)
    for attempt in range(2):  # initial + one retry
        try:
            content = _ollama_chat(config["model"], system, user)
        except Exception as exc:  # noqa: BLE001 - network/timeout -> fall through to fallback
            print(f"[classify] {email['filename']}: Ollama error: {exc}", file=sys.stderr)
            break
        obj = _parse_json_object(content)
        if obj is not None and validate_labels(obj, config):
            return {
                "status": obj["status"],
                "category": obj["category"],
                "summary": obj["summary"].strip(),
                "next_step": obj["next_step"].strip(),
            }
        if attempt == 0:
            user += ("\n\nYour previous response was invalid. Reply with ONLY a JSON "
                     "object using labels strictly from the provided lists.")

    # Fallback: never emit a hallucinated label.
    fb = config["fallback"]
    return {
        "status": fb["status"],
        "category": fb["category"],
        "summary": email.get("subject") or "(no subject)",
        "next_step": "Review this email manually — automatic classification failed.",
    }


# --------------------------------------------------------------------------- #
# Digest rendering
# --------------------------------------------------------------------------- #
def section_heading(status: str) -> str:
    """Markdown heading for a status section.

    Carries the exact enum token (e.g. ``## WAITING_ON_YOU``) so headings are
    greppable and unambiguous — ``WAITING`` is a prefix of ``WAITING_ON_YOU``,
    so the full token must appear to keep the two sections distinct.
    """
    return f"## {status}"


def build_digest(items: list[dict], config: dict, date_str: str) -> str:
    """Render items into markdown grouped by status in configured order.

    Each item renders as one scannable line:
        - CATEGORY — one-line summary — suggested next step
    Every email appears exactly once; only status sections with members appear.
    """
    out = [f"# NexaDose morning digest — {date_str}", ""]
    total = len(items)
    action = sum(1 for i in items if i["status"] in ("WAITING_ON_YOU", "BLOCKED"))
    out.append(f"_{total} emails · {action} need attention (WAITING_ON_YOU / BLOCKED)_")
    out.append("")

    for status in config["digest_order"]:
        group = [i for i in items if i["status"] == status]
        if not group:
            continue
        out.append(section_heading(status))
        for it in group:
            # Render summary/next_step verbatim (already stripped in classify) so
            # each email's text appears in the digest unaltered.
            out.append(f"- **{it['category']}** — {it['summary']} — "
                       f"_next:_ {it['next_step']}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def build_alert(items: list[dict], config: dict, date_str: str,
                digest_path=None, max_chars: int = 3500) -> str:
    """Condensed push-notification body: the actionable sections only.

    Includes a one-line count summary plus the WAITING_ON_YOU and BLOCKED items
    (the two "need attention" statuses), then a pointer to the full on-disk
    digest. Bounded to ``max_chars`` to stay under ntfy's message cap. Contains
    NO secrets — the ntfy topic lives only in the delivery layer (tools/notify.sh).
    """
    total = len(items)
    woy = [i for i in items if i["status"] == "WAITING_ON_YOU"]
    blk = [i for i in items if i["status"] == "BLOCKED"]
    lines = [f"NexaDose digest {date_str}: {total} emails · "
             f"{len(woy)} waiting on you, {len(blk)} blocked."]
    for status, group in (("WAITING_ON_YOU", woy), ("BLOCKED", blk)):
        if not group:
            continue
        lines.append("")
        lines.append(f"{status}:")
        for it in group:
            lines.append(f"• {it['category']}: {it['summary']}")
    if digest_path is not None:
        lines.append("")
        lines.append(f"Full digest on disk: {digest_path}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…[truncated — see full digest on disk]"
    return text


def write_digest(text: str, output_dir, date_str: str) -> Path:
    """Write the digest to <output_dir>/<date>_digest.md (creates dir). Returns the path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{date_str}_digest.md"
    out_path.write_text(text, encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _today() -> str:
    # Import here so the module imports cleanly in test collection.
    import datetime
    return datetime.date.today().isoformat()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NexaDose read-only email digest")
    default_cfg = Path(__file__).resolve().parent / "config" / "nexadose.yaml"
    ap.add_argument("--config", default=str(default_cfg))
    ap.add_argument("--input", default=None, help="override input_dir")
    ap.add_argument("--output", default=None, help="override output_dir")
    ap.add_argument("--date", default=None, help="digest date (YYYY-MM-DD)")
    ap.add_argument("--alert-out", default=None,
                    help="also write a condensed push-notification body to this path")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    input_dir = Path(args.input) if args.input else _resolve_dir(config["input_dir"])
    output_dir = Path(args.output) if args.output else _resolve_dir(config["output_dir"])
    date_str = args.date or _today()
    max_body_chars = config.get("max_body_chars", 4000)

    emails = load_emails(input_dir, max_body_chars)
    if not emails:
        print(f"[digest] no emails found in {input_dir}", file=sys.stderr)
        return 1
    print(f"[digest] classifying {len(emails)} emails from {input_dir} ...", flush=True)

    items = []
    for email in emails:
        labels = classify(email, config)
        items.append({**email, **labels})
        print(f"[digest]   {email['filename']}: {labels['status']} / {labels['category']}",
              flush=True)

    text = build_digest(items, config, date_str)
    out_path = write_digest(text, output_dir, date_str)
    print(f"[digest] wrote {out_path}", flush=True)

    if args.alert_out:
        alert = build_alert(items, config, date_str, digest_path=out_path)
        Path(args.alert_out).write_text(alert, encoding="utf-8")
        print(f"[digest] wrote alert body {args.alert_out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
