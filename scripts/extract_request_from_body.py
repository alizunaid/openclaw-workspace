#!/usr/bin/env python3
import sys, re

REPLY_CUT = re.compile(
    r"(?im)^\s*(?:from:\s|sent:\s|to:\s|subject:\s|-----original message-----|on\s+.*wrote:\s*$|>|confidentiality statement|external email:)"
)

REQ_PHRASES = [
    "please", "can you", "can we", "could you", "would you", "need", "required",
    "action required", "following up", "follow up"
]
VERBS = [
    "send","provide","confirm","approve","sign","review","submit",
    "schedule","pay","invoice","wire","deposit","attach","complete",
    "share","update","forward"
]
ENTITY = ["invoice","w9","ach","wire","deposit","payment","disbursement","tracking","form","contract","signature"]
DEADLINE = ["by ", "before ", "today", "tomorrow", "eod", "as soon as"]
RE_DATE = re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b20\d{2}-\d{2}-\d{2}\b")

RE_NOT_A_REQUEST = [
    re.compile(r"(?i)^\s*you can\b"),
    re.compile(r"(?i)^\s*i\s*(?:am|'?m)\b"),
    re.compile(r"(?i)^\s*i\s*(?:will|'?ll)\b"),
    re.compile(r"(?i)^\s*we\s*(?:will|'?ll)\b"),
    re.compile(r"(?i)^\s*there was\b"),
    re.compile(r"(?i)^\s*there is\b"),
    re.compile(r"(?i)^\s*here you go\b"),
    re.compile(r"(?i)^\s*just letting you know\b"),
    re.compile(r"(?i)^\s*for your information\b"),
    re.compile(r"(?i)^\s*thank(s| you)\b"),         # <-- NEW: drop gratitude-only lines
    re.compile(r"(?i)^\s*confidentiality statement\b"),
    re.compile(r"(?i)received this communication in error"),
    re.compile(r"(?i)^\s*external email:"),
    re.compile(r"(?i)^\s*this message did not originate"),
    re.compile(r"(?i)^\s*do not click links"),
    re.compile(r"(?i)strictly prohibited"),
]

RE_QUESTION = re.compile(r"\?\s*$")
RE_IMPERATIVE_START = re.compile(r"(?i)^\s*(please\s+)?(" + "|".join(map(re.escape, VERBS)) + r")\b")
RE_CAN_YOU_START = re.compile(r"(?i)^\s*(please\s+)?(can|could|would)\s+(you|we)\b")
RE_CAN_YOU_ANY = re.compile(r"(?i)\b(can|could|would)\s+you\b")
RE_CAN_WE_ANY  = re.compile(r"(?i)\bcan\s+we\b")
RE_QSTART = re.compile(r"(?i)^\s*(is|are|do|did|will|can|could|would|may)\b")

def trim_quoted(text: str) -> str:
    out = []
    for line in text.splitlines():
        if REPLY_CUT.search(line):
            break
        out.append(line)
    return "\n".join(out).strip()

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u00a0"," ").replace("\u2019","'")).strip()

def is_filtered_out(s: str) -> bool:
    s2 = s.replace("\u2019", "'")
    for rx in RE_NOT_A_REQUEST:
        if rx.search(s2):
            return True
    return False

def score_sentence(s: str):
    if is_filtered_out(s):
        return 0.0, False

    s2 = s.replace("\u2019", "'")
    sl = s2.lower()
    score = 0.0
    strong_entity = False

    strong_intent = bool(
        RE_CAN_YOU_START.search(s2)
        or RE_CAN_YOU_ANY.search(s2)
        or RE_CAN_WE_ANY.search(s2)
        or RE_IMPERATIVE_START.search(s2)
        or RE_QUESTION.search(s2)
        or (RE_QSTART.search(s2) and (any(e in sl for e in ENTITY) or ("we can" in sl)))
    )

    has_req_phrase = any(p in sl for p in REQ_PHRASES) or ("can we" in sl)

    if not (strong_intent or has_req_phrase):
        return 0.0, False

    # core weights
    if RE_CAN_YOU_START.search(s2): score += 0.75
    elif RE_CAN_YOU_ANY.search(s2): score += 0.70
    if RE_IMPERATIVE_START.search(s2): score += 0.70
    if RE_QUESTION.search(s2): score += 0.25
    if (RE_QSTART.search(s2) and (any(e in sl for e in ENTITY) or ("we can" in sl))): score += 0.35
    if has_req_phrase: score += 0.10
    if RE_CAN_WE_ANY.search(s2): score += 0.55
    if ("follow up" in sl or "following up" in sl) and any(e in sl for e in ENTITY): score += 0.35
    if any(v in sl for v in VERBS): score += 0.10
    if re.search(r"\b(you|your)\b", s2, re.I): score += 0.05
    if any(d in sl for d in DEADLINE): score += 0.08
    if RE_DATE.search(s2): score += 0.06
    if any(e in sl for e in ENTITY):
        score += 0.08
        strong_entity = True

    if score > 1.0:
        score = 1.0

    return float(score), bool(strong_entity)
def extract_best(text: str):
    t = trim_quoted(text)
    if not t:
        return "", 0.0, False

    chunks = re.split(r"(?<=[\.\?\!])\s+|\n+", t)
    # merge small lead-in fragments (e.g., "and ...") into next chunk
    merged = []
    i = 0
    while i < len(chunks):
        c = chunks[i].strip()
        if i + 1 < len(chunks):
            nxt = chunks[i + 1].strip()
            if c and len(c) <= 40 and (c.lower().startswith(("and ", "but ", "so ", "also ", "re: ", "fwd: "))):
                chunks[i + 1] = c + " " + nxt
                i += 1
                continue
        merged.append(c)
        i += 1
    chunks = merged
    best_text, best_score, best_strong = ("", 0.0, False)

    for c in chunks:
        c = c.strip()
        if len(c) < 8:
            continue
        sc, strong_ent = score_sentence(c)
        if sc > best_score:
            best_text, best_score, best_strong = c, sc, strong_ent

    return norm(best_text), best_score, best_strong

def main():
    if len(sys.argv) != 2:
        print("usage: extract_request_from_body.py /path/to/body.txt", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    req, conf, strong_ent = extract_best(raw)
    print(f"request={req}")
    print(f"confidence={conf:.3f}")
    print(f"strong_entity={1 if strong_ent else 0}")

if __name__ == "__main__":
    main()
