#!/usr/bin/env python3
"""
Decision Master - Email Intelligence System for NexaDose Pharmacy Build
Processes all emails in email_raw/, extracts decisions, organizes attachments,
builds response queue, generates daily briefing, and syncs to Google Drive.
"""

import os
import sys
import json
import re
import hashlib
import time
import shutil
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent.parent
ENV_FILE   = Path("/root/.openclaw/.env")
INPUT_DIR  = BASE_DIR / "email_raw"
OUT_DIR    = BASE_DIR / "organized_attachments"
LOGS_DIR   = BASE_DIR / "logs"

DECISION_LOG    = LOGS_DIR / "decision_master.json"
RESPONSE_QUEUE  = LOGS_DIR / "response_queue.json"
DAILY_BRIEFING  = LOGS_DIR / "daily_briefing.txt"
CACHE_FILE      = LOGS_DIR / "decision_master_cache.json"
GDRIVE_CREDS    = BASE_DIR / ".gdrive_credentials.json"
GDRIVE_TOKEN    = BASE_DIR / ".gdrive_token.json"

# Attachment category → folder name (ORDER MATTERS — first match wins)
ATTACHMENT_FOLDERS = {
    "Cleanroom_Equipment":  r"cleanroom|clean.?room|clean.?suite|hvac|usp.{0,5}797|iso.{0,10}class|laminar|hepa|modular.*panel|pharmaceutical.*equip|negative.?pressure|positive.?pressure|airflow|sterile.*room",
    "Permits_Inspections":  r"permit|inspection|certificate|zoning|fire.?marshal|city.*approval|drc|environmental|code.?compliance|parking|straight.?4ward|phase.?1.?esa|phase.?1.?fee",
    "Legal_Contracts":      r"contract|agreement|loi|lease|amendment|closing|escrow|deed|plat|title|waiver|subordination|docusign|sign.*request|formation|resolution|llc|ein|federal.*tax|critical.?dates|commitment|purchase.?sale|psa|raiza|landlord",
    "Finance_Documents":    r"invoice|payment|w-?9|statement|appraisal|insurance|loan|sba|deposit|bank|finance|disbursement|reimbursement|builder.?s.?risk|bill|receipt|fee.?disclosure|estimate.*cost|budget|wire|check",
    "Engineering_Plans":    r"drawing|plan|design|survey|spec|arch|civil|electrical|sewer|septic|scope.?of.?work|renovation|layout|structural|mechanical|plumbing|stamped|construction",
    "Vendor_Quotes":        r"quote|proposal|bid|vendor|supplier|sourcing|equipment|material|supply|deliverable|purchase.?order|\bpo\b|consumable|pi.{0,5}nexadose|srm.?united",
}
DEFAULT_FOLDER = "General_Documents"

# Noise subjects to skip AI analysis (newsletters, system emails, etc.)
SKIP_PATTERNS = re.compile(
    r"unsubscribe|newsletter|noreply|no-reply|verify your|welcome to|"
    r"trial ends|happy holidays|merry christmas|best wishes|"
    r"new properties recommended|google workspace|link.*terms|"
    r"ayrshare|chatgpt weekly|n8n .*trial|verify.*account|"
    r"delivery status notification|security alert",
    re.I,
)

ANALYSIS_MODEL  = "claude-haiku-4-5-20251001"   # fast + cheap for per-thread analysis
BRIEFING_MODEL  = "claude-haiku-4-5-20251001"   # synthesize briefing
MAX_BODY_CHARS  = 6000   # chars per email body sent to AI
RATE_LIMIT_WAIT = 0.3    # seconds between API calls


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # Also pull from process environment (e.g. ANTHROPIC_API_KEY set by Claude Code)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if key not in env and os.environ.get(key):
            env[key] = os.environ[key]
    return env


def get_ai_client(env: dict):
    """Return (client, provider) — prefers Anthropic, falls back to OpenAI."""
    if env.get("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        return Anthropic(api_key=env["ANTHROPIC_API_KEY"]), "anthropic"
    if env.get("OPENAI_API_KEY"):
        from openai import OpenAI
        return OpenAI(api_key=env["OPENAI_API_KEY"]), "openai"
    raise RuntimeError("No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")


def ai_call(client, model: str, system: str, user: str, retries=3) -> str:
    provider = "anthropic" if hasattr(client, "messages") else "openai"
    for attempt in range(retries):
        try:
            if provider == "anthropic":
                resp = client.messages.create(
                    model=model,
                    max_tokens=1500,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return resp.content[0].text.strip()
            else:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    temperature=0.1,
                    max_tokens=1500,
                )
                return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"    [retry {attempt+1}] AI error: {e} — waiting {wait}s")
                time.sleep(wait)
            else:
                return f"ERROR: {e}"


def file_hash(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()


def folder_for_attachment(filename: str, subject: str = "") -> str:
    combined = (filename + " " + subject).lower()
    for folder, pattern in ATTACHMENT_FOLDERS.items():
        if re.search(pattern, combined, re.I):
            return folder
    # Extension fallback
    ext = Path(filename).suffix.lower()
    if ext in (".pdf", ".docx", ".doc"):
        return DEFAULT_FOLDER
    return DEFAULT_FOLDER


# ── Phase 1: Discover & Deduplicate Emails ────────────────────────────────────

def discover_emails(input_dir: Path) -> list[dict]:
    """
    Parse all .eml files. Deduplicate by Message-ID.
    Prefer emails from Inbox/Sent over All Mail duplicates.
    """
    print(f"\n[1/8] Scanning {input_dir} for .eml files...")

    priority_dirs = {"inbox", "sent", "important", "1360 documents"}
    by_msgid: dict[str, dict] = {}
    no_msgid: list[dict] = []

    all_files = list(input_dir.rglob("*.eml"))
    print(f"      Found {len(all_files)} .eml files")

    for path in all_files:
        try:
            with open(path, "rb") as f:
                msg = BytesParser(policy=policy.default).parse(f)
        except Exception as e:
            print(f"      WARN: could not parse {path.name}: {e}")
            continue

        subject = str(msg.get("subject") or "").strip()
        from_   = str(msg.get("from")    or "").strip()
        date_   = str(msg.get("date")    or "").strip()
        msg_id  = str(msg.get("message-id") or "").strip()
        refs    = str(msg.get("references")   or "") + " " + str(msg.get("in-reply-to") or "")
        in_priority = any(d in str(path).lower() for d in priority_dirs)

        try:
            dt = parsedate_to_datetime(date_)
            date_iso = dt.isoformat()
        except Exception:
            date_iso = date_

        record = {
            "path":       str(path),
            "subject":    subject,
            "from":       from_,
            "date":       date_iso,
            "message_id": msg_id,
            "refs":       refs.strip(),
            "priority":   in_priority,
            "_msg":       msg,
        }

        if msg_id:
            existing = by_msgid.get(msg_id)
            if existing is None or (in_priority and not existing["priority"]):
                by_msgid[msg_id] = record
        else:
            no_msgid.append(record)

    unique = list(by_msgid.values()) + no_msgid
    print(f"      Deduplicated to {len(unique)} unique emails")
    return unique


# ── Phase 2: Build Thread Groups ─────────────────────────────────────────────

def build_threads(emails: list[dict]) -> dict[str, list[dict]]:
    """Group emails into threads using References / In-Reply-To headers."""
    print("\n[2/8] Building email threads...")

    # Map message-id → email
    by_id: dict[str, dict] = {e["message_id"]: e for e in emails if e["message_id"]}

    # Union-find to cluster threads
    parent: dict[str, str] = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent.get(x, x)
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for email in emails:
        mid = email["message_id"]
        if not mid:
            continue
        for ref_id in email["refs"].split():
            ref_id = ref_id.strip("<>")
            if ref_id and ref_id in by_id:
                union(f"<{ref_id}>", mid)

    # Also group by normalized subject
    subj_to_root: dict[str, str] = {}
    for email in emails:
        mid = email["message_id"]
        if not mid:
            continue
        norm_subj = re.sub(r"^(re|fw|fwd|答复|回复)[\s:：]+", "", email["subject"], flags=re.I).strip().lower()
        root = subj_to_root.get(norm_subj)
        if root:
            union(root, mid)
        else:
            subj_to_root[norm_subj] = find(mid)

    threads: dict[str, list[dict]] = defaultdict(list)
    for email in emails:
        mid = email["message_id"]
        key = find(mid) if mid else f"no-id-{email['subject'][:40]}"
        threads[key].append(email)

    # Sort each thread chronologically
    for key in threads:
        threads[key].sort(key=lambda e: e["date"])

    print(f"      Grouped into {len(threads)} threads")
    return dict(threads)


# ── Phase 3: AI Analysis per Thread ──────────────────────────────────────────

ANALYSIS_SYSTEM = """You are an expert assistant helping a pharmacy business owner
understand their email communications about building a new pharmacy at
1360 S Main St, Mansfield TX 76063.

Analyze the email thread provided. Extract and return a JSON object with:
{
  "summary": "1-2 sentence overview of what this thread is about",
  "decisions": [
    {"decision": "...", "date": "...", "made_by": "...", "context": "..."}
  ],
  "unresolved": ["list of open questions or pending items"],
  "needs_response": true/false,
  "response_urgency": "high/medium/low/none",
  "response_reason": "why a response is needed (or null)",
  "draft_reply": "suggested reply if needs_response is true (or null)",
  "category": "PERMITS_INSPECTIONS|ENGINEERING|FINANCE|LEGAL|VENDORS_EQUIPMENT|CLEANROOM|GENERAL"
}
Return ONLY valid JSON, no markdown fences."""


def format_thread_for_ai(thread_emails: list[dict]) -> str:
    parts = []
    for i, email in enumerate(thread_emails[:8]):  # cap at 8 msgs per thread
        msg = email["_msg"]
        body_part = msg.get_body(preferencelist=("plain", "html"))
        if body_part:
            try:
                body = body_part.get_content()
                # Strip quoted replies (lines starting with >)
                lines = [l for l in body.splitlines() if not l.strip().startswith(">")]
                body = "\n".join(lines)[:MAX_BODY_CHARS]
            except Exception:
                body = "[body unreadable]"
        else:
            body = "[no body]"

        parts.append(
            f"--- Email {i+1} ---\n"
            f"From: {email['from']}\n"
            f"Date: {email['date']}\n"
            f"Subject: {email['subject']}\n\n"
            f"{body}\n"
        )
    return "\n".join(parts)


def analyze_threads(
    threads: dict[str, list[dict]],
    client,
    cache: dict,
) -> dict[str, dict]:
    """Run AI analysis on each thread (skip noise, use cache)."""
    print(f"\n[3/8] AI-analyzing {len(threads)} threads (cached: {len(cache)})...")

    results = dict(cache)
    new_count = 0
    skip_count = 0

    items = list(threads.items())
    for idx, (thread_key, emails) in enumerate(items):
        if thread_key in results:
            continue

        subject = emails[0]["subject"]
        if SKIP_PATTERNS.search(subject):
            skip_count += 1
            results[thread_key] = {
                "summary": f"Skipped (noise): {subject}",
                "decisions": [],
                "unresolved": [],
                "needs_response": False,
                "response_urgency": "none",
                "response_reason": None,
                "draft_reply": None,
                "category": "GENERAL",
            }
            continue

        thread_text = format_thread_for_ai(emails)
        result_str  = ai_call(client, ANALYSIS_MODEL, ANALYSIS_SYSTEM, thread_text)
        time.sleep(RATE_LIMIT_WAIT)

        try:
            result = json.loads(result_str)
        except json.JSONDecodeError:
            # Try to extract JSON from result
            m = re.search(r"\{.*\}", result_str, re.DOTALL)
            if m:
                try:
                    result = json.loads(m.group())
                except Exception:
                    result = {"summary": result_str[:200], "decisions": [], "unresolved": [],
                              "needs_response": False, "response_urgency": "none",
                              "response_reason": None, "draft_reply": None, "category": "GENERAL"}
            else:
                result = {"summary": result_str[:200], "decisions": [], "unresolved": [],
                          "needs_response": False, "response_urgency": "none",
                          "response_reason": None, "draft_reply": None, "category": "GENERAL"}

        # Attach thread metadata
        result["thread_subject"]  = subject
        result["thread_from"]     = list({e["from"] for e in emails})
        result["thread_dates"]    = [emails[0]["date"], emails[-1]["date"]]
        result["email_count"]     = len(emails)
        results[thread_key] = result
        new_count += 1

        if (new_count % 10) == 0 or new_count == 1:
            print(f"      [{idx+1}/{len(items)}] analyzed ({new_count} new, {skip_count} skipped)")

    print(f"      Done — {new_count} new analyses, {skip_count} skipped as noise")
    return results


# ── Phase 4: Write Decision Log ───────────────────────────────────────────────

def write_decision_log(analyses: dict[str, dict]) -> list[dict]:
    print("\n[4/8] Writing decision log...")
    all_decisions = []

    for thread_key, analysis in analyses.items():
        for decision in analysis.get("decisions", []):
            all_decisions.append({
                "what":      decision.get("decision", ""),
                "when":      decision.get("date", ""),
                "who":       decision.get("made_by", ""),
                "why":       decision.get("context", ""),
                "thread":    analysis.get("thread_subject", ""),
                "category":  analysis.get("category", "GENERAL"),
                "thread_key": thread_key,
            })

    DECISION_LOG.write_text(
        json.dumps(all_decisions, indent=2, ensure_ascii=False)
    )
    print(f"      Logged {len(all_decisions)} decisions → {DECISION_LOG}")
    return all_decisions


# ── Phase 5: Attachment Organization ─────────────────────────────────────────

# Patterns to skip — inline signature images, calendar invites, tiny noise files
SKIP_ATTACHMENTS = re.compile(
    r"^image\d+\.(jpg|jpeg|png|gif)$|"        # image001.jpg, image002.png (email signatures)
    r"^Outlook-[a-z0-9]+\.(jpg|png|gif)$|"    # Outlook inline images
    r"^img-[a-f0-9\-]+$|"                      # unnamed blobs
    r"\.ics$",                                  # calendar invites
    re.I,
)


def organize_attachments(emails: list[dict]) -> dict:
    print("\n[5/8] Extracting and organizing attachments...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seen_hashes: set[str] = set()
    stats = defaultdict(int)
    manifest = []

    for email_rec in emails:
        msg     = email_rec["_msg"]
        subject = email_rec["subject"]

        for part in msg.iter_attachments():
            filename = part.get_filename()
            if not filename:
                continue

            # Skip inline noise
            if SKIP_ATTACHMENTS.search(filename):
                stats["skipped_noise"] += 1
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            # Skip very small files (< 1KB — likely blank placeholders)
            if len(payload) < 512:
                stats["skipped_noise"] += 1
                continue

            h = file_hash(payload)
            if h in seen_hashes:
                stats["duplicates"] += 1
                continue
            seen_hashes.add(h)

            folder = folder_for_attachment(filename, subject)
            dest_dir = OUT_DIR / folder
            dest_dir.mkdir(parents=True, exist_ok=True)

            safe_name = sanitize_filename(filename)
            dest_path = dest_dir / safe_name

            # Avoid collisions with different content
            if dest_path.exists():
                stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
                i = 1
                while dest_path.exists():
                    dest_path = dest_dir / f"{stem}_{i}{suffix}"
                    i += 1

            dest_path.write_bytes(payload)
            stats[folder] += 1
            manifest.append({
                "filename": filename,
                "folder":   folder,
                "subject":  subject,
                "from":     email_rec["from"],
                "date":     email_rec["date"],
            })

    total = sum(v for k, v in stats.items() if k != "duplicates")
    print(f"      Saved {total} attachments ({stats['duplicates']} duplicates removed)")
    for folder, count in sorted(stats.items()):
        if folder != "duplicates":
            print(f"        {folder:30} {count} files")

    manifest_path = OUT_DIR / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return dict(stats)


# ── Phase 6: Response Queue ───────────────────────────────────────────────────

def build_response_queue(analyses: dict[str, dict], threads: dict[str, list]) -> list[dict]:
    print("\n[6/8] Building response queue...")

    queue = []
    urgency_rank = {"high": 0, "medium": 1, "low": 2, "none": 3}

    for thread_key, analysis in analyses.items():
        if not analysis.get("needs_response"):
            continue

        thread_emails = threads.get(thread_key, [])
        last_email    = thread_emails[-1] if thread_emails else {}

        queue.append({
            "priority":      analysis.get("response_urgency", "low"),
            "subject":       analysis.get("thread_subject", ""),
            "from":          last_email.get("from", ""),
            "last_date":     last_email.get("date", ""),
            "reason":        analysis.get("response_reason", ""),
            "draft_reply":   analysis.get("draft_reply", ""),
            "category":      analysis.get("category", "GENERAL"),
            "thread_summary": analysis.get("summary", ""),
        })

    queue.sort(key=lambda x: urgency_rank.get(x["priority"], 3))
    RESPONSE_QUEUE.write_text(json.dumps(queue, indent=2, ensure_ascii=False))
    print(f"      {len(queue)} emails need response → {RESPONSE_QUEUE}")
    high = sum(1 for e in queue if e["priority"] == "high")
    med  = sum(1 for e in queue if e["priority"] == "medium")
    print(f"        High: {high}  Medium: {med}  Low: {len(queue)-high-med}")
    return queue


# ── Phase 7: Daily Briefing ───────────────────────────────────────────────────

BRIEFING_SYSTEM = """You are a strategic advisor for a pharmacy business owner
building a new pharmacy at 1360 S Main St, Mansfield TX 76063 (NexaDose RX).

Given a JSON summary of email threads, decisions, and pending items,
write a clear, concise daily briefing in this exact format:

NEXADOSE PHARMACY BUILD — DAILY BRIEFING
Date: {date}

TOP 3 DECISIONS MADE RECENTLY
1. [decision] — [who] — [date]
2. ...
3. ...

TOP 5 ITEMS NEEDING YOUR ATTENTION
1. [item] — [urgency: HIGH/MEDIUM/LOW]
2. ...
...

ACTIVE BLOCKERS ON THE PHARMACY BUILD
• [blocker] — [what is needed to unblock]
...

QUICK STATS
• Emails analyzed: X | Decisions logged: Y | Emails needing response: Z

Keep it factual, direct, and actionable. No fluff."""


def generate_daily_briefing(
    analyses: dict,
    decisions: list[dict],
    response_queue: list[dict],
    client,
    stats: dict,
) -> str:
    print("\n[7/8] Generating daily briefing...")

    # Summarize for the AI — pick the most recent/important threads
    recent_decisions = sorted(decisions, key=lambda d: d.get("when", ""), reverse=True)[:20]
    urgent_items = [
        {"subject": r["subject"], "reason": r["reason"], "priority": r["priority"]}
        for r in response_queue[:15]
    ]
    unresolved = []
    for a in analyses.values():
        for item in a.get("unresolved", [])[:2]:
            unresolved.append({"thread": a.get("thread_subject", ""), "item": item})
        if len(unresolved) >= 20:
            break

    payload = {
        "recent_decisions":    recent_decisions,
        "urgent_response_needed": urgent_items,
        "unresolved_items":    unresolved[:20],
        "total_emails_analyzed": len(analyses),
        "total_decisions":     len(decisions),
        "emails_needing_response": len(response_queue),
    }

    prompt = (
        f"Today's date: {datetime.now().strftime('%B %d, %Y')}\n\n"
        + json.dumps(payload, indent=2)
    )

    system = BRIEFING_SYSTEM.replace("{date}", datetime.now().strftime("%B %d, %Y"))
    briefing = ai_call(client, BRIEFING_MODEL, system, prompt)
    DAILY_BRIEFING.write_text(briefing)
    print(f"      Briefing written → {DAILY_BRIEFING}")
    return briefing


# ── Phase 8: Google Drive Sync ────────────────────────────────────────────────

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def gdrive_sync(organized_dir: Path, creds_file: Path, token_file: Path):
    print("\n[8/8] Google Drive sync...")

    if not creds_file.exists():
        print(f"""
      ⚠  Google Drive sync SKIPPED — credentials not found.

      To enable Google Drive sync:
      1. Go to https://console.cloud.google.com/
      2. Create a project → Enable the Google Drive API
      3. Create OAuth2 credentials (Desktop app) → Download as JSON
      4. Save the file to: {creds_file}
      5. Re-run this script — it will open a browser to authorize

      Your organized attachments are saved locally at:
        {organized_dir}
""")
        return

    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        print(f"      SKIP: Google API libs not available: {e}")
        return

    # Auth
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), GDRIVE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), GDRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())

    service = build("drive", "v3", credentials=creds)

    def get_or_create_folder(name: str, parent_id: Optional[str] = None) -> str:
        q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        results = service.files().list(q=q, fields="files(id)").execute()
        if results["files"]:
            return results["files"][0]["id"]
        meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            meta["parents"] = [parent_id]
        folder = service.files().create(body=meta, fields="id").execute()
        return folder["id"]

    root_id = get_or_create_folder("NexaDose_Pharmacy_Build")
    uploaded = 0

    for subfolder in organized_dir.iterdir():
        if not subfolder.is_dir() or subfolder.name.startswith("_"):
            continue
        folder_id = get_or_create_folder(subfolder.name, root_id)

        for file_path in subfolder.iterdir():
            if not file_path.is_file():
                continue
            try:
                media = MediaFileUpload(str(file_path), resumable=True)
                service.files().create(
                    body={"name": file_path.name, "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                ).execute()
                uploaded += 1
            except Exception as e:
                print(f"      WARN: failed to upload {file_path.name}: {e}")

    print(f"      Uploaded {uploaded} files to Google Drive → NexaDose_Pharmacy_Build/")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_cache(data: dict):
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  DECISION MASTER — NexaDose Email Intelligence System")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Setup
    env = load_env()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        client, provider = get_ai_client(env)
        print(f"  AI provider: {provider} / {ANALYSIS_MODEL}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Phase 1: Discover emails
    emails = discover_emails(INPUT_DIR)

    # Phase 2: Build threads
    threads = build_threads(emails)

    # Phase 3: AI analysis (with cache)
    cache = load_cache()
    analyses = analyze_threads(threads, client, cache)
    save_cache(analyses)

    # Phase 4: Decision log
    decisions = write_decision_log(analyses)

    # Phase 5: Attachment organization
    att_stats = organize_attachments(emails)

    # Phase 6: Response queue
    response_queue = build_response_queue(analyses, threads)

    # Phase 7: Daily briefing
    briefing = generate_daily_briefing(analyses, decisions, response_queue, client, att_stats)

    # Phase 8: Google Drive sync
    gdrive_sync(OUT_DIR, GDRIVE_CREDS, GDRIVE_TOKEN)

    # Final summary
    print("\n" + "=" * 60)
    print("  COMPLETE")
    print("=" * 60)
    print(f"  Decisions logged:        {len(decisions)}")
    print(f"  Emails need response:    {len(response_queue)}")
    total_attachments = sum(v for k, v in att_stats.items() if k not in ("duplicates",))
    print(f"  Attachments organized:   {total_attachments}")
    print(f"  Duplicates removed:      {att_stats.get('duplicates', 0)}")
    print(f"\n  Outputs:")
    print(f"    {DECISION_LOG}")
    print(f"    {RESPONSE_QUEUE}")
    print(f"    {DAILY_BRIEFING}")
    print(f"    {OUT_DIR}/")
    print()

    # Print briefing preview
    print("\n--- DAILY BRIEFING PREVIEW ---\n")
    print(briefing[:2000])
    if len(briefing) > 2000:
        print(f"\n... (see {DAILY_BRIEFING} for full briefing)")


if __name__ == "__main__":
    main()
