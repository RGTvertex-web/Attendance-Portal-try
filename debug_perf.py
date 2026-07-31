"""
debug_perf.py - Standalone diagnostic: reads raw Performance sheet and shows all intern_ids.
Run as: python debug_perf.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def main():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        print("ERROR: GOOGLE_CREDENTIALS_JSON not set")
        sys.exit(1)

    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        print("ERROR: SPREADSHEET_ID not set")
        sys.exit(1)

    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    client = gspread.authorize(creds)
    ss = client.open_by_key(spreadsheet_id)

    # ── Raw Performance sheet ──────────────────────────────────────────────────
    try:
        ws = ss.worksheet("Performance")
        all_rows = ws.get_all_values()
        print(f"\n=== Performance Sheet: {len(all_rows)} total rows (including header) ===")
        if all_rows:
            header = all_rows[0]
            print(f"Header ({len(header)} cols): {header}")
            print(f"\nData rows: {len(all_rows)-1}")
            for i, row in enumerate(all_rows[1:], 1):
                # Pad to at least 28 cols
                row = row + [''] * (28 - len(row))
                print(f"\n--- Row {i} ---")
                print(f"  report_id  (col0): {repr(row[0])}")
                print(f"  intern_id  (col1): {repr(row[1])}")
                print(f"  manager_id (col2): {repr(row[2])}")
                print(f"  period     : {row[3]} to {row[4]}")
                print(f"  total_score(col12): {repr(row[12])}")
                print(f"  grade_band (col13): {repr(row[13])}")
                print(f"  submitted_at(col17): {repr(row[17])}")
                print(f"  discipline (col23): {repr(row[23])}")
                print(f"  task_comp  (col24): {repr(row[24])}")
                print(f"  initiative (col25): {repr(row[25])}")
    except Exception as e:
        print(f"ERROR reading Performance sheet: {e}")

    # ── Users sheet ────────────────────────────────────────────────────────────
    try:
        ws_users = ss.worksheet("Users")
        user_rows = ws_users.get_all_values()
        print(f"\n=== Users Sheet: {len(user_rows)-1} users ===")
        user_header = user_rows[0] if user_rows else []
        print(f"Headers: {user_header}")
        id_idx = user_header.index("id") if "id" in user_header else 0
        email_idx = user_header.index("email") if "email" in user_header else 1
        role_idx = user_header.index("role") if "role" in user_header else 4
        intern_id_idx = user_header.index("intern_id") if "intern_id" in user_header else 13
        name_idx = user_header.index("name") if "name" in user_header else 3
        for row in user_rows[1:]:
            row = row + [''] * (max(id_idx, email_idx, role_idx, intern_id_idx, name_idx)+1 - len(row))
            role = row[role_idx] if len(row) > role_idx else ""
            if role == "intern":
                uid = row[id_idx] if len(row) > id_idx else ""
                email = row[email_idx] if len(row) > email_idx else ""
                iid = row[intern_id_idx] if len(row) > intern_id_idx else ""
                name = row[name_idx] if len(row) > name_idx else ""
                print(f"  INTERN: name={repr(name)} uuid={repr(uid)} intern_id={repr(iid)} email={repr(email)}")
    except Exception as e:
        print(f"ERROR reading Users sheet: {e}")

if __name__ == "__main__":
    main()
