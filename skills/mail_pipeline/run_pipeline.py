#!/usr/bin/env python3
"""
CLI runner for the mailbox pipeline (Brick A). Chains the four deterministic
stages and prints live progress (flush=True) so a long ingest shows liveness.

Usage:
  python3 -u run_pipeline.py [--mail-dir DIR] [--db PATH]
                             [--steps ingest,thread,contacts,report]

Runtime: STEP 1 on a ~14 GB export can take 60-90 minutes; this is expected.
Nothing here calls a model or a remote service.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_MAIL_DIR = P.Path("/root/.openclaw/workspace/mail_export/Takeout/Mail")
DEFAULT_DB = HERE / "db" / "mail.db"
CONTACTS_OUT = HERE / "contacts_draft.yaml"
REPORT_OUT = HERE / "ingest_report.md"


def _log(msg, flush=True):
    print(msg, flush=flush)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mailbox pipeline — Brick A")
    ap.add_argument("--mail-dir", default=str(DEFAULT_MAIL_DIR))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--steps", default="ingest,thread,contacts,report",
                    help="comma-separated subset of ingest,thread,contacts,report")
    args = ap.parse_args(argv)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]

    conn = P.connect(args.db)
    P.init_db(conn)

    t0 = time.time()
    if "ingest" in steps:
        _log(f"=== STEP 1 ingest (mail_dir={args.mail_dir}) ===")
        s = P.ingest(conn, args.mail_dir, log=_log)
        _log(f"[ingest] STATS: {s['unique_in_window']} unique / "
             f"{s['total_parsed']} parsed / {s['excluded_by_date']} excluded-by-date "
             f"/ {s['undated_excluded']} undated / {s['total_malformed']} malformed "
             f"({time.time()-t0:.0f}s)")

    if "thread" in steps:
        _log("=== STEP 2 thread ===")
        s = P.thread(conn, log=_log)
        _log(f"[thread] STATS: {s['threads']} threads ({time.time()-t0:.0f}s)")

    cands = None
    if "contacts" in steps:
        _log("=== STEP 3 contacts ===")
        s = P.contacts(conn, CONTACTS_OUT, log=_log)
        cands = s["candidates"]
        _log(f"[contacts] STATS: {s['contacts']} contacts, {s['noise']} noise "
             f"({time.time()-t0:.0f}s)")

    if "report" in steps:
        _log("=== STEP 4 report ===")
        if cands is None:
            cands = P.aggregate_contacts(conn, log=_log)
        P.report(conn, cands, REPORT_OUT, log=_log)
        _log(f"[report] done ({time.time()-t0:.0f}s)")

    conn.close()
    _log(f"=== pipeline complete in {time.time()-t0:.0f}s ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
