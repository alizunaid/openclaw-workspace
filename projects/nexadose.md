# Project: Nexadose RX

## What this is
Pharmacy facility build at 1360 S. Main St., Mansfield, TX.
Second active entity: SRM United LLC.

## People
- Zunaid (owner)
- Bobby (business partner)
- Imran (property/entity matters)

## Key vendors
- SSB Designs: Amanda Moore, Sheri Bumgardner, George Patterson
- Michael Klemenz PE (engineer)
- Katie Murillo at Texas International Freight (customs)
- Eden Pan at Dersion Environmental (cleanroom equipment)
- Drew Thacker at ARIEL Inspections
- John P. Chay PLLC (legal)
- Raiza Langsford (legal/entity)
- Cody Brooks at Bannister Engineering

## Work item categories
PERMITS_INSPECTIONS, ENGINEERING, FINANCE, VENDORS_EQUIPMENT, LEGAL, CLEANROOM_USP, WAITING_ON_YOU

## Email accounts
- admin@nexadose.com (primary, Gmail)
- alizunaid2025@gmail.com (prior)
- alizunaid@hotmail.com (Outlook, not directly accessible)

## Key files
- WORK_ITEMS_REGISTER.upgraded.v4_1.csv (master register)
- TODAY_TOP10.md (daily priorities)
- logs/daily_briefing.txt

## Key file schemas

### WORK_ITEMS_REGISTER.upgraded.v4_1.csv
Columns: work_item_id, status_current, next_step_current, status_urgent, next_step_urgent, last_activity_date, email_count, owners_seen, categories_seen, representative_subject, canonical_key, next_step_original, next_step_extracted, next_step_confidence, strong_entity, request_email_id, next_step_source, next_step_upgrade_status, next_step_upgrade_timestamp

Status values in status_current: WAITING_ON_YOU, OPEN, WAITING
Category values in categories_seen (semicolon-separated when multiple): PERMITS_INSPECTIONS, ENGINEERING, FINANCE, VENDORS_EQUIPMENT, LEGAL, CLEANROOM_USP, UNCLASSIFIED

## File paths
All work items: /root/.openclaw/workspace/WORK_ITEMS_REGISTER.upgraded.v4_1.csv
Daily top 10: /root/.openclaw/workspace/TODAY_TOP10.md
Daily briefing log: /root/.openclaw/workspace/logs/daily_briefing.txt
Generated scripts go in: /root/.openclaw/workspace/tools/generated/
