import sys
import os

# Add current directory to path so we can import app and services
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    from app import app
    from services import supabase_service as supa
    from services import sheets_service as ss
except ImportError as e:
    print(f"Error importing app or services: {e}")
    sys.exit(1)

def sync_all():
    with app.app_context():
        print("Starting two-way synchronization between Supabase and Google Sheets...")
        
        # 1. Fetch all profiles from Supabase
        try:
            all_profiles = supa.get_all_profiles()
        except Exception as e:
            print(f"Failed to fetch profiles from Supabase: {e}")
            return

        print(f"Loaded {len(all_profiles)} users from Supabase.")

        # 2. Fetch Users worksheet from Google Sheets
        print("Fetching Users worksheet from Google Sheets...")
        try:
            sheet = ss._get_sheet("Users")
            rows = sheet.get_all_values()
        except Exception as e:
            print(f"Failed to fetch Users tab from Google Sheets: {e}")
            return

        if not rows or len(rows) < 2:
            print("Users worksheet is empty or only has headers.")
            return

        headers = rows[0]
        id_idx = headers.index("id") if "id" in headers else 0
        jdate_idx = headers.index("joining_date") if "joining_date" in headers else 10
        int_id_idx = headers.index("intern_id") if "intern_id" in headers else 13

        # Map Sheet rows by user ID
        sheet_users = {}
        for r in rows[1:]:
            if len(r) > id_idx:
                uid = r[id_idx].strip()
                if uid:
                    sheet_users[uid] = {
                        "joining_date": r[jdate_idx].strip() if len(r) > jdate_idx else "",
                        "intern_id": r[int_id_idx].strip() if len(r) > int_id_idx else "",
                        "row_data": r
                    }

        print(f"Loaded {len(sheet_users)} users from Google Sheets.")

        supa_updated = 0
        sheet_updated = 0
        sheet_added = 0

        # 3. Reconcile differences
        for user in all_profiles:
            uid = user.get("id")
            name = user.get("name", "Unknown")
            role = user.get("role")
            
            supa_jdate = user.get("joining_date")
            supa_int_id = user.get("intern_id")

            if uid in sheet_users:
                sh_data = sheet_users[uid]
                sh_jdate = sh_data["joining_date"]
                sh_int_id = sh_data["intern_id"]

                # A: If Supabase is missing joining_date or intern_id, but Sheet has it -> update Supabase
                supa_updates = {}
                if not supa_jdate and sh_jdate:
                    supa_updates["joining_date"] = sh_jdate
                if not supa_int_id and sh_int_id and role == "intern":
                    supa_updates["intern_id"] = sh_int_id

                if supa_updates:
                    print(f"[Supabase Sync] Updating {name} ({uid}) in Supabase: {supa_updates}")
                    try:
                        supa.update_profile(uid, **supa_updates)
                        supa_updated += 1
                        # Update local dict so we don't overwrite Sheet below
                        if "joining_date" in supa_updates: supa_jdate = supa_updates["joining_date"]
                        if "intern_id" in supa_updates: supa_int_id = supa_updates["intern_id"]
                    except Exception as e:
                        print(f"  -> Error updating Supabase for {name}: {e}")

                # B: If Sheet is missing joining_date or intern_id, but Supabase has it -> update Sheet
                sheet_updates = {}
                if not sh_jdate and supa_jdate:
                    sheet_updates["joining_date"] = supa_jdate
                if not sh_int_id and supa_int_id and role == "intern":
                    sheet_updates["intern_id"] = supa_int_id

                if sheet_updates:
                    print(f"[Sheet Sync] Updating {name} ({uid}) in Google Sheets: {sheet_updates}")
                    try:
                        ss.update_user_in_sheet(uid, **sheet_updates)
                        sheet_updated += 1
                    except Exception as e:
                        print(f"  -> Error updating Sheet for {name}: {e}")
            else:
                # C: User is in Supabase but completely missing from Google Sheets -> add to Sheet
                print(f"[Sheet Add] Adding missing user {name} ({uid}) to Google Sheets...")
                try:
                    ss.add_user_to_sheet(user)
                    sheet_added += 1
                except Exception as e:
                    print(f"  -> Error adding user {name} to Sheet: {e}")

        print("\n--- Synchronization Summary ---")
        print(f"Total users checked: {len(all_profiles)}")
        print(f"Updated in Supabase from Sheets: {supa_updated}")
        print(f"Updated in Sheets from Supabase: {sheet_updated}")
        print(f"Added new users to Sheets: {sheet_added}")
        print("Sync complete!")

if __name__ == "__main__":
    sync_all()
