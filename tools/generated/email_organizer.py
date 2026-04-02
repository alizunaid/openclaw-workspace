#!/usr/bin/env python3
import os
import json
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path

INPUT_DIR = 'email_raw/'
OUTPUT_DIR = 'organized/'
LOG_FILE = 'logs/email_organizer_decisions.json'
CATEGORIES = {
    'PERMITS_INSPECTIONS': [],
    'ENGINEERING': [],
    'FINANCE': [],
    'VENDORS_EQUIPMENT': [],
    'LEGAL': [],
    'UNCLASSIFIED': []
}

def categorize_subject(subject):
    if subject is None:
        return 'UNCLASSIFIED'
    if re.search(r'permit|inspection|fire|city|marshal|zoning|pre-?application|development review|drc|site walk|parking|environmental', subject, re.I):
        return 'PERMITS_INSPECTIONS'
    elif re.search(r'engineer|plan|design|survey|sewer|septic|cleanroom|clean.?room|usp|renovation|layout|arch|architectural|scope of work|electrical|civil|mock.?up|797|water line', subject, re.I):
        return 'ENGINEERING'
    elif re.search(r'insurance|bank|finance|invoice|payment|w-9|w9|loan|sba|deposit|disbursement|transfer|builder.?s risk|reimbursement|appraisal|fee|bill', subject, re.I):
        return 'FINANCE'
    elif re.search(r'vendor|equipment|sourcing|quote|proposal|supplier|supplies|consumables|lab|shipment|delivered', subject, re.I):
        return 'VENDORS_EQUIPMENT'
    elif re.search(r'attorney|legal|contract|loi|title|agreement|sign|docusign|amendment|closing|escrow|lease|landlord|waiver|plat|property|for sale|for lease|subordination', subject, re.I):
        return 'LEGAL'
    else:
        return 'UNCLASSIFIED'


def categorize_filename(filename):
    if filename is None:
        return 'UNCLASSIFIED'
    if re.search(r'permit|inspection|fire|city|marshal|zoning|environmental', filename, re.I):
        return 'PERMITS_INSPECTIONS'
    elif re.search(r'engineer|plan|design|survey|sewer|septic|cleanroom|clean.?room|usp|renovation|arch|civil|electrical|scope|drawing|spec', filename, re.I):
        return 'ENGINEERING'
    elif re.search(r'insurance|bank|finance|invoice|payment|w-?9|loan|sba|deposit|statement|appraisal|reimbursement', filename, re.I):
        return 'FINANCE'
    elif re.search(r'vendor|equipment|sourcing|quote|proposal|supplier|supplies|consumables', filename, re.I):
        return 'VENDORS_EQUIPMENT'
    elif re.search(r'attorney|legal|contract|loi|title|agreement|lease|landlord|waiver|plat|amendment|closing|escrow|deed', filename, re.I):
        return 'LEGAL'
    else:
        return 'UNCLASSIFIED'

def process_email(file_path):
    with open(file_path, 'rb') as f:
        email = BytesParser(policy=policy.default).parse(f)
        
        subject = email['subject']
        from_ = email['from']
        date_ = email['date']
        attachments = email.iter_attachments()
        
        body = email.get_body(preferencelist=('plain'))
        body_content = body.get_content() if body is not None else ''
        needs_response_flag = 'need a response' in (subject or '').lower() or 'reply' in body_content.lower()

        decision_log = {
            'subject': subject,
            'from': from_,
            'date': date_,
            'attachments': []
        }

        added_filenames = set()

        # Determine category from subject first
        subject_category = categorize_subject(subject)

        for attachment in attachments:
            filename = attachment.get_filename()
            if filename and filename not in added_filenames:
                added_filenames.add(filename)
                # Fall back to filename keywords if subject is unclassified
                category = subject_category if subject_category != 'UNCLASSIFIED' else categorize_filename(filename)
                decision_log['attachments'].append({'filename': filename, 'category': category})
                if category != 'UNCLASSIFIED':
                    target_directory = os.path.join(OUTPUT_DIR, category)
                    os.makedirs(target_directory, exist_ok=True)
                    
                    # Sanitize filename to remove problematic characters
                    sanitized_filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
                    
                    destination = os.path.join(target_directory, sanitized_filename)
                    with open(destination, 'wb') as dest_f:
                        dest_f.write(attachment.get_payload(decode=True))
        
        with open(LOG_FILE, 'a') as log_file:
            json.dump(decision_log, log_file)
            log_file.write('\n')
        
        return needs_response_flag

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    needs_response_emails = []

    for file_path in Path(INPUT_DIR).rglob('*.eml'):
        if file_path.is_file():
            if process_email(file_path):
                needs_response_emails.append(file_path.name)

    for email in needs_response_emails:
        print(f'Email {email} needs a response.')

if __name__ == "__main__":
    raise SystemExit(main())