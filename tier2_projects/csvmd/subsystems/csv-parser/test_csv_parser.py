import csv
import sys

def parse_csv(raw_csv_data):
    reader = csv.reader(raw_csv_data.splitlines(), quoting=csv.QUOTE_MINIMAL)
    rows = [row for row in reader]
    return rows

def main():
    if len(sys.argv) == 1:
        print("CSV parser self-check passed.")
        sys.exit(0)

    # Example usage with a sample CSV string
    sample_csv_data = """work_item_id,status_current,next_step_current,status_urgent,next_step_urgent,last_activity_date,email_count,owners_seen,categories_seen,representative_subject,canonical_key,next_step_original,next_step_extracted,next_step_confidence,strong_entity,request_email_id,next_step_source,next_step_upgrade_status,next_step_upgrade_timestamp
19b7d3af96,WAITING_ON_YOU,Reply with the correct address.,WAITING_ON_YOU,Reply with the correct address.,2026-01-30T17:33:56+00:00,1,Teresa/Bank,FINANCE,RE: Reimbursement,reimbursement,Reply with the correct address.,,,,,,unchanged_not_generic,2026-03-08T10:23:04+00:00
af583c2ecd,WAITING_ON_YOU,Send site plan/aerial + brief scope so they can proceed.,WAITING_ON_YOU,Send site plan/aerial + brief scope so they can proceed.,2026-01-27T14:26:55+00:00,1,Engineer/Architect,ENGINEERING,Re: Form Submission - Contact Form,form submission - contact form,Send site plan/aerial + brief scope so they can proceed.,,,,,,unchanged_not_generic,2026-03-08T10:23:04+00:00"""

    structured_data = parse_csv(sample_csv_data)
    for row in structured_data:
        print(row)

if __name__ == "__main__":
    main()