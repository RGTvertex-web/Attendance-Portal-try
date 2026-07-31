"""
routes/admin.py — Admin-only routes
"""
import logging
from datetime import datetime, timezone
from flask import Blueprint, flash, redirect, render_template, request, url_for, g

from services import sheets_service as ss
from services import supabase_service as supa
from services.email_service import send_manager_invite_email
from services.auth_helpers import admin_required
from services.attendance_service import evaluate_attendance_for_date, get_org_summary
from config import get_departments

admin_bp = Blueprint("admin", __name__)
logger = logging.getLogger(__name__)

VALID_ROLES = ("admin", "manager", "intern")
VALID_STATUSES = ("active", "inactive")

# ── Dashboard ─────────────────────────────────────────────────────────────────
@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    department = request.args.get("department", "")
    
    from services.attendance_service import get_org_summary, get_attendance_trend_for_target, get_org_performance_analytics
    org = get_org_summary(department=department if department else None)
    managers = supa.get_all_managers()
    
    # Filter managers if department selected
    if department:
        managers = [m for m in managers if m.get("department") == department]
        
    all_warnings = ss.get_all_warnings()
    if department:
        # We need to filter warnings by department. 
        all_warnings = [w for w in all_warnings if w.get("department") == department]
    recent_warnings = sorted(all_warnings, key=lambda w: w["date"], reverse=True)[:10]
    
    # Filter trend data
    all_users = supa.get_all_profiles()
    if department:
        dept_intern_ids = {u["id"] for u in all_users if u.get("role") == "intern" and u.get("department") == department}
        trend_data = get_attendance_trend_for_target(dept_intern_ids)
    else:
        trend_data = get_attendance_trend_for_target(None)
        
    perf_analytics = get_org_performance_analytics(department=department if department else None)
    
    # Calculate department-wise intern numbers
    dept_stats = {}
    for u in all_users:
        if u.get("role") == "intern":
            d = u.get("department") or "Unknown"
            dept_stats[d] = dept_stats.get(d, 0) + 1
            
    return render_template("admin/dashboard.html",
                           org=org, managers=managers,
                           recent_warnings=recent_warnings,
                           dept_stats=dept_stats,
                           trend=trend_data,
                           perf_analytics=perf_analytics,
                           department_filter=department)


# ── Users ─────────────────────────────────────────────────────────────────────
@admin_bp.route("/users")
@admin_required
def users():
    all_users = supa.get_all_profiles()
    role_filter = request.args.get("role", "")
    status_filter = request.args.get("status", "")
    department_filter = request.args.get("department", "")
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25
    
    if role_filter:
        all_users = [u for u in all_users if u.get("role") == role_filter]
    if status_filter:
        all_users = [u for u in all_users if u.get("status") == status_filter]
    if department_filter:
        all_users = [u for u in all_users if u.get("department") == department_filter]
    if search_query:
        all_users = [u for u in all_users if search_query in (u.get("name") or "").lower() or search_query in (u.get("email") or "").lower() or search_query in (u.get("intern_id") or "").lower()]
        
    total = len(all_users)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_users = all_users[start:end]
        
    managers = [u for u in supa.get_all_profiles() if u.get("role") in ("manager", "admin") and u.get("status") == "active"]
    return render_template("admin/users.html", users=paginated_users, managers=managers,
                           role_filter=role_filter, status_filter=status_filter, department_filter=department_filter,
                           search=request.args.get("search", ""), page=page, total_pages=total_pages)

@admin_bp.route("/repair-sheet-sync", methods=["POST"])
@admin_required
def repair_sheet_sync():
    """Admin route to manually push all Supabase profiles to Sheets to repair any drift."""
    try:
        from services.sheets_service import update_user_in_sheet
        all_users = supa.get_all_profiles()
        count = 0
        for user in all_users:
            # We only sync fields that are mapped in the sheet
            updates = {
                "name": user.get("name"),
                "role": user.get("role"),
                "department": user.get("department"),
                "manager_id": user.get("manager_id"),
                "internship_duration_months": user.get("internship_duration_months"),
                "joining_date": user.get("joining_date"),
                "leave_allotted_days": user.get("leave_allotted_days"),
                "status": user.get("status")
            }
            if update_user_in_sheet(user["id"], **updates):
                count += 1
        
        logger.info(f"AUDIT: Admin {g.user['id']} triggered manual sheet sync repair. Synced {count} users.")
        flash(f"Successfully synced {count} profiles to Google Sheets.", "success")
    except Exception as e:
        logger.error(f"Failed to repair sheet sync: {e}")
        flash(f"Failed to sync sheets: {str(e)}", "error")
        
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/create", methods=["GET", "POST"])
@admin_required
def create_user():
    managers = supa.get_all_managers()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "intern")
        department = request.form.get("department", "")
        manager_id = request.form.get("manager_id", "")
        duration = request.form.get("internship_duration_months", "")

        errors = _validate_user_form(name, email, password, role)

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/user_form.html", managers=managers,
                                   action="create", form_data=request.form)

        try:
            supa.sign_up(email, password, name, role, department, manager_id, duration if role == "intern" else None)
            logger.info("AUDIT: Admin %s created user %s role=%s", g.user["id"], email, role)
            flash(f"User '{name}' created successfully.", "success")
            return redirect(url_for("admin.users"))
        except Exception as e:
            flash(f"Failed to create user: {str(e)}", "error")
            return render_template("admin/user_form.html", managers=managers,
                                   action="create", form_data=request.form)

    return render_template("admin/user_form.html", managers=managers,
                           action="create", form_data={})


@admin_bp.route("/users/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    user_data = supa.get_profile(user_id)
    if not user_data:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))
        
    managers = supa.get_all_managers()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        role = request.form.get("role", user_data.get("role"))
        department = request.form.get("department", user_data.get("department"))
        manager_id = request.form.get("manager_id", "")
        status = request.form.get("status", user_data.get("status"))

        old_manager_id = user_data.get("manager_id")
        updates = {"name": name, "role": role, "department": department, "manager_id": manager_id if manager_id else None, "status": status}
        
        try:
            supa.update_profile(user_id, **updates)
            
            # Log manager reassignment
            if role == "intern" and old_manager_id != manager_id:
                ss.log_audit(g.user["id"], "reassigned_manager", f"Reassigned {name} from {old_manager_id} to {manager_id}")
                
                # Send emails to old and new managers and intern
                old_mgr = supa.get_profile(old_manager_id) if old_manager_id else None
                new_mgr = supa.get_profile(manager_id) if manager_id else None
                
                old_email = old_mgr.get("email") if old_mgr else None
                old_name = old_mgr.get("name") if old_mgr else None
                new_email = new_mgr.get("email") if new_mgr else None
                new_name = new_mgr.get("name") if new_mgr else None
                
                from services.email_service import send_manager_reassignment_notifications
                send_manager_reassignment_notifications(old_email, old_name, new_email, new_name, user_data.get("email"), name, request.host_url.rstrip('/'))
                
            ss.log_audit(g.user["id"], "edited_user", f"Edited user {user_id}")
            if role != user_data.get("role"):
                ss.log_audit(g.user["id"], "escalated_role", f"Escalated user {user_id} role from {user_data.get('role')} to {role}")
            
            flash("User updated.", "success")
            return redirect(url_for("admin.users"))
        except Exception as e:
            flash(f"Failed to update user: {str(e)}", "error")

    return render_template("admin/user_form.html", managers=managers,
                           action="edit", form_data=user_data, user_id=user_id)


@admin_bp.route("/users/<user_id>/deactivate", methods=["POST"])
@admin_required
def deactivate_user(user_id):
    if user_id == g.user["id"]:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.users"))
    
    reason = request.form.get("deactivation_reason", "")
    if not reason:
        flash("Deactivation reason is required.", "error")
        return redirect(url_for("admin.users"))
        
    try:
        supa.update_profile(user_id, status="inactive")
        
        # We need to manually update the deactivation_reason since update_profile doesn't handle it
        supabase = supa.get_supabase_client()
        supabase.table("users").update({"deactivation_reason": reason}).eq("id", user_id).execute()
        
        # Also update Google Sheets
        try:
            from services import sheets_service as ss
            ss.update_user_in_sheet(user_id, status="inactive", deactivation_reason=reason)
        except Exception as e:
            logger.error("Failed to update user in sheets: %s", str(e))
            
        logger.info("AUDIT: Admin %s deactivated user %s (Reason: %s)", g.user["id"], user_id, reason)
        
        # Send deactivation email
        user_prof = supa.get_profile(user_id)
        if user_prof and user_prof.get("email"):
            from services.email_service import send_account_deactivation_notification
            send_account_deactivation_notification(user_prof["email"], user_prof["name"], reason)
            
        flash(f"User deactivated successfully (Reason: {reason}).", "success")
    except Exception as e:
        flash(f"Failed to deactivate user: {str(e)}", "error")
        
    return redirect(url_for("admin.users"))

@admin_bp.route("/users/<user_id>/delete", methods=["POST"])
@admin_required
def delete_user_route(user_id):
    if user_id == g.user["id"]:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for("admin.users"))
        
    try:
        # Delete from Supabase Auth & Users table
        from services import supabase_service
        supabase_service.delete_user(user_id)
        
        # Delete from Google Sheets
        try:
            from services import sheets_service as ss
            ss.delete_user_from_sheet(user_id)
        except Exception as e:
            logger.error("Failed to delete user from sheets: %s", str(e))
            
        logger.info("AUDIT: Admin %s permanently deleted user %s", g.user["id"], user_id)
        flash("User deleted permanently.", "success")
    except Exception as e:
        flash(f"Failed to delete user: {str(e)}", "error")
        
    return redirect(url_for("admin.users"))

@admin_bp.route("/users/bulk", methods=["POST"])
@admin_required
def bulk_users():
    action = request.form.get("bulk_action")
    department = request.form.get("bulk_department")
    user_ids = request.form.getlist("user_ids")
    
    if not user_ids:
        flash("No users selected.", "error")
        return redirect(url_for("admin.users"))
        
    if action == "deactivate":
        reason = request.form.get("bulk_deactivation_reason", "terminated")
        count = 0
        for uid in user_ids:
            if uid == g.user["id"]:
                continue
            try:
                supa.update_profile(uid, status="inactive")
                supabase = supa.get_supabase_client()
                supabase.table("users").update({"deactivation_reason": reason}).eq("id", uid).execute()
                from services import sheets_service as ss
                ss.update_user_in_sheet(uid, status="inactive", deactivation_reason=reason)
                ss.log_audit(g.user["id"], "bulk_deactivate", f"Deactivated user {uid}")
                count += 1
            except Exception as e:
                logger.error(f"Bulk deactivate failed for {uid}: {e}")
        flash(f"Deactivated {count} users.", "success")
        
    elif action == "export_csv":
        all_users = supa.get_all_profiles()
        selected_users = [u for u in all_users if u["id"] in user_ids]
        
        import io
        import csv
        from flask import make_response
        si = io.StringIO()
        if selected_users:
            cw = csv.DictWriter(si, fieldnames=list(selected_users[0].keys()))
            cw.writeheader()
            cw.writerows(selected_users)
            
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=users_export.csv"
        output.headers["Content-type"] = "text/csv"
        ss.log_audit(g.user["id"], "bulk_export", f"Exported {len(user_ids)} users")
        return output
        
    elif department:
        count = 0
        for uid in user_ids:
            try:
                supa.update_profile(uid, department=department)
                ss.log_audit(g.user["id"], "bulk_reassign_dept", f"Reassigned {uid} to {department}")
                count += 1
            except Exception as e:
                logger.error(f"Bulk reassign failed for {uid}: {e}")
        flash(f"Reassigned {count} users to {department}.", "success")
        
    else:
        flash("No action selected.", "warning")
        
    return redirect(url_for("admin.users"))


# ── Invites ───────────────────────────────────────────────────────────────────
@admin_bp.route("/managers/invite", methods=["GET", "POST"])
@admin_required
def invite_manager():
    departments = get_departments()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        department = request.form.get("department", "")

        if not email or department not in departments:
            flash("Valid email and department are required.", "error")
            return redirect(url_for("admin.invite_manager"))

        import secrets
        token = secrets.token_urlsafe(32)
        
        try:
            ss.create_invite(email, department, token, g.user["id"])
            logger.info("AUDIT: Admin %s created manager invite for %s (dept: %s)", g.user["id"], email, department)
            
            try:
                host_url = request.host_url.rstrip("/")
                success = send_manager_invite_email(email, token, department, host_url)
                if not success:
                    logger.error("Failed to send manager invite email to %s", email)
                    flash(f"Invite created, but failed to send email to {email}.", "warning")
                else:
                    flash(f"Invite sent successfully to {email}.", "success")
            except Exception as e:
                logger.error("Exception sending manager invite email to %s: %s", email, str(e))
                flash(f"Invite created, but failed to send email to {email}.", "warning")
                
            return redirect(url_for("admin.users"))
        except Exception as e:
            flash(f"Failed to create invite: {str(e)}", "error")
            return redirect(url_for("admin.invite_manager"))

    return render_template("admin/invite_manager.html", departments=departments)


# ── Global Attendance & Reports ───────────────────────────────────────────────
@admin_bp.route("/attendance")
@admin_required
def attendance():
    # Fetch requested date or default to today
    selected_date_str = request.args.get("date", "").strip()
    if not selected_date_str:
        selected_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
    try:
        selected_dt = datetime.strptime(selected_date_str, "%Y-%m-%d")
        is_weekend = selected_dt.weekday() >= 5
    except ValueError:
        is_weekend = False
        
    import json
    all_profiles = supa.get_all_profiles()
    interns = [u for u in all_profiles if u.get("role") == "intern"]
    
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25
    
    if search_query:
        interns = [u for u in interns if search_query in (u.get("name") or "").lower() or search_query in (u.get("email") or "").lower()]
        
    total = len(interns)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_interns = interns[start:end]
    
    managers = {u["id"]: u.get("name", "Unknown") for u in all_profiles if u.get("role") in ["manager", "admin"]}
    
    todays_attendance = ss.get_attendance_for_date(selected_date_str)
    all_submissions = ss.get_all_submissions()
    
    for intern in paginated_interns:
        # Attach manager name
        m_id = intern.get("manager_id")
        intern["manager_name"] = managers.get(m_id) if m_id else None
        
        # Check attendance
        att = next((a for a in todays_attendance if a["intern_id"] == intern["id"]), None)
        intern["today_status"] = att["status"] if att else "pending"
        
        # Get report specifically for the selected date
        subs = [s for s in all_submissions if s.get("intern_id") == intern["id"]]
        daily_sub = None
        if subs:
            for s in subs:
                if s["task_id"] == f"REPORT-{selected_date_str}" or s.get("submitted_at", "")[:10] == selected_date_str:
                    daily_sub = s
                    break
            
        if daily_sub:
            try:
                daily_sub["report_data"] = json.loads(daily_sub.get("notes", "{}"))
            except:
                daily_sub["report_data"] = {"given": "Unknown", "done": daily_sub.get("notes", ""), "remaining": "Unknown"}
            intern["latest_report"] = daily_sub
        else:
            intern["latest_report"] = None

    return render_template("admin/attendance.html", students=paginated_interns, today=selected_date_str, is_weekend=is_weekend, search=request.args.get("search", ""), page=page, total_pages=total_pages)


@admin_bp.route("/attendance/mark", methods=["POST"])
@admin_required
def mark_attendance():
    intern_id = request.form.get("student_id", "").strip()
    status = request.form.get("status", "").strip() # 'present', 'absent', or 'on_leave'
    date_str = request.form.get("date", "").strip()
    
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
    if not intern_id or status not in ['present', 'absent', 'on_leave']:
        flash("Invalid attendance data.", "error")
        return redirect(url_for("admin.attendance", date=date_str))
        
    intern = supa.get_profile(intern_id)
    if not intern:
        flash("Intern not found.", "error")
        return redirect(url_for("admin.attendance", date=date_str))
        
    # Check if there is a daily report for this date
    subs = ss.get_submissions_for_student(intern_id)
    has_report = any(s["task_id"] == f"REPORT-{date_str}" or s["submitted_at"][:10] == date_str for s in subs)

    department = intern.get("department", "Unknown")
    ss.upsert_attendance(intern_id, department, date_str, status, "admin_override", "daily", f"Marked by {g.user['name']} (Admin)")
    logger.info("AUDIT: Admin %s marked attendance %s for intern %s on %s", g.user["id"], status, intern_id, date_str)
    
    flash_msg = f"Attendance marked as {status.upper()} for {intern['name']} on {date_str}."
    
    # Auto-issue warning if absent and no report
    if status == 'absent' and not has_report:
        reason = f"Marked absent and no daily report submitted for {date_str}."
        ss.create_warning(intern_id, department, date_str, reason, issued_by="system")
        logger.info("AUDIT: System auto-issued warning for intern %s on %s", intern_id, date_str)
        flash_msg += " (Warning auto-issued for missing report)"
        
    from services.email_service import send_attendance_notification
    intern_email = intern.get("email")
    if not intern_email:
        logger.error("Cannot send attendance email — intern %s has no email on file", intern_id)
    else:
        try:
            intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern.get("intern_id") else intern.get("name", "Intern")
            success = send_attendance_notification(intern_email, intern_display, date_str, status, g.user["name"] + " (Admin)", department, request.host_url.rstrip('/'))
            if not success:
                logger.error("Failed to send attendance email to intern %s", intern_id)
        except Exception as e:
            logger.error("Exception sending attendance email to intern %s: %s", intern_id, str(e))
            
    flash(flash_msg, "success")
    return redirect(url_for("admin.attendance", date=date_str))


# ── Attendance Override (Legacy) ──────────────────────────────────────────────
@admin_bp.route("/attendance/override", methods=["POST"])
@admin_required
def override_attendance():
    intern_id = request.form.get("student_id", "")
    date_str = request.form.get("date", "")
    linked_task_id = request.form.get("linked_task_id", "")
    new_status = request.form.get("new_status", "")
    notes = request.form.get("notes", "")

    allowed_statuses = ("present", "present_late", "absent", "on_leave")
    if new_status not in allowed_statuses:
        flash("Invalid status for override.", "error")
        return redirect(request.referrer or url_for("admin.dashboard"))

    success = ss.override_attendance(intern_id, date_str, linked_task_id,
                                     new_status, g.user["id"], notes)
    if success:
        logger.info("AUDIT: Admin %s overrode attendance intern=%s date=%s → %s",
                    g.user["id"], intern_id, date_str, new_status)
        flash("Attendance overridden.", "success")
    else:
        flash("Attendance record not found.", "error")
    return redirect(request.referrer or url_for("admin.dashboard"))


# ── Manual Attendance Trigger ─────────────────────────────────────────────────
@admin_bp.route("/trigger-attendance", methods=["POST"])
@admin_required
def trigger_attendance():
    date_str = request.form.get("date", "")
    try:
        if date_str:
            target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            target = datetime.now(timezone.utc)
    except ValueError:
        flash("Invalid date format.", "error")
        return redirect(url_for("admin.dashboard"))

    summary = evaluate_attendance_for_date(target)
    logger.info("AUDIT: Admin %s manually triggered attendance for %s", g.user["id"], summary["date"])
    flash(f"Attendance evaluated for {summary['date']}: "
          f"{summary['present']} present, {summary['absent']} absent, "
          f"{summary['warnings_created']} warnings created.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/leaves")
@admin_required
def leaves():
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25

    leaves_list = ss.get_all_leaves()
    leaves_list.sort(key=lambda l: (0 if l["status"] == "pending" else 1, l.get("start_date", "")), reverse=True)
    
    all_profiles = {u["id"]: u for u in supa.get_all_profiles()}
    
    for l in leaves_list:
        intern = all_profiles.get(l["intern_id"])
        l["student_name"] = intern["name"] if intern else "Unknown Intern"
        l["rgt_id"] = (intern.get("intern_id") or intern.get("rgt_id") if intern else None) or "Not Set"
        
    if search_query:
        leaves_list = [l for l in leaves_list if search_query in (l.get("student_name") or "").lower() or search_query in (l.get("reason") or "").lower()]
        
    total = len(leaves_list)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_leaves = leaves_list[start:end]
        
    return render_template("admin/leaves.html", leaves=paginated_leaves, search=request.args.get("search", ""), page=page, total_pages=total_pages)


@admin_bp.route("/leaves/<leave_id>")
@admin_required
def leave_detail(leave_id):
    leave = ss.get_leave_by_id(leave_id)
    if not leave:
        flash("Leave request not found.", "error")
        return redirect(url_for("admin.leaves"))
        
    intern = supa.get_profile(leave["intern_id"])
    leave["student_name"] = intern["name"] if intern else "Unknown Intern"
    
    return render_template("admin/leave_detail.html", leave=leave)


@admin_bp.route("/leaves/<leave_id>/decide", methods=["POST"])
@admin_required
def decide_leave(leave_id):
    from services.email_service import send_leave_decision_notification
    
    leave = ss.get_leave_by_id(leave_id)
    if not leave:
        flash("Leave request not found.", "error")
        return redirect(url_for("admin.leaves"))
        
    status = request.form.get("status", "").strip()
    remarks = request.form.get("remarks", "").strip()
    
    if status not in ["approved", "rejected"]:
        flash("Invalid status.", "error")
        return redirect(url_for("admin.leave_detail", leave_id=leave_id))
        
    ss.update_leave_status(leave_id, status, decided_by=g.user["name"], remarks=remarks)
    logger.info("AUDIT: Admin %s %s leave %s", g.user["id"], status, leave_id)
    
    intern = supa.get_profile(leave["intern_id"])
    intern_email = intern.get("email", "intern@example.com")
    intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern and intern.get("intern_id") else intern.get("name", "")
    send_leave_decision_notification(intern_email, intern_display, status, leave["start_date"], leave["end_date"], remarks, g.user["name"], request.host_url.rstrip('/'))
        
    flash(f"Leave request has been {status}.", "success")
    return redirect(url_for("admin.leaves"))


@admin_bp.route("/reports")
@admin_required
def reports():
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 10

    all_submissions = ss.get_all_submissions()
    reports_list = []
    for s in all_submissions:
        if str(s.get("task_id", "")).startswith("REPORT-"):
            date_str = str(s["task_id"]).replace("REPORT-", "")
            is_reviewed = s.get("status") in ["approved", "reviewed", "rejected"]
            reports_list.append({
                "report_id": s["submission_id"],
                "intern_id": s["intern_id"],
                "report_type": "daily",
                "period_start": date_str,
                "period_end": None,
                "submitted_at": s.get("submitted_at", ""),
                "reviewed_by": "Manager" if is_reviewed else None,
            })

    reports_list.sort(key=lambda r: (0 if not r["reviewed_by"] else 1, r.get("submitted_at", "")), reverse=True)
    
    all_profiles = {u["id"]: u for u in supa.get_all_profiles()}
    
    for r in reports_list:
        intern = all_profiles.get(r["intern_id"])
        r["student_name"] = intern["name"] if intern else "Unknown Intern"
        r["department"] = intern["department"] if intern else "Unknown"
        r["rgt_id"] = (intern.get("intern_id") or intern.get("rgt_id") if intern else None) or "Not Set"
        
    if search_query:
        reports_list = [r for r in reports_list if search_query in (r.get("student_name") or "").lower() or search_query in (r.get("report_type") or "").lower()]
        
    total = len(reports_list)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_reports = reports_list[start:end]
        
    return render_template("admin/reports.html", reports=paginated_reports, search=request.args.get("search", ""), page=page, total_pages=total_pages)


@admin_bp.route("/reports/<report_id>", methods=["GET", "POST"])
@admin_required
def report_detail(report_id):
    from services.email_service import send_leave_decision_notification # if needed later
    
    # fetch the submission
    all_submissions = ss.get_all_submissions()
    submission = next((s for s in all_submissions if s["submission_id"] == report_id), None)
    
    if not submission:
        flash("Report not found.", "error")
        return redirect(url_for("admin.reports"))
        
    if request.method == "POST":
        status = request.form.get("status", "reviewed")
        remarks = request.form.get("remarks", "").strip()
        ss.update_submission(report_id, status=status, remarks=remarks)
        logger.info("AUDIT: Admin %s reviewed report %s", g.user["id"], report_id)
        flash("Report review saved.", "success")
        return redirect(url_for("admin.reports"))
        
    intern = supa.get_profile(submission["intern_id"])
    import json
    try:
        report_data = json.loads(submission.get("notes", "{}"))
    except:
        report_data = {"given": "Unknown", "done": submission.get("notes", ""), "remaining": "Unknown"}
        
    date_str = str(submission.get("task_id", "")).replace("REPORT-", "")
    
    return render_template("admin/report_detail.html", 
                           report=submission, 
                           report_data=report_data, 
                           intern=intern, 
                           date_str=date_str)


@admin_bp.route("/warnings", methods=["GET", "POST"])
@admin_required
def warnings():
    if request.method == "POST":
        action = request.form.get("action")
        warning_id = request.form.get("warning_id")
        if action == "revoke" and warning_id:
            ss.update_warning_status(warning_id, "revoked", g.user["id"])
            flash("Warning revoked successfully.", "success")
        return redirect(url_for("admin.warnings"))

    department = request.args.get("department", "")
    intern_id = request.args.get("intern_id", "")
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25

    all_warnings = ss.get_all_warnings()
    if department:
        all_warnings = [w for w in all_warnings if w.get("department") == department]
    if intern_id:
        all_warnings = [w for w in all_warnings if w.get("intern_id") == intern_id]

    interns = supa.get_all_profiles()
    intern_map = {u["id"]: u for u in interns if u.get("role") == "intern"}
    
    for w in all_warnings:
        intern_prof = intern_map.get(w["intern_id"], {})
        w["intern_name"] = intern_prof.get("name", "Unknown Intern")
        w["rgt_id"] = intern_prof.get("intern_id") or "Not Set"

    if search_query:
        all_warnings = [w for w in all_warnings if search_query in (w.get("intern_name") or "").lower() or search_query in (w.get("reason") or "").lower()]

    all_warnings.sort(key=lambda x: x["date"], reverse=True)
    
    total = len(all_warnings)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_warnings = all_warnings[start:end]

    # For filter dropdowns
    filter_interns = [u for u in interns if u.get("role") == "intern"]
    if department:
        filter_interns = [i for i in filter_interns if i.get("department") == department]

    return render_template("admin/warnings.html", 
                           warnings=paginated_warnings, 
                           interns=filter_interns,
                           filters={"department": department, "intern_id": intern_id},
                           search=request.args.get("search", ""), page=page, total_pages=total_pages)


@admin_bp.route("/performance")
@admin_required
def performance():
    department = request.args.get("department", "")
    month = request.args.get("month", "") # format: YYYY-MM
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 10

    all_perf = ss.get_all_performance_reports()
    
    interns = supa.get_all_profiles()
    intern_map = {u["id"]: u for u in interns if u.get("role") == "intern"}
    manager_map = {u["id"]: u for u in interns if u.get("role") in ("manager", "admin")}
    
    if department:
        all_perf = [p for p in all_perf if intern_map.get(p["intern_id"], {}).get("department") == department]
    if month:
        all_perf = [p for p in all_perf if p.get("period_start", "")[:7] == month or p.get("period_end", "")[:7] == month]

    for p in all_perf:
        intern_prof = intern_map.get(p["intern_id"], {})
        p["intern_name"] = intern_prof.get("name", "Unknown Intern")
        p["department"] = intern_prof.get("department", "Unknown")
        p["manager_name"] = manager_map.get(p["manager_id"], {}).get("name", "Unknown Manager")
        p["rgt_id"] = intern_prof.get("intern_id") or "Not Set"

    if search_query:
        all_perf = [p for p in all_perf if search_query in (p.get("intern_name") or "").lower() or search_query in (p.get("manager_name") or "").lower()]

    all_perf.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)
    
    total = len(all_perf)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_perf = all_perf[start:end]

    return render_template("admin/performance.html", 
                           reports=paginated_perf,
                           filters={"department": department, "month": month},
                           search=request.args.get("search", ""), page=page, total_pages=total_pages)

@admin_bp.route("/audit")
@admin_required
def audit_logs():
    page = int(request.args.get("page", 1))
    per_page = 50
    search_query = request.args.get("search", "").lower()
    
    logs = ss.get_all_audit_logs()
    
    if search_query:
        logs = [L for L in logs if search_query in (L.get("action") or "").lower() or search_query in (L.get("details") or "").lower() or search_query in (L.get("actor_id") or "").lower()]
        
    logs.sort(key=lambda x: x["timestamp"], reverse=True)
    
    total = len(logs)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_logs = logs[start:end]
    
    # Resolve UUIDs to Names
    try:
        import re
        from services.supabase_service import get_all_profiles
        all_profiles = get_all_profiles()
        profile_map = {u["id"]: u["name"] for u in all_profiles if "id" in u and "name" in u}
        
        uuid_pattern = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.IGNORECASE)
        
        for log in paginated_logs:
            actor_id = log.get("actor_id", "")
            log["actor_name"] = profile_map.get(actor_id, actor_id)
            
            details = log.get("details", "")
            if details:
                def replace_uuid(match):
                    uid = match.group(0).lower()
                    return profile_map.get(uid, match.group(0))
                log["details"] = uuid_pattern.sub(replace_uuid, details)
    except Exception as e:
        logger.error(f"Failed to resolve names for audit logs: {e}")
        for log in paginated_logs:
            log["actor_name"] = log.get("actor_id", "")
    
    return render_template("admin/audit.html", logs=paginated_logs, search=search_query, page=page, total_pages=total_pages)


import io
from flask import make_response

@admin_bp.route("/exports/<report_type>/<format>")
@admin_required
def export_data(report_type, format):
    if report_type not in ["performance", "attendance", "leaves"]:
        flash("Invalid export type.", "error")
        return redirect(url_for("admin.dashboard"))
        
    dept_filter = request.args.get("department", "").strip()
    manager_filter = request.args.get("manager_id", "").strip()
    date_filter = request.args.get("date", "").strip()
        
    data = []
    if report_type == "performance":
        data = ss.get_all_performance_reports()
        if date_filter:
            # Filter by week_start or week_end
            data = [d for d in data if d.get("week_start") == date_filter or d.get("week_end") == date_filter]
    elif report_type == "attendance":
        if date_filter:
            data = ss.get_attendance_for_date(date_filter)
        else:
            data = ss.get_all_attendance()
    elif report_type == "leaves":
        data = ss.get_all_leaves()
        if date_filter:
            data = [d for d in data if d.get("start_date") <= date_filter <= d.get("end_date")]
    
    # Filter by department or manager if applicable
    if data and (dept_filter or manager_filter):
        all_users = supa.get_all_profiles()
        user_map = {u["id"]: u for u in all_users}
        filtered_data = []
        for d in data:
            intern = user_map.get(d.get("intern_id"))
            if not intern:
                continue
            if dept_filter and intern.get("department") != dept_filter:
                continue
            if manager_filter and intern.get("manager_id") != manager_filter:
                continue
            filtered_data.append(d)
        data = filtered_data
    
    if not data:
        flash("No data available to export with the given filters.", "warning")
        return redirect(url_for("admin.dashboard"))
        
    headers = list(data[0].keys())
    
    if format == "csv":
        import csv
        si = io.StringIO()
        cw = csv.DictWriter(si, fieldnames=headers)
        cw.writeheader()
        cw.writerows(data)
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename={report_type}.csv"
        output.headers["Content-type"] = "text/csv"
        return output
        
    elif format == "excel":
        try:
            import pandas as pd
            df = pd.DataFrame(data)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Sheet1')
            response = make_response(output.getvalue())
            response.headers["Content-Disposition"] = f"attachment; filename={report_type}.xlsx"
            response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            return response
        except ImportError:
            flash("Excel export requires pandas and openpyxl.", "error")
            return redirect(url_for("admin.dashboard"))
            
    elif format == "pdf":
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            buffer = io.BytesIO()
            p = canvas.Canvas(buffer, pagesize=letter)
            p.drawString(100, 750, f"RGTvertex {report_type.title()} Report")
            y = 700
            for row in data:
                line = " | ".join([f"{str(v)[:20]}" for v in row.values()])
                p.drawString(50, y, line)
                y -= 20
                if y < 50:
                    p.showPage()
                    y = 750
            p.save()
            response = make_response(buffer.getvalue())
            response.headers["Content-Disposition"] = f"attachment; filename={report_type}.pdf"
            response.headers["Content-type"] = "application/pdf"
            return response
        except ImportError:
            flash("PDF export requires reportlab.", "error")
            return redirect(url_for("admin.dashboard"))
            
    flash("Invalid format.", "error")
    return redirect(url_for("admin.dashboard"))

def _validate_user_form(name, email, password, role):
    errors = []
    if not name:
        errors.append("Name is required.")
    if not email or "@" not in email:
        errors.append("Valid email is required.")
    if not password or len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if role not in VALID_ROLES:
        errors.append("Invalid role.")
    return errors

@admin_bp.route("/holidays", methods=["GET", "POST"])
@admin_required
def holidays():
    if request.method == "POST":
        action = request.form.get("action")
        date_str = request.form.get("date")
        
        if action == "add":
            name = request.form.get("name")
            if date_str and name:
                success = ss.add_holiday(date_str, name)
                if success:
                    ss.log_audit(g.user["id"], "added_holiday", f"Added holiday {name} on {date_str}")
                    flash(f"Added holiday {name}.", "success")
                else:
                    flash(f"Failed to add holiday {name}. Please try again.", "error")
            else:
                flash("Date and Name are required.", "error")
        elif action == "delete":
            if date_str:
                success = ss.delete_holiday(date_str)
                if success:
                    ss.log_audit(g.user["id"], "deleted_holiday", f"Deleted holiday on {date_str}")
                    flash(f"Deleted holiday on {date_str}.", "success")
                else:
                    flash(f"Failed to delete holiday. Please try again.", "error")
        return redirect(url_for("admin.holidays"))
        
    all_holidays = ss.get_all_holidays()
    all_holidays.sort(key=lambda x: x["date"])
    return render_template("admin/holidays.html", holidays=all_holidays)


@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        import json
        
        # Departments (comma separated string -> list)
        depts_str = request.form.get("departments", "")
        depts_list = [d.strip() for d in depts_str.split(",") if d.strip()]
        ss.update_setting("departments", json.dumps(depts_list))
        
        ss.update_setting("at_risk_consecutive", request.form.get("at_risk_consecutive", "2"))
        ss.update_setting("at_risk_rolling_count", request.form.get("at_risk_rolling_count", "4"))
        ss.update_setting("at_risk_rolling_window", request.form.get("at_risk_rolling_window", "30"))
        ss.update_setting("manager_notification_email", request.form.get("manager_notification_email", ""))
        
        ss.log_audit(g.user["id"], "updated_settings", "Updated global settings")
        flash("Settings updated successfully. They will take effect shortly.", "success")
        return redirect(url_for("admin.settings"))
        
    current_settings = ss.get_all_settings()
    
    # Format departments list for textarea
    import json
    try:
        depts = json.loads(current_settings.get("departments", "[]"))
        if not depts:
            from config import DEPARTMENTS
            depts = DEPARTMENTS
        depts_str = ", ".join(depts)
    except:
        from config import DEPARTMENTS
        depts_str = ", ".join(DEPARTMENTS)
        
    return render_template("admin/settings.html", settings=current_settings, depts_str=depts_str)


# ── Admin Announcements Management ─────────────────────────────────────────────
@admin_bp.route("/announcements", methods=["GET", "POST"])
@admin_required
def announcements():
    all_anns = ss.get_all_announcements()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            title = request.form.get("title", "").strip()
            category = request.form.get("category", "General Notice").strip()
            status = request.form.get("status", "Active Now").strip()
            color = request.form.get("color", "#7c3aed").strip()
            content = request.form.get("content", "").strip()
            
            if not title or not content:
                flash("Title and Content are required for an announcement.", "error")
            else:
                import uuid
                new_ann = {
                    "id": str(uuid.uuid4())[:8],
                    "title": title,
                    "category": category,
                    "status": status,
                    "color": color,
                    "content": content,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d")
                }
                all_anns.insert(0, new_ann)
                ss.save_announcements(all_anns)
                ss.log_audit(g.user["id"], "created_announcement", f"Created announcement '{title}'")
                flash("Announcement published successfully!", "success")
        elif action == "delete":
            ann_id = request.form.get("ann_id")
            all_anns = [a for a in all_anns if str(a.get("id")) != str(ann_id)]
            ss.save_announcements(all_anns)
            ss.log_audit(g.user["id"], "deleted_announcement", f"Deleted announcement ID {ann_id}")
            flash("Announcement removed successfully.", "success")
        return redirect(url_for("admin.announcements"))
        
    return render_template("admin/announcements.html", announcements=all_anns)


# ── Admin Task Management ──────────────────────────────────────────────────────
@admin_bp.route("/tasks")
@admin_required
def tasks():
    tasks_list = ss.get_all_tasks()
    interns = supa.get_all_profiles()
    interns = [u for u in interns if u.get("role") == "intern" and u.get("status") == "active"]
    
    # Get all submissions to cross-reference
    all_subs = ss.get_all_submissions()
    for t in tasks_list:
        subs = [s for s in all_subs if s["task_id"] == t["task_id"]]
        t["submission_count"] = len(subs)
        
    from config import TASK_CATEGORIES
    department_filter = request.args.get("department", "")
    category_filter = request.args.get("category", "")
    
    if department_filter:
        tasks_list = [t for t in tasks_list if t.get("department") == department_filter]
    if category_filter:
        tasks_list = [t for t in tasks_list if t.get("category") == category_filter]
        
    page = int(request.args.get("page", 1))
    per_page = 25
    total = len(tasks_list)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_tasks = tasks_list[start:end]
        
    return render_template("admin/tasks.html", tasks=paginated_tasks, students=interns, 
                           categories=TASK_CATEGORIES, department_filter=department_filter, 
                           category_filter=category_filter, page=page, total_pages=total_pages)


@admin_bp.route("/tasks/create", methods=["POST"])
@admin_required
def create_task():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "Project Task").strip()
    assigned_to = request.form.get("assigned_to", "all").strip()
    due_date = request.form.get("due_date", "").strip()
    department = request.form.get("department", "All").strip()
    
    if not title or not due_date:
        flash("Title and Due Date are required.", "error")
        return redirect(url_for("admin.tasks"))
        
    try:
        ss.create_task(title=title, description=description, category=category,
                       department=department, assigned_to=assigned_to,
                       assigned_by=f"Admin ({g.user.get('name', 'Admin')})", due_date=due_date)
        ss.log_audit(g.user["id"], "created_task", f"Admin created task '{title}' for department '{department}'")
        flash(f"Task '{title}' assigned successfully to {department} department!", "success")
    except Exception as e:
        logger.error(f"Error creating admin task: {e}")
        flash("Failed to assign task. Please check system logs.", "error")
        
    return redirect(url_for("admin.tasks"))


@admin_bp.route("/tasks/delete/<task_id>", methods=["POST"])
@admin_required
def delete_task(task_id):
    try:
        success = ss.delete_task(task_id)
        if success:
            ss.log_audit(g.user["id"], "deleted_task", f"Deleted task ID {task_id}")
            flash("Task deleted successfully.", "success")
        else:
            flash("Task not found or already deleted.", "error")
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        flash("Failed to delete task.", "error")
    return redirect(url_for("admin.tasks"))


@admin_bp.route("/exports/performance-report/<intern_id>")
@admin_required
def export_performance_report_pdf(intern_id):
    from services.pdf_service import generate_internship_report_pdf
    try:
        intern_profile = supa.get_profile(intern_id)
        if not intern_profile:
            flash("Intern not found.", "error")
            return redirect(url_for("admin.users"))
            
        pdf_bytes = generate_internship_report_pdf(intern_profile, host_url=request.host_url)
        response = make_response(pdf_bytes)
        display_id = intern_profile.get("intern_id") or intern_profile.get("rgt_id") or f"RGTV-INT-{str(intern_profile.get('id', ''))[:4]}"
        filename = f"RGTvertex_Official_Evaluation_Report_{display_id}.pdf"
        response.headers["Content-Disposition"] = f"inline; filename={filename}"
        response.headers["Content-Type"] = "application/pdf"
        return response
    except Exception as e:
        logger.error("Failed to export PDF report for intern %s: %s", intern_id, e, exc_info=True)
        flash("Could not generate PDF report. Please try again later.", "error")
        return redirect(url_for("admin.users"))


@admin_bp.route("/debug/performance/<intern_id>")
@admin_required
def debug_performance(intern_id):
    """Diagnostic: shows raw performance sheet data for a given intern_id."""
    from flask import jsonify
    from services.cache_service import global_cache
    global_cache.invalidate("Performance")

    all_reports = ss.get_all_performance_reports()
    matched = [r for r in all_reports if str(r.get("intern_id", "")).strip().lower() == str(intern_id).strip().lower()]
    all_intern_ids_in_sheet = list({str(r.get("intern_id", "")).strip() for r in all_reports})

    intern_profile = supa.get_profile(intern_id)

    return jsonify({
        "queried_intern_id": intern_id,
        "intern_profile_found": bool(intern_profile),
        "intern_profile_id": intern_profile.get("id") if intern_profile else None,
        "intern_profile_intern_id": intern_profile.get("intern_id") if intern_profile else None,
        "intern_profile_email": intern_profile.get("email") if intern_profile else None,
        "total_performance_rows_in_sheet": len(all_reports),
        "matched_reports_count": len(matched),
        "matched_reports": [
            {
                "report_id": r.get("report_id"),
                "intern_id": r.get("intern_id"),
                "period": f"{r.get('period_start')} to {r.get('period_end')}",
                "submitted_at": r.get("submitted_at"),
                "total_score": r.get("total_score"),
                "grade_band": r.get("grade_band"),
            }
            for r in matched
        ],
        "all_intern_ids_in_performance_sheet": all_intern_ids_in_sheet,
    })
