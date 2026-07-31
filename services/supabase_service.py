"""
supabase_service.py - Handles user authentication and table operations directly via Supabase.
Uses a standalone 'users' table and manual password hashing.
"""
from supabase import create_client, Client
from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash
from services.cache_service import global_cache
import uuid
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
from services.sheets_service import add_user_to_sheet

def get_supabase_client() -> Client:
    url = current_app.config.get("SUPABASE_URL")
    key = current_app.config.get("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Supabase credentials not configured.")
    return create_client(url, key)

# ── Auth Methods ──────────────────────────────────────────────────────────────

def sign_up(email, password, name, role, department=None, manager_id=None, internship_duration_months=None, joining_date=None, phone=None, college_name=None):
    """Sign up using manual hash and insert into users table."""
    try:
        supabase = get_supabase_client()
        
        # Check if email exists
        existing = supabase.table("users").select("id").eq("email", email).execute()
        if existing.data:
            raise ValueError("Email already registered.")
        
        # Hash the password
        password_hash = generate_password_hash(password)
        
        # Compute leave_allotted_days if intern
        leave_allotted_days = None
        if role == "intern" and internship_duration_months:
            try:
                dur = int(internship_duration_months)
                leave_allotted_days = round(dur * 3.33)
            except ValueError:
                pass

        # Create Profile
        user_data = {
            "email": email,
            "password_hash": password_hash,
            "name": name,
            "role": role,
            "department": department if department else None,
            "status": "active"
        }
        
        if department:
            user_data["department"] = department
        if manager_id:
            user_data["manager_id"] = manager_id
        if phone:
            user_data["phone"] = phone
        if college_name:
            user_data["college_name"] = college_name
        if internship_duration_months:
            user_data["internship_duration_months"] = int(internship_duration_months)
        if leave_allotted_days is not None:
            user_data["leave_allotted_days"] = leave_allotted_days
        if joining_date:
            user_data["joining_date"] = joining_date
            
        if role == "intern":
            try:
                # Dynamically generate intern_id by querying existing ones
                all_interns = supabase.table("users").select("intern_id").eq("role", "intern").execute().data
                max_id = 0
                for i in all_interns:
                    iid = i.get("intern_id")
                    if iid and iid.startswith("RGTV-INT-"):
                        try:
                            num = int(iid.split("-")[-1])
                            if num > max_id:
                                max_id = num
                        except ValueError:
                            pass
                user_data["intern_id"] = f"RGTV-INT-{max_id + 1:04d}"
            except Exception as e:
                logger.error("Failed to generate intern_id: %s", e)
            
        session_token = uuid.uuid4().hex
        user_data["session_token"] = session_token
        
        try:
            res = supabase.table("users").insert(user_data).execute()
        except Exception as e:
            err_str = str(e)
            if "session_token" in err_str or "college_name" in err_str:
                user_data.pop("session_token", None)
                user_data.pop("college_name", None)
                res = supabase.table("users").insert(user_data).execute()
            else:
                raise
        
        # Return structured data like before
        created_user = res.data[0] if res.data else user_data
        
        # Sync to Google Sheets with plain text password for visibility (per user request)
        sheet_user_data = created_user.copy()
        sheet_user_data["password"] = password
        add_user_to_sheet(sheet_user_data)
        
        global_cache.invalidate("Profiles")
        
        return {
            "user": created_user,
            "session": {"user_id": created_user["id"]},
            "profile": created_user
        }
    except Exception as e:
        raise ValueError(f"User creation failed: {str(e)}")

def sign_in(email, password):
    """Sign in using manual password verification. Email param can be an email or intern_id."""
    try:
        supabase = get_supabase_client()
        
        # Check if email is an intern_id (contains RGTV) or an actual email
        if "@" in email:
            res = supabase.table("users").select("*").eq("email", email).execute()
        else:
            # Maybe it's an intern_id
            res = supabase.table("users").select("*").eq("intern_id", email).execute()
            # Fallback if they somehow have no @ in email
            if not res.data:
                res = supabase.table("users").select("*").eq("email", email).execute()
                
        if not res.data:
            raise ValueError("Invalid credentials.")
            
        user = res.data[0]
        
        if not check_password_hash(user["password_hash"], password):
            raise ValueError("Invalid credentials.")
            
        # Check status and deactivation_reason
        if user.get("status") == "inactive":
            reason = user.get("deactivation_reason")
            if reason == "terminated":
                raise ValueError("Your account has been deactivated due to termination. Please contact your administrator for details.")
            elif reason == "completed":
                raise ValueError("Your internship has concluded. Access to the portal has been closed. Thank you for your contribution.")
            else:
                raise ValueError("Your account has been deactivated. Please contact your administrator for details.")
            
        session_token = uuid.uuid4().hex
        try:
            supabase.table("users").update({"session_token": session_token}).eq("id", user["id"]).execute()
            user["session_token"] = session_token
        except Exception as e:
            pass # Gracefully handle missing column
            
        return {
            "user": user,
            "session": {"user_id": user["id"]},
            "profile": user
        }
    except Exception as e:
        raise ValueError(f"Invalid credentials. {str(e)}")

def sign_out():
    # Manual auth doesn't require backend sign out for Supabase
    pass

def update_profile_and_password(user_id: str, name: str = None, new_password: str = None) -> dict:
    """Updates the user's name and/or password in the manual users table."""
    updates = {}
    
    if name:
        updates["name"] = name
        
    if new_password:
        updates["password_hash"] = generate_password_hash(new_password)
        
    if not updates:
        return None
def get_user_by_id(user_id):
    """Fetch profile data by user ID."""
    if not user_id:
        return None
    return get_profile(user_id)


# ── Profile CRUD Methods ──────────────────────────────────────────────────────

def get_profile(user_id):
    """Fetch a specific user."""
    if not user_id:
        return None
    try:
        supabase = get_supabase_client()
        res = supabase.table("users").select("*").eq("id", user_id).execute()
        if res.data:
            return res.data[0]
        return None
    except Exception as e:
        current_app.logger.error("Supabase get_profile failed for %s: %s", user_id, e)
        return None

def get_all_profiles():
    """Fetch all users."""
    cached = global_cache.get("Profiles")
    if cached is not None:
        return cached

    try:
        supabase = get_supabase_client()
        res = supabase.table("users").select("*").execute()
        global_cache.set("Profiles", res.data)
        return res.data
    except Exception as e:
        current_app.logger.error("Supabase get_all_profiles failed: %s", e)
        return []

def get_profiles_by_manager(manager_id):
    """Fetch interns in the manager's department or explicitly assigned to this manager."""
    manager = get_profile(manager_id)
    if not manager:
        return []
    
    all_profiles = get_all_profiles()
    manager_dept = str(manager.get("department", "")).strip().lower()
    
    result = []
    for p in all_profiles:
        if p.get("role") != "intern":
            continue
        intern_dept = str(p.get("department", "")).strip().lower()
        if (manager_dept and intern_dept == manager_dept) or (str(p.get("manager_id", "")).strip().lower() == str(manager_id).strip().lower()):
            if p not in result:
                result.append(p)
            
    return result

def get_managers_by_department(department):
    """Fetch all users with the 'manager' role in a specific department."""
    if not department:
        return []
    return [p for p in get_all_profiles() if p.get("role") == "manager" and p.get("department") == department]

def get_least_loaded_manager(department):
    """
    Finds the manager in the department with the fewest interns assigned.
    Returns the manager_id or None if no managers exist.
    """
    dept_managers = get_managers_by_department(department)
    if not dept_managers:
        return None
        
    all_profiles = get_all_profiles()
    interns = [p for p in all_profiles if p.get("role") == "intern" and p.get("department") == department]
    
    counts = {m["id"]: 0 for m in dept_managers}
    for intern in interns:
        i_mid = intern.get("manager_id")
        if i_mid in counts:
            counts[i_mid] += 1
            
    best_manager = min(dept_managers, key=lambda m: (counts[m["id"]], m["id"]))
    return best_manager["id"]

def get_all_managers():
    """Fetch all users with the 'manager' or 'admin' role."""
    return [p for p in get_all_profiles() if p.get("role") in ("manager", "admin")]

def update_profile(user_id, **fields):
    """Update specific fields for a user."""
    try:
        supabase = get_supabase_client()
        res = supabase.table("users").update(fields).eq("id", user_id).execute()
        
        # Keep Sheets in sync
        try:
            from services.sheets_service import update_user_in_sheet
            update_user_in_sheet(user_id, **fields)
        except Exception as e:
            logger.error(f"Failed to sync profile update to Sheets for {user_id}: {e}")
            
        global_cache.invalidate("Profiles")
        return res.data
    except Exception as e:
        raise ValueError(f"Failed to update user: {str(e)}")

def update_profile_and_password(user_id, name=None, new_password=None):
    """Update name and/or password, and regenerate session token if password changes."""
    updates = {}
    if name:
        updates["name"] = name
    
    if new_password:
        updates["password_hash"] = generate_password_hash(new_password)
        session_token = uuid.uuid4().hex
        updates["session_token"] = session_token
        
    if not updates:
        return None
        
    try:
        supabase = get_supabase_client()
        res = supabase.table("users").update(updates).eq("id", user_id).execute()
        
        # Update Google Sheets
        if res.data:
            from services.sheets_service import update_user_in_sheet
            try:
                sheet_updates = {}
                if name: sheet_updates['name'] = name
                if new_password: sheet_updates['password'] = "OMITTED"
                if sheet_updates:
                    update_user_in_sheet(user_id, **sheet_updates)
            except Exception as e:
                current_app.logger.error("Failed to sync profile update to Sheets: %s", e)
                
        global_cache.invalidate("Profiles")
        return res.data[0] if res.data else None
    except Exception as e:
        if "session_token" in str(e) and new_password:
            # Graceful fallback for missing session_token column
            del updates["session_token"]
            res = supabase.table("users").update(updates).eq("id", user_id).execute()
            global_cache.invalidate("Profiles")
            return res.data[0] if res.data else None
        raise ValueError(f"Failed to update profile: {str(e)}")

def delete_user(user_id):
    """Completely delete a user from the database and cascade their records."""
    try:
        supabase = get_supabase_client()
        # 1. Delete intern-related records
        for table in ["attendance", "submissions", "tasks", "warnings", "leaves", "reports", "performance"]:
            try:
                if table == "tasks":
                    supabase.table(table).delete().eq("assigned_to", user_id).execute()
                else:
                    supabase.table(table).delete().eq("intern_id", user_id).execute()
            except Exception:
                pass
                
        # 2. Delete manager-related records
        for table in ["tasks", "warnings", "leaves", "reports", "performance"]:
            try:
                if table == "tasks":
                    supabase.table(table).delete().eq("assigned_by", user_id).execute()
                elif table == "warnings":
                    supabase.table(table).delete().eq("issued_by", user_id).execute()
                elif table in ["leaves", "reports", "performance"]:
                    supabase.table(table).delete().eq("manager_id", user_id).execute()
            except Exception:
                pass

        # 3. Finally delete the user
        res = supabase.table("users").delete().eq("id", user_id).execute()
        
        # Keep Sheets in sync
        try:
            from services.sheets_service import delete_user_from_sheet
            delete_user_from_sheet(user_id)
        except Exception as e:
            current_app.logger.error("Failed to sync user deletion to Sheets: %s", e)
            
        global_cache.invalidate("Profiles")
        return True
    except Exception as e:
        raise ValueError(f"Failed to delete user: {str(e)}")

# ── Password Reset Methods ───────────────────────────────────────────────────

def get_user_by_email(email):
    """Fetch user by email."""
    try:
        supabase = get_supabase_client()
        res = supabase.table("users").select("*").eq("email", email).execute()
        if res.data:
            return res.data[0]
        return None
    except Exception as e:
        current_app.logger.error("Supabase get_user_by_email failed for %s: %s", email, e)
        return None

def set_reset_token(user_id, token, expires_at):
    """Set reset token and expiry for a user, syncing with Google Sheets."""
    try:
        supabase = get_supabase_client()
        expires_str = expires_at.isoformat()
        res = supabase.table("users").update({
            "reset_token": token,
            "reset_token_expires_at": expires_str
        }).eq("id", user_id).execute()
        return True
    except Exception as e:
        current_app.logger.error("Failed to set reset token: %s", str(e))
        return False

def get_user_by_reset_token(token):
    """Fetch user by reset token if valid and not expired."""
    try:
        supabase = get_supabase_client()
        res = supabase.table("users").select("*").eq("reset_token", token).execute()
        if res.data:
            user = res.data[0]
            expires_at = datetime.fromisoformat(user.get("reset_token_expires_at"))
            if datetime.now(timezone.utc) <= expires_at:
                return user
        return None
    except Exception as e:
        current_app.logger.error("Supabase get_user_by_reset_token failed: %s", e)
        return None

def update_password_with_token(user_id, new_password):
    """Update password, clear reset token, and sync with Sheets."""
    password_hash = generate_password_hash(new_password)
    session_token = uuid.uuid4().hex
    
    try:
        supabase = get_supabase_client()
        res = supabase.table("users").update({
            "password_hash": password_hash,
            "reset_token": None,
            "reset_token_expires_at": None,
            "session_token": session_token
        }).eq("id", user_id).execute()
        
        if res.data:
            from services.sheets_service import _get_sheet
            try:
                sheet = _get_sheet("Users")
                all_rows = sheet.get_all_values()
                for i, row in enumerate(all_rows[1:], start=2):
                    if row[0] == str(user_id):
                        sheet.update_cell(i, 3, "OMITTED") # password is index 2 -> 3
                        break
            except Exception as e:
                current_app.logger.error("Failed to sync new password to Sheets: %s", e)
        return True
    except Exception as e:
        if "session_token" in str(e):
            # Graceful fallback for missing session_token column
            try:
                supabase.table("users").update({
                    "password_hash": password_hash,
                    "reset_token": None,
                    "reset_token_expires_at": None
                }).eq("id", user_id).execute()
                return True
            except Exception as inner_e:
                current_app.logger.error("Failed to update password with token: %s", str(inner_e))
                return False
        current_app.logger.error("Failed to update password with token: %s", str(e))
        return False
