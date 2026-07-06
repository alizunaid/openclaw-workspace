# Mailbox ingest report — Brick A

_Deterministic ingest of the Gmail Takeout export. Counts and addresses/domains only — no message bodies._

## Totals

- Messages parsed across all mboxes (with duplication): **11210**
- Unique in-window messages kept (deduped): **2429**
- Apparent duplication / out-of-window rate: **78.3%**
- Excluded by date (< 2025-01-01): **0**
- Excluded as undated (no parseable Date): **0**
- Malformed / undecodable messages skipped: **0**

## Per-mbox parsed counts

| mbox (label) | parsed | malformed |
|---|---:|---:|
| Archived | 1817 | 0 |
| Important | 1742 | 0 |
| Opened | 1720 | 0 |
| Category Personal | 1538 | 0 |
| Sent | 1514 | 0 |
| Inbox | 1121 | 0 |
| Unread | 728 | 0 |
| Category Promotions | 424 | 0 |
| Category Updates | 353 | 0 |
| Starred | 59 | 0 |
| Leasing | 55 | 0 |
| 1360 documents | 52 | 0 |
| Category Bills | 47 | 0 |
| IMAP_$Forwarded | 18 | 0 |
| ESA | 8 | 0 |
| Category Purchases | 7 | 0 |
| Template Response | 5 | 0 |
| Category Travel | 1 | 0 |
| Sourcing agent | 1 | 0 |

## Threads

- Total threads: **630**
- Singletons: **380**
- 2–5 messages: **162**
- 6–20 messages: **73**
- 20+ messages: **15**

## Label signals

Operator-made labels (project-relevance hints):

| operator label | messages |
|---|---:|
| 1360 documents | 52 |
| Leasing | 55 |
| ESA | 8 |
| Sourcing agent | 1 |

Category-* labels (noise hints):

| category label | messages |
|---|---:|
| Category Personal | 1537 |
| Category Promotions | 424 |
| Category Travel | 1 |
| Category Updates | 353 |

## Top 40 contacts

| # | email | names | msgs | recv_from | sent_to | first_seen | last_seen | noise |
|---:|---|---|---:|---:|---:|---|---|:--:|
| 1 | admin@nexadose.com | NexaDose RX | 2428 | 788 | 1643 | 2025-05-20 | 2026-07-04 |  |
| 2 | imranrali88@gmail.com | imran ali | 357 | 24 | 333 | 2025-05-21 | 2026-06-29 |  |
| 3 | emails@search.crexi.com | Crexi | 328 | 328 | 0 | 2025-06-17 | 2026-07-04 |  |
| 4 | sheri@ssbdesigns.com | Sheri Bumgardner | 269 | 113 | 156 | 2025-11-06 | 2026-06-29 |  |
| 5 | afconstructionandpainting@gmail.com | Adrian Aponte | 185 | 32 | 153 | 2025-12-19 | 2026-07-02 |  |
| 6 | florian.chauvin@js-sourcing.com | Florian JS; Florian02CHAUVIN | 165 | 70 | 96 | 2025-05-28 | 2026-05-29 |  |
| 7 | tcraig@ffb1.com | Teresa Craig | 86 | 42 | 44 | 2026-01-12 | 2026-04-10 |  |
| 8 | alizunaid@hotmail.com | Zunaid Ali | 85 | 5 | 80 | 2025-06-13 | 2026-06-05 |  |
| 9 | bglaze@ffb1.com | Bobby Glaze | 85 | 30 | 55 | 2025-05-21 | 2026-06-02 |  |
| 10 | asa.atkinson@carr.us | Asa Atkinson | 82 | 36 | 46 | 2025-06-20 | 2026-06-24 |  |
| 11 | john@strategiclegalservice.com | John Chay | 73 | 31 | 42 | 2025-09-02 | 2025-12-29 |  |
| 12 | ops@txintlfreight.com | Ops | 62 | 24 | 38 | 2026-04-06 | 2026-06-08 |  |
| 13 | hector@ddsg.us | Hector Reyes | 61 | 12 | 49 | 2026-02-26 | 2026-06-29 |  |
| 14 | emails@notifications.crexi.com | Crexi | 60 | 59 | 1 | 2025-06-11 | 2025-08-20 |  |
| 15 | michael@txintlfreight.com | Michael Dyll | 53 | 5 | 48 | 2026-04-04 | 2026-06-09 |  |
| 16 | amanda@ssbdesigns.com | Amanda Moore | 52 | 17 | 35 | 2025-11-12 | 2026-05-14 |  |
| 17 | kate@title.law | Kate Tucker | 52 | 27 | 27 | 2025-09-08 | 2025-12-29 |  |
| 18 | eden@dersionclean.com | Eden Pan; eden@dersionclean.com | 48 | 23 | 25 | 2025-10-14 | 2026-05-29 |  |
| 19 | jonathan.saldana@ddsg.us | Jonathan Saldana | 46 | 4 | 42 | 2026-03-03 | 2026-06-29 |  |
| 20 | service01@js-sourcing.com |  | 45 | 0 | 45 | 2025-10-11 | 2026-05-29 |  |
| 21 | mklemenz@ranfpe.com | Michael Klemenz, PE | 40 | 17 | 23 | 2026-03-25 | 2026-06-04 |  |
| 22 | aponte.adrian@gmail.com | Adrian Aponte | 39 | 2 | 37 | 2026-01-13 | 2026-05-28 |  |
| 23 | shogan@ffb1.com | Shelly Hogan | 31 | 14 | 17 | 2026-04-16 | 2026-06-26 |  |
| 24 | castroderrick@outlook.com | Derrick Castro | 27 | 22 | 5 | 2025-10-13 | 2025-11-03 |  |
| 25 | cody@bannistereng.com | Cody Brooks | 27 | 11 | 16 | 2025-12-02 | 2026-02-12 |  |
| 26 | gracesyn@title.law |  | 27 | 0 | 27 | 2025-09-08 | 2025-12-22 |  |
| 27 | hanner@texasins.net | Hanner Shipley | 23 | 13 | 10 | 2025-12-15 | 2025-12-18 |  |
| 28 | isaac.alva@txdot.gov | Isaac Alva | 23 | 11 | 12 | 2025-08-07 | 2026-01-09 |  |
| 29 | workspace-noreply@google.com | The Google Workspace Team | 22 | 22 | 0 | 2025-05-20 | 2026-06-29 |  |
| 30 | arty.wheaton-rodriguez@mansfieldtexas.gov | Arty Wheaton-Rodriguez | 21 | 5 | 16 | 2025-08-07 | 2026-05-26 |  |
| 31 | cyle@ccrookconsulting.com | Cyle Cox | 20 | 9 | 11 | 2025-12-02 | 2026-02-10 |  |
| 32 | michael.roberts@mansfieldtexas.gov | Michael Roberts | 19 | 5 | 14 | 2025-08-22 | 2026-06-04 |  |
| 33 | ld@cpgsourcing.com | Laura Dow | 18 | 13 | 5 | 2025-05-28 | 2025-06-24 |  |
| 34 | ljcleaver@4wayinvestments.com | LJ Cleaver | 18 | 11 | 7 | 2025-05-21 | 2025-06-09 |  |
| 35 | scott.lingo@mansfieldtexas.gov | Scott Lingo | 18 | 4 | 14 | 2025-12-04 | 2026-06-04 |  |
| 36 | mollie.carroll@mansfieldtexas.gov | Mollie Carroll | 17 | 3 | 14 | 2026-04-02 | 2026-05-28 |  |
| 37 | jenny.cai@js-sourcing.com | Jenny-JS | 16 | 4 | 12 | 2025-05-29 | 2026-04-01 |  |
| 38 | marketing@germfree.com | Germfree Laboratories | 16 | 16 | 0 | 2025-05-27 | 2026-06-30 |  |
| 39 | sfranklin@dfwcad.com | Seth Franklin | 16 | 11 | 5 | 2025-09-24 | 2026-02-28 |  |
| 40 | quickbooks@notification.intuit.com | D&M Enterprize; GAP Consultants, Inc. | 15 | 15 | 0 | 2025-11-17 | 2026-06-24 |  |

## Top 20 domains (non-noise contacts, by message volume)

| # | domain | messages |
|---:|---|---:|
| 1 | nexadose.com | 2428 |
| 2 | gmail.com | 688 |
| 3 | search.crexi.com | 328 |
| 4 | ssbdesigns.com | 321 |
| 5 | js-sourcing.com | 226 |
| 6 | ffb1.com | 211 |
| 7 | mansfieldtexas.gov | 132 |
| 8 | ddsg.us | 123 |
| 9 | txintlfreight.com | 122 |
| 10 | hotmail.com | 85 |
| 11 | carr.us | 83 |
| 12 | title.law | 79 |
| 13 | strategiclegalservice.com | 73 |
| 14 | notifications.crexi.com | 60 |
| 15 | bannistereng.com | 49 |
| 16 | dersionclean.com | 48 |
| 17 | google.com | 44 |
| 18 | txdot.gov | 42 |
| 19 | waxlerfpe.com | 42 |
| 20 | ranfpe.com | 41 |

_Contacts modeled: 369 unique addresses._
