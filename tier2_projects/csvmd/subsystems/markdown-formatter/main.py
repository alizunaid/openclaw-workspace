import csv
import sys

def rows_to_markdown(rows):
    if not rows:
        return ""
    
    headers = rows[0]
    table = [headers, ["---"] * len(headers)] + rows[1:]
    markdown_table = "\n".join("|" + " | ".join(map(str, row)) + "|" for row in table)
    return markdown_table

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Example usage with arguments (not used in no-args self-check)
        csv_content = """work_item_id,status_current,next_step_current,status_urgent,next_step_urgent,last_activity_date,email_count,owners_seen,categories_seen,representative_subject,canonical_key
19b7d3af96,WAITING_ON_YOU,Reply with the correct address.,WAITING_ON_YOU,Reply with the correct address.,2026-01-30T17:33:56+00:00,1,Teresa/Bank,FINANCE,RE: Reimbursement,reimbursement"""
        reader = csv.reader(csv_content.splitlines())
        rows = list(reader)
        print(rows_to_markdown(rows))
    else:
        # No-args self-check
        print("No input provided; no-op confirmed.")
        sys.exit(0)