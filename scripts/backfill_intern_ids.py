import os
import sys

# Add the parent directory to sys.path to allow imports from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from services import supabase_service as supa
from services import sheets_service as ss

def backfill_interns():
    with app.app_context():
        supabase = supa.get_supabase_client()
        
        print("Fetching all users...")
        res = supabase.table("users").select("*").order("created_at").execute()
        users = res.data
        
        interns_to_update = [u for u in users if u.get("role") == "intern" and not u.get("intern_id")]
        
        if not interns_to_update:
            print("No interns need backfilling. All set!")
            return
            
        print(f"Found {len(interns_to_update)} interns to backfill.")
        
        for user in interns_to_update:
            user_id = user["id"]
            email = user["email"]
            print(f"Processing intern: {email} ({user_id})")
            
            try:
                rpc_res = supabase.rpc("next_intern_id", {}).execute()
                seq_val = rpc_res.data
                if seq_val:
                    new_intern_id = f"RGTV-INT-{int(seq_val):04d}"
                    print(f"  -> Generated ID: {new_intern_id}")
                    
                    # Update Supabase
                    supabase.table("users").update({"intern_id": new_intern_id}).eq("id", user_id).execute()
                    
                    # Update Sheets
                    ss.update_user_in_sheet(user_id, intern_id=new_intern_id)
                    print("  -> Successfully updated.")
                else:
                    print(f"  -> Failed to generate sequence for {email}")
            except Exception as e:
                print(f"  -> Error processing {email}: {e}")

if __name__ == "__main__":
    backfill_interns()
