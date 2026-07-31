import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Error: SUPABASE_URL and SUPABASE_KEY must be set in .env")
    exit(1)

supabase = create_client(url, key)

def backfill():
    print("Starting intern_id backfill...")
    
    # Fetch all interns
    res = supabase.table("users").select("id, name, email, intern_id").eq("role", "intern").execute()
    interns = res.data
    
    # Find max existing intern_id
    max_id = 0
    missing_interns = []
    
    for i in interns:
        if i.get("intern_id") and i["intern_id"].startswith("RGTV-INT-"):
            try:
                val = int(i["intern_id"].split("-")[-1])
                if val > max_id:
                    max_id = val
            except ValueError:
                pass
        else:
            missing_interns.append(i)
            
    print(f"Found {len(interns)} total interns. {len(missing_interns)} are missing an intern_id.")
    print(f"Current max intern_id sequence is {max_id}.")
    
    if not missing_interns:
        print("No interns need backfilling. Exiting.")
        return

    # Update missing interns
    for intern in missing_interns:
        max_id += 1
        new_intern_id = f"RGTV-INT-{max_id:04d}"
        
        try:
            print(f"Updating {intern['name']} ({intern['email']}) -> {new_intern_id}")
            supabase.table("users").update({"intern_id": new_intern_id}).eq("id", intern["id"]).execute()
        except Exception as e:
            print(f"Failed to update {intern['name']}: {e}")
            
    print("Backfill completed successfully.")
    print("NOTE: Please make sure to sync these changes to Google Sheets if they don't sync automatically. You can do this by running a sync script or manually copying the updated intern_id column.")

if __name__ == "__main__":
    backfill()
