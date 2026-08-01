"""
routes/auth.py — Authentication routes (login, signup, logout) using Supabase Auth.
"""
import logging
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, g
from extensions import limiter
from services import supabase_service as supa
from services import sheets_service as ss
from config import get_departments

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if getattr(g, "user", None):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        
        if not email or not password:
            error = "Email and password are required."
        else:
            try:
                auth_data = supa.sign_in(email, password)
                if auth_data and auth_data.get("profile"):
                    profile = auth_data["profile"]
                    session.clear()
                    session["user_id"] = profile["id"]
                    session["session_token"] = profile.get("session_token")
                    session.permanent = True
                    
                    # Log them in
                    flash("Logged in successfully.", "success")
                    role = profile.get("role")
                    if role == "admin":
                        return redirect(url_for("admin.dashboard"))
                    elif role == "manager":
                        return redirect(url_for("manager.dashboard"))
                    else:
                        return redirect(url_for("intern.dashboard"))
                else:
                    error = "Invalid credentials."
            except Exception as e:
                err_msg = str(e)
                if "Your account has been deactivated" in err_msg or "Your internship has concluded" in err_msg or "Invalid credentials" in err_msg:
                    error = err_msg
                else:
                    error = f"Login failed: {err_msg}"
                logger.warning("Login failed for %s: %s", email, err_msg)

    return render_template("auth/login.html", error=error)

@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def signup():
    if getattr(g, "user", None):
        return redirect(url_for("index"))

    error = None
    managers = supa.get_all_managers()
    departments = get_departments()

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        role = "intern"  # Public signup is strictly for interns now
        department = request.form.get("department")
        phone = request.form.get("phone", "").strip()
        college_name = request.form.get("college_name", "").strip()
        manager_id = request.form.get("manager_id")
        joining_date = request.form.get("joining_date", "").strip()
        internship_duration = request.form.get("internship_duration_months")
        if not name or not email or not password or not department:
            error = "Name, email, password, and department are required."
        elif not joining_date and role == "intern":
            error = "Joining date is required for interns."
        elif department not in departments:
            error = "Invalid department selected."
        else:
            if joining_date:
                try:
                    from datetime import datetime
                    datetime.strptime(joining_date, "%Y-%m-%d")
                except ValueError:
                    error = "Invalid joining date format."
            
            if not error:
                try:
                    if role == "intern" and not internship_duration:
                        error = "Interns must select an internship duration."
                    else:
                        if role == "intern":
                            # Automatic manager assignment
                            manager_id = supa.get_least_loaded_manager(department)
                            # It's okay if manager_id is None, they will be auto-assigned when a manager signs up
                        else:
                            # Fallback for manual manager ID (if role != intern)
                            if manager_id:
                                manager_profile = supa.get_profile(manager_id)
                                if manager_profile and manager_profile.get("department") != department:
                                    error = "Manager must be in the same department."
                        
                        if not error:
                            auth_data = supa.sign_up(
                                email=email,
                                password=password,
                                name=name,
                                role=role,
                                department=department,
                                manager_id=manager_id,
                                internship_duration_months=internship_duration,
                                joining_date=joining_date,
                                phone=phone,
                                college_name=college_name
                            )
                            
                            if auth_data and auth_data.get("profile"):
                                profile = auth_data["profile"]
                                session.clear()
                                session["user_id"] = profile["id"]
                                session["session_token"] = profile.get("session_token")
                                session.permanent = True
                                flash("Account created! Welcome.", "success")
                                
                                # Send emails
                                from services.email_service import send_welcome_email, send_new_intern_manager_notification
                                host_url = request.host_url.rstrip("/")
                                disp_id = profile.get("intern_id") or profile.get("rgt_id") or profile["id"]
                                
                                if role == "intern":
                                    send_welcome_email(profile["email"], profile["name"], disp_id, host_url)
                                    if manager_id:
                                        mgr = supa.get_profile(manager_id)
                                        if mgr and mgr.get("email"):
                                            send_new_intern_manager_notification(mgr["email"], mgr["name"], profile["name"], disp_id, host_url)
                                
                                if role == "manager":
                                    return redirect(url_for("manager.dashboard"))
                                else:
                                    return redirect(url_for("intern.dashboard"))
                            else:
                                error = "Failed to create account."
                except Exception as e:
                    error = f"Sign up failed: {str(e)}"
                    logger.error("Signup failed for %s: %s", email, str(e))

    return render_template("auth/signup.html", error=error, managers=managers, departments=departments)

@auth_bp.route("/manager/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def manager_signup():
    if getattr(g, "user", None):
        return redirect(url_for("index"))

    departments = get_departments()
    error = None

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        department = request.form.get("department")
        
        if not name or not email or not password or not department:
            error = "Name, email, password, and department are required."
        elif department not in departments:
            error = "Invalid department selected."
        else:
            try:
                auth_data = supa.sign_up(
                    email=email,
                    password=password,
                    name=name,
                    role="manager",
                    department=department
                )
                
                if auth_data and auth_data.get("profile"):
                    profile = auth_data["profile"]
                    
                    # Auto-assign this manager to all unassigned interns in the department
                    try:
                        all_interns = supa.get_all_profiles()
                        unassigned = [u for u in all_interns if u.get("role") == "intern" and u.get("department") == department and not u.get("manager_id")]
                        for intern in unassigned:
                            supa.update_profile(intern["id"], manager_id=profile["id"])
                        if unassigned:
                            logger.info(f"Auto-assigned {len(unassigned)} interns to new manager {profile['id']}")
                    except Exception as assign_e:
                        logger.error(f"Failed to auto-assign interns to new manager: {assign_e}")
                        
                    session.clear()
                    session["user_id"] = profile["id"]
                    session["session_token"] = profile.get("session_token")
                    session.permanent = True
                    flash("Manager account created! Welcome.", "success")
                    return redirect(url_for("manager.dashboard"))
                else:
                    error = "Failed to create account."
            except Exception as e:
                error = f"Sign up failed: {str(e)}"
                logger.error("Manager signup failed for %s: %s", email, str(e))

    return render_template("auth/manager_signup.html", error=error, departments=departments)

@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    supa.sign_out()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per hour")
def forgot_password():
    if getattr(g, "user", None):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email = request.form.get("email")
        if email:
            import secrets
            from datetime import datetime, timedelta, timezone
            from services.email_service import send_password_reset_email
            
            user = supa.get_user_by_email(email)
            if user:
                token = secrets.token_urlsafe(32)
                expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
                
                if supa.set_reset_token(user["id"], token, expires_at):
                    reset_link = url_for("auth.reset_password", token=token, _external=True)
                    send_password_reset_email(email, user.get("name", "User"), reset_link)
            
            # Always show the same message regardless of whether the email exists
            flash("If that email is registered, you will receive a password reset link shortly.", "success")
            return redirect(url_for("auth.login"))
        else:
            error = "Email is required."
            
    return render_template("auth/forgot_password.html", error=error)

@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def reset_password(token):
    if getattr(g, "user", None):
        return redirect(url_for("index"))
        
    user = supa.get_user_by_reset_token(token)
    if not user:
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))
        
    error = None
    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        
        if not password or not confirm_password:
            error = "Both password fields are required."
        elif password != confirm_password:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters long."
        else:
            if supa.update_password_with_token(user["id"], password):
                # Log audit event
                from services.sheets_service import log_audit
                log_audit(user["id"], "PASSWORD_RESET", "Password was reset via email link.")
                
                if user and user.get("email"):
                    from services.email_service import send_password_changed_notification
                    send_password_changed_notification(user["email"], user["name"], request.host_url.rstrip("/"))
                
                flash("Your password has been successfully reset. Please log in.", "success")
                return redirect(url_for("auth.login"))
            else:
                error = "An error occurred while resetting your password. Please try again."
                
    return render_template("auth/reset_password.html", error=error, token=token)

from services.auth_helpers import login_required

@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    from services import supabase_service as supa
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        
        # Validate password confirmation
        if password:
            if password != confirm_password:
                flash("New passwords do not match. No changes were saved.", "error")
                return redirect(url_for("auth.profile"))
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return redirect(url_for("auth.profile"))
        
        try:
            if g.user.get("role") == "intern":
                university = request.form.get("university", "").strip()
                if university and university != g.user.get("college_name"):
                    supa.update_profile(g.user["id"], college_name=university)
                    g.user["college_name"] = university
                    
            if name or password:
                updated_user = supa.update_profile_and_password(
                    user_id=g.user["id"],
                    name=name if name else None,
                    new_password=password if password else None
                )
                if updated_user:
                    g.user["name"] = updated_user["name"]
                    if password:
                        session["session_token"] = updated_user.get("session_token")
                    flash("Profile updated successfully.", "success")
                else:
                    flash("No changes made.", "info")
        except Exception as e:
            flash(f"Error updating profile: {str(e)}", "error")
            
        return redirect(url_for("auth.profile"))
    
    # ── Gather role-specific extra data ─────────────────────────────────────────
    extra = {}
    role = g.user.get("role")
    
    if role == "intern":
        # Cycle data
        try:
            from services.internship_cycle_service import get_internship_cycle
            extra["cycle"] = get_internship_cycle(
                g.user.get("joining_date"),
                g.user.get("internship_duration_months"),
                g.user.get("created_at")
            )
        except Exception:
            extra["cycle"] = None
        # Manager info
        extra["managers"] = supa.get_managers_by_department(g.user.get("department"))
        
    elif role in ("manager", "admin"):
        try:
            extra["intern_count"] = len(supa.get_profiles_by_manager(g.user["id"]))
        except Exception:
            extra["intern_count"] = 0
    
    # ── Format joined date ───────────────────────────────────────────────────────
    created_at_raw = g.user.get("created_at", "")
    if created_at_raw:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            extra["member_since"] = dt.strftime("%d %B %Y")
        except Exception:
            extra["member_since"] = created_at_raw[:10]
    else:
        extra["member_since"] = "Unknown"
        
    return render_template("auth/profile.html", extra=extra)


@auth_bp.route("/verify/<intern_id>")
def verify_intern(intern_id):
    try:
        supabase = supa.get_supabase_client()
        
        if intern_id == "DEBUG":
            all_users = supabase.table("users").select("*").execute()
            return {"users": all_users.data}
            
        res = supabase.table("users").select("*").eq("intern_id", intern_id).execute()
        
        if not res.data:
            res = supabase.table("users").select("*").eq("rgt_id", intern_id).execute()
            
        if not res.data:
            try:
                res = supabase.table("users").select("*").eq("id", intern_id).execute()
            except Exception:
                pass
                
        # Fallback for dynamic PDF IDs (e.g. RGTV-INT-0005 -> id 5)
        if not res.data and str(intern_id).startswith("RGTV-INT-"):
            raw_id = str(intern_id).replace("RGTV-INT-", "")
            stripped_id = raw_id.lstrip("0") or "0"
            
            try:
                res = supabase.table("users").select("*").eq("id", stripped_id).execute()
            except Exception:
                pass
                
            if not res.data:
                try:
                    # Search by UUID prefix since PDF uses first 4 chars of UUID
                    res = supabase.table("users").select("*").ilike("id", f"{raw_id}%").execute()
                except Exception:
                    pass

            if not res.data:
                res = supabase.table("users").select("*").eq("rgt_id", raw_id).execute()
            if not res.data:
                res = supabase.table("users").select("*").eq("intern_id", raw_id).execute()

        intern = res.data[0] if (res and hasattr(res, 'data') and res.data) else None
        
        if not intern:
            return render_template("auth/verify.html", valid=False, intern_id=intern_id, error="intern is None. res.data was: " + str(getattr(res, 'data', 'NO_DATA'))), 404
            
        if intern.get("role") != "intern":
            return render_template("auth/verify.html", valid=False, intern_id=intern_id, error="Role is not intern: " + str(intern.get("role"))), 404
            
        return render_template("auth/verify.html", valid=True, intern=intern), 200
    except Exception as e:
        logger.error(f"Verification error for {intern_id}: {e}")
        return render_template("auth/verify.html", valid=False, intern_id=intern_id, error="Exception: " + str(e)), 500
