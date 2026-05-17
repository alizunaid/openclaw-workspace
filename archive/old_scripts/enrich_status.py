#!/usr/bin/env python3
import csv, re
from pathlib import Path

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.csv"
OUT_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.enriched.csv"

# --- Core signals ---
AUTO_RX = re.compile(r"\b(automatic reply|auto-?reply|out of office|do not reply|no-reply@)\b", re.I)

# Past-tense completion / execution
DONE_RX = re.compile(
    r"\b(i\s+have\s+)?(submitted|sent|completed|paid|processed|approved|delivered|filed|uploaded)\b",
    re.I,
)

# Deliverables / artifact receipt (often should be INFO, not OPEN)
DELIVER_RX = re.compile(
    r"\b(attached is|please find attached|see attached|enclosed|here is|here are|as discussed, attached)\b",
    re.I,
)

# Future / commitments (waiting on someone else)
FUTURE_RX = re.compile(
    r"\b(we('| a)?ll|will|next week|tomorrow|today|by (?:end of day|eod)|soon|asap|once|after)\b",
    re.I,
)

# Asking *you* for input/action
REQUEST_INPUT_RX = re.compile(
    r"\b(if you could|could you|can you|please|kindly)\s+(send|share|provide|confirm|review|sign|approve|forward|attach)\b"
    r"|\b(need|needed|require|required)\b.*\b(from you|your)\b"
    r"|\b(send|share|provide)\b.*\b(site plan|aerial|address|w-?9|scope|photos|documents)\b",
    re.I,
)

# Implicit "you need to provide X" even without polite request words
IMPLICIT_NEED_RX = re.compile(
    r"\b(will likely need|will need|need|needed|required|require)\b.*\b(site plan|aerial|address|w-?9|scope|photos|documents|building location)\b"
    r"|\b(site plan|aerial)\b.*\b(help|needed|required)\b",
    re.I,
)

# "Proposal received" language (often just FYI/deliverable)
PROPOSAL_RECEIPT_RX = re.compile(
    r"\b(this is a proposal|proposal attached|attached proposal|estimate attached|here is (?:the )?proposal)\b",
    re.I,
)


ADDRESS_RX = re.compile(r"\b(what address|please send to|where should|ship to|shipping address|deliver to)\b", re.I)
SIGN_RX = re.compile(r"\b(sign and return|please sign|docusign|signature required)\b", re.I)
TRACK_RX = re.compile(r"\b(tracking number|track (it|this)|fedex|ups|usps)\b", re.I)

# Invoice/PI logic: default REVIEW because it usually implies pay/approve
INVOICE_STRONG_RX = re.compile(r"\b(proforma|invoice|\bpi\b|payment due)\b", re.I)

# Proposal/quote words exist in FYIs; REVIEW only if decision language exists
QUOTE_WORD_RX = re.compile(r"\b(quote|proposal|estimate|po\b|purchase order)\b", re.I)
DECISION_RX = re.compile(
    r"\b(please review|kindly review|please confirm|kindly confirm|approve|approval|ok to proceed|proceed\?|"
    r"which option|select|choose|sign off|let me know if you want to move forward|your approval)\b",
    re.I,
)

def classify(subject: str, ask: str, frm: str):
    text = f"{frm} {subject} {ask}".strip()

    # 1) INFO
    if AUTO_RX.search(text):
        return ("INFO", "No action (auto-reply/FYI).")

    # 2) DONE (execution completed) — guard against future commitments
    if DONE_RX.search(text) and not FUTURE_RX.search(text):
        return ("DONE", "Log as complete; verify if any follow-up needed.")

    # 3) WAITING_ON_YOU — explicit requests for your action/inputs
    if ADDRESS_RX.search(text):
        return ("WAITING_ON_YOU", "Reply with the correct address.")
    if SIGN_RX.search(text):
        return ("WAITING_ON_YOU", "Review, sign, and return the document.")
    if TRACK_RX.search(text):
        return ("WAITING_ON_YOU", "Check tracking status and reply with the update.")
    if REQUEST_INPUT_RX.search(text):
        if re.search(r"\bsite plan|aerial\b", text, re.I):
            return ("WAITING_ON_YOU", "Send site plan/aerial + brief scope so they can proceed.")
        if re.search(r"\bw-?9\b", text, re.I):
            return ("WAITING_ON_YOU", "Send W-9 / confirm W-9 details.")
        return ("WAITING_ON_YOU", "Respond with requested info/action.")
    if IMPLICIT_NEED_RX.search(text):
        return ("WAITING_ON_YOU", "Provide the missing input (site plan/aerial/address/W-9/scope) so they can proceed.")


    # 4) WAITING — someone else committed to do something
    if FUTURE_RX.search(text):
        return ("WAITING", "Monitor until completed; follow up if no update.")

    # 5) REVIEW — invoices/proformas default to REVIEW (decision implied)
    if INVOICE_STRONG_RX.search(text):
        return ("REVIEW", "Review invoice/PI and decide: pay/approve/questions.")

    # 6) REVIEW for quotes/proposals only when decision is requested
    if QUOTE_WORD_RX.search(text) and DECISION_RX.search(text):
        return ("REVIEW", "Review quote/proposal and decide approve/deny/questions.")


    # Proposal received (usually FYI unless decision language is present)
    if PROPOSAL_RECEIPT_RX.search(text):
        return ("INFO", "Proposal received; file it and decide later if/when needed.")
    # 7) Deliverable received (artifact) — not OPEN
    if DELIVER_RX.search(text):
        return ("INFO", "Artifact received; file it and link to the correct project object.")

    # 8) Default
    return ("OPEN", (ask[:240] if ask else "Review email and decide next step."))

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        status, next_step = classify(r.get("subject",""), r.get("ask",""), r.get("from",""))
        r["status"] = status
        r["next_step"] = next_step

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote: {OUT_CSV} ({len(rows)} rows)")
    # quick sanity: show first 15 non-OPEN
    sample = [r for r in rows if r["status"] != "OPEN"][:15]
    for r in sample:
        print(r["email_id"], r["status"], r["subject"])

if __name__ == "__main__":
    main()
