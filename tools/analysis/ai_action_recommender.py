#!/usr/bin/env python3
"""
AI Action Recommender — OpenClaw

Reads the build_priority_watch.json output, takes the top items from
do_these_first / highest_priority_build_related_first_5, and calls
the Claude API to generate a concrete one-line recommended_action for each.

Writes:
  logs/ai_action_recommendations.json
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, UTC
from pathlib import Path


OPENAI_API_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "qwen2.5-coder:32b"
MAX_TOKENS = 512

SYSTEM_PROMPT = """\
You are an executive project assistant for a pharmacy build project at 1360 S Main St, Mansfield TX.
You receive a work item with a subject line and a current next step.
Your job: return a single short, concrete, action-oriented sentence (under 20 words) that tells
the project owner exactly what to do right now to unblock this item.
Be specific. Name what to send, who to contact, or what decision to make.
Do NOT be generic. Do NOT say "respond with requested info." Say what info specifically.
Return ONLY the one-line action. No preamble, no explanation, no punctuation beyond a period.
"""


def call_llm(subject: str, next_step: str, category: str, owners: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "OPENAI_API_KEY not set — skipping AI enrichment."

    user_msg = (
        f"Work item: {subject}\n"
        f"Category: {category}\n"
        f"Waiting on: {owners}\n"
        f"Current next step: {next_step}\n\n"
        "What is the single most important action to take right now?"
    )

    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    }

    req = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "No response").strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"API error {e.code}: {body[:200]}"
    except Exception as e:
        return f"Request failed: {e}"

def enrich_items(items: list[dict]) -> list[dict]:
    enriched = []
    for item in items:
        subject = item.get("representative_subject", "")
        next_step = item.get("next_step_current", "")
        category = item.get("categories_seen", "UNCLASSIFIED")
        owners = item.get("owners_seen", "Unknown")

        print(f"  -> Enriching: {subject[:60]}", flush=True)
        recommended_action = call_llm(subject, next_step, category, owners)

        enriched.append({
            **item,
            "recommended_action": recommended_action,
        })
    return enriched


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    bpw_path = logs_dir / "build_priority_watch.json"
    output_path = logs_dir / "ai_action_recommendations.json"

    if not bpw_path.exists():
        print(
            f"build_priority_watch.json not found at {bpw_path}. "
            "Run build_priority_watch.py first.",
            file=sys.stderr,
        )
        return 1

    bpw = json.loads(bpw_path.read_text(encoding="utf-8"))
    register = bpw.get("work_items_register", {})
    build_focus = register.get("build_focus_summary", {})

    do_these_first = build_focus.get("do_these_first", [])
    highest_priority = build_focus.get("highest_priority_build_related_first_5", [])

    seen_ids: set[str] = set()
    combined: list[dict] = []
    for item in do_these_first + highest_priority:
        wid = item.get("work_item_id", "")
        if wid not in seen_ids:
            seen_ids.add(wid)
            combined.append(item)

    combined = combined[:8]

    if not combined:
        print("No items to enrich.")
        payload = {
            "task": "ai_action_recommender",
            "status": "ok",
            "mode": "no_items",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "enriched_items": [],
        }
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {output_path}")
        return 0

    print(f"Enriching {len(combined)} items with Claude API...")
    enriched = enrich_items(combined)

    brief_lines = []
    for i, item in enumerate(enriched, 1):
        brief_lines.append(
            f"{i}. [{item.get('categories_seen', '?')}] "
            f"{item.get('representative_subject', '')[:55]} — "
            f"{item.get('recommended_action', '')}"
        )

    payload = {
        "task": "ai_action_recommender",
        "status": "ok",
        "mode": "claude_enriched",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "model_used": MODEL,
        "item_count": len(enriched),
        "executive_brief": brief_lines,
        "enriched_items": enriched,
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")

    print("\n=== Executive Action Brief ===")
    for line in brief_lines:
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
