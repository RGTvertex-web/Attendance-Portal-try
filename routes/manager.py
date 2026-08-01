"""
routes/manager.py — Manager routes
"""
import logging
import json
from datetime import datetime, timezone
from flask import Blueprint, flash, redirect, render_template, request, url_for, g, jsonify, Response

from services import sheets_service as ss
from services import supabase_service as supa
from services.auth_helpers import manager_required, can_manage_intern
from services.attendance_service import get_manager_summary, get_attendance_trend_for_target, get_student_attendance_summary
from services.internship_cycle_service import get_internship_cycle

manager_bp = Blueprint("manager", __name__)
logger = logging.getLogger(__name__)

@manager_bp.route("/dashboard")
@manager_required
def dashboard():
    summary = get_manager_summary(g.user["id"])
    interns = supa.get_profiles_by_manager(g.user["id"])
    
    # Enrich intern data for the Team Table
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_leaves = ss.get_leaves_for_manager(g.user["id"])
    
    for intern in interns:
        intern_id = intern["id"]
        
        # 1. Cycle Data (duration, days remaining)
        try:
            dur = int(intern.get("internship_duration_months", 0))
        except ValueError:
            dur = 0
        intern["cycle"] = get_internship_cycle(intern.get("joining_date"), dur, intern.get("created_at"))
        
        # 2. Latest Performance
        reports = sorted(ss.get_performance_reports_for_student(intern_id), key=lambda r: r.get("submitted_at", ""), reverse=True)
        intern["latest_performance"] = reports[0] if reports else None
        
        # 3. Warnings Count
        warnings = ss.get_warnings_for_student(intern_id)
        intern["warnings_count"] = len([w for w in warnings if w.get("status") == "active"])
        
        # 4. Attendance Summary
        intern["att_summary"] = get_student_attendance_summary(intern_id)
        
        # 5. On Leave Today Flag
        intern["on_leave_today"] = any(
            l.get("status") == "approved" and l.get("start_date") <= today_str <= l.get("end_date")
            for l in all_leaves if l.get("intern_id") == intern_id
        )
        
        # 6. Leaves count
        intern["leaves_taken"] = len([l for l in all_leaves if l.get("intern_id") == intern_id and l.get("status") == "approved"])

    intern_ids = {s["id"] for s in interns}
    trend_data = get_attendance_trend_for_target(intern_ids) if intern_ids else {"labels": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], "values": [0,0,0,0,0,0,0]}
    
    # Enrich pending data for To-Do List widget
    pending_leaves = [l for l in all_leaves if l.get("status") == "pending"]
    for l in pending_leaves:
        intern = next((i for i in interns if i["id"] == l["intern_id"]), None)
        l["student_name"] = intern["name"] if intern else "Unknown Intern"
        
    all_reports = ss.get_reports_for_manager(g.user["id"])
    pending_reports = [r for r in all_reports if not r.get("reviewed_by")]
    for r in pending_reports:
        intern = next((i for i in interns if i["id"] == r["intern_id"]), None)
        r["student_name"] = intern["name"] if intern else "Unknown Intern"

    # Calculate Top Stat Cards Metrics
    current_month_str = today_str[:7]
    active_interns = [i for i in interns if i.get("status", "active") == "active"]
    total_interns_count = len(active_interns)
    new_joiners_count = len([i for i in interns if (i.get("joining_date") or "").startswith(current_month_str)])
    
    total_att = 0
    valid_att_count = 0
    for i in interns:
        pct = i.get("att_summary", {}).get("percentage")
        if pct is not None:
            total_att += pct
            valid_att_count += 1
    average_attendance_percent = round(total_att / valid_att_count) if valid_att_count > 0 else 0
    
    departments_covered = len(set(i.get("department") for i in active_interns if i.get("department")))

    # Enrich Data for new Dashboard Table
    all_attendance = ss.get_all_attendance()
    today_attendance = [a for a in all_attendance if a.get("date") == today_str]
    today_reports = [r for r in all_reports if (r.get("submitted_at") or "").startswith(today_str)]
    
    for intern in interns:
        intern_id = intern["id"]
        
        # Today's attendance status
        today_att_record = next((a for a in today_attendance if a.get("intern_id") == intern_id), None)
        if intern.get("on_leave_today"):
            intern["today_attendance_status"] = "On Leave"
        elif today_att_record:
            if today_att_record.get("status") in ["present", "present_late"]:
                intern["today_attendance_status"] = "Present"
            elif today_att_record.get("status") == "absent":
                intern["today_attendance_status"] = "Absent"
            else:
                intern["today_attendance_status"] = "Not Marked"
        else:
            intern["today_attendance_status"] = "Not Marked"
            
        # Today's report time
        today_report_record = next((r for r in today_reports if r.get("intern_id") == intern_id), None)
        if today_report_record:
            time_str = (today_report_record.get("submitted_at") or "").split("T")[-1].replace("Z", "")[:5] # gets HH:MM
            intern["today_report_time"] = time_str
        else:
            intern["today_report_time"] = "—"
            
        # Days remaining and Duration text
        cycle = intern.get("cycle", {})
        intern["duration_text"] = f"{intern.get('internship_duration_months', 0)} Months"
        intern["days_remaining_text"] = f"{cycle.get('days_remaining', 0)} Days Left"
        
        # Leave balance text
        leaves_taken = intern.get("leaves_taken", 0)
        leaves_allotted = intern.get("leave_allotted_days", 0)
        intern["leave_balance_text"] = f"{leaves_taken} / {leaves_allotted} days"

    # Group by Department for Progress-Ring Cards
    dept_stats = {}
    for intern in active_interns:
        dept = intern.get("department", "Unknown")
        if dept not in dept_stats:
            dept_stats[dept] = {
                "name": dept,
                "intern_count": 0,
                "total_att_pct": 0,
                "valid_att_count": 0,
                "new_joiners": 0,
                "pending_leaves": len([l for l in pending_leaves if l.get("department") == dept])
            }
            
        dept_stats[dept]["intern_count"] += 1
        pct = intern.get("att_summary", {}).get("attendance_percent")
        if pct is not None:
            dept_stats[dept]["total_att_pct"] += pct
            dept_stats[dept]["valid_att_count"] += 1
            
        if (intern.get("joining_date") or "").startswith(current_month_str):
            dept_stats[dept]["new_joiners"] += 1

    for dept, stats in dept_stats.items():
        if stats["valid_att_count"] > 0:
            stats["avg_attendance_pct"] = round(stats["total_att_pct"] / stats["valid_att_count"])
        else:
            stats["avg_attendance_pct"] = 0

    # Fetch Announcements
    announcements = ss.get_all_announcements()

    return render_template("manager/dashboard.html", 
                           summary=summary, 
                           students=interns, 
                           trend=trend_data,
                           pending_leaves=pending_leaves,
                           pending_reports=pending_reports,
                           total_interns_count=total_interns_count,
                           new_joiners_count=new_joiners_count,
                           average_attendance_percent=average_attendance_percent,
                           departments_covered=departments_covered,
                           dept_stats=dept_stats.values(),
                           announcements=announcements)

@manager_bp.route("/attendance")
@manager_required
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
        
    all_interns = supa.get_profiles_by_manager(g.user["id"])
    
    # Filter by joining date
    all_interns = [i for i in all_interns if not i.get("joining_date") or i.get("joining_date") <= selected_date_str]
    
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25
    
    if search_query:
        all_interns = [u for u in all_interns if search_query in (u.get("name") or "").lower() or search_query in (u.get("email") or "").lower()]
        
    total = len(all_interns)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    interns = all_interns[start:end]
        
    todays_attendance = ss.get_attendance_for_date(selected_date_str)
    
    for intern in interns:
        # Check attendance
        att = next((a for a in todays_attendance if a["intern_id"] == intern["id"]), None)
        intern["today_status"] = att["status"] if att else "pending"
        
        # Get report specifically for the selected date
        subs = ss.get_submissions_for_student(intern["id"])
        daily_sub = None
        if subs:
            # Look for a submission with task_id == REPORT-{selected_date_str} OR submitted on that date
            for s in subs:
                if s["task_id"] == f"REPORT-{selected_date_str}" or s["submitted_at"][:10] == selected_date_str:
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

    return render_template("manager/attendance.html", students=interns, today=selected_date_str, is_weekend=is_weekend, search=request.args.get("search", ""), page=page, total_pages=total_pages)


@manager_bp.route("/attendance/mark", methods=["POST"])
@manager_required
def mark_attendance():
    intern_id = request.form.get("student_id", "").strip()
    status = request.form.get("status", "").strip() # 'present', 'absent', or 'on_leave'
    date_str = request.form.get("date", "").strip()
    
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    else:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for("manager.attendance"))
        
    if not intern_id or status not in ['present', 'absent', 'on_leave']:
        flash("Invalid attendance data.", "error")
        return redirect(url_for("manager.attendance", date=date_str))
        
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    if dt_obj.weekday() >= 5:
        flash("Cannot manually mark attendance on weekends. Weekends are automatically set to Weekend Leave.", "error")
        return redirect(url_for("manager.attendance", date=date_str))
        
    all_holidays = {h.get("date", "")[:10] for h in ss.get_all_holidays()}
    if date_str in all_holidays:
        flash("Cannot manually mark attendance on a holiday. Holidays are automatically set to Holiday Leave.", "error")
        return redirect(url_for("manager.attendance", date=date_str))
        
    # Verify manager owns this intern
    intern = supa.get_profile(intern_id)
    if not can_manage_intern(g.user, intern):
        flash("Access denied.", "error")
        return redirect(url_for("manager.attendance", date=date_str))
        
    # Check if there is a daily report for this date
    subs = ss.get_submissions_for_student(intern_id)
    has_report = any(s.get("task_id") == f"REPORT-{date_str}" or (s.get("submitted_at") or "")[:10] == date_str for s in subs)

    # Upsert attendance
    department = intern.get("department", "Unknown")
    ss.upsert_attendance(intern_id, department, date_str, status, "manager_override", "daily", f"Marked by {g.user.get('name', 'Manager')}")
    logger.info("AUDIT: Manager %s marked attendance %s for intern %s on %s", g.user.get("id"), status, intern_id, date_str)
    
    flash_msg = f"Attendance marked as {status.upper()} for {intern.get('name', 'Intern')} on {date_str}."
    
    # Auto-issue warning if absent and no report
    if status == 'absent' and not has_report:
        reason = f"Marked absent and no daily report submitted for {date_str}."
        ss.create_warning(intern_id, department, date_str, reason, issued_by="system")
        logger.info("AUDIT: System auto-issued warning for intern %s on %s", intern_id, date_str)
        flash_msg += " (Warning auto-issued for missing report)"
        
    from services.email_service import send_attendance_notification, send_manager_attendance_override_notification
    intern_email = intern.get("email")
    if not intern_email:
        logger.error("Cannot send attendance email — intern %s has no email on file", intern_id)
    else:
        try:
            intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern.get("intern_id") else intern.get("name", "Intern")
            success = send_attendance_notification(intern_email, intern_display, date_str, status, g.user.get("name", "Manager"), department, request.host_url.rstrip('/'))
            if not success:
                logger.error("Failed to send attendance email to intern %s", intern_id)
        except Exception as e:
            logger.error("Exception sending attendance email to intern %s: %s", intern_id, str(e))
            
    # Notify other managers in the department
    try:
        managers = supa.get_managers_by_department(department)
        intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern.get("intern_id") else intern.get("name", "Intern")
        for m in managers:
            if m.get("id") != g.user.get("id") and m.get("email"):
                send_manager_attendance_override_notification(
                    m["email"], m.get("name", "Manager"), intern_display, date_str, status, g.user.get("name", "Manager"), department, request.host_url.rstrip('/')
                )
    except Exception as e:
        logger.error("Exception notifying other managers of attendance mark: %s", str(e))
        
    flash(flash_msg, "success")
    return redirect(url_for("manager.attendance", date=date_str))


@manager_bp.route("/interns/<intern_id>")
@manager_required
def student_detail(intern_id):
    intern = supa.get_profile(intern_id)
    if not can_manage_intern(g.user, intern):
        flash("Access denied.", "error")
        return redirect(url_for("manager.dashboard"))

    from services.attendance_service import get_student_attendance_summary
    att_summary = get_student_attendance_summary(intern_id)
    warnings = ss.get_warnings_for_student(intern_id)
    submissions = ss.get_submissions_for_student(intern_id)
    perf_reports = ss.get_performance_reports_for_student(intern_id)
    
    # Parse JSON notes if possible
    for sub in submissions:
        try:
            sub["report_data"] = json.loads(sub.get("notes", "{}"))
        except:
            sub["report_data"] = {"given": "Unknown", "done": sub.get("notes", ""), "remaining": "Unknown"}

    manager_notes = ss.get_manager_notes(intern_id)

    return render_template("manager/student_detail.html",
                           student=intern, att_summary=att_summary,
                           warnings=warnings, submissions=submissions,
                           perf_reports=perf_reports, manager_notes=manager_notes)

@manager_bp.route("/interns/<intern_id>/notes", methods=["POST"])
@manager_required
def add_manager_note(intern_id):
    intern = supa.get_profile(intern_id)
    if not can_manage_intern(g.user, intern):
        flash("Access denied.", "error")
        return redirect(url_for("manager.dashboard"))
        
    note_content = request.form.get("note", "").strip()
    if not note_content:
        flash("Note cannot be empty.", "error")
    else:
        ss.create_manager_note(intern_id, g.user["id"], note_content)
        flash("Note added.", "success")
        
    return redirect(url_for("manager.student_detail", intern_id=intern_id))


@manager_bp.route("/warnings/issue", methods=["POST"])
@manager_required
def issue_warning():
    intern_id = request.form.get("student_id", "")
    reason = request.form.get("reason", "").strip()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not intern_id or not reason:
        flash("Intern and reason are required.", "error")
        return redirect(request.referrer or url_for("manager.dashboard"))

    intern = supa.get_profile(intern_id)
    if not can_manage_intern(g.user, intern):
        flash("Access denied.", "error")
        return redirect(url_for("manager.dashboard"))

    ss.create_warning(intern_id, intern.get("department", "Unknown"), date_str, reason, issued_by=g.user["name"])
    
    # Send email to intern
    from services.email_service import send_manual_warning_notification
    host_url = request.host_url.rstrip("/")
    send_manual_warning_notification(intern["email"], intern["name"], reason, g.user["name"], host_url)
    
    logger.info("AUDIT: Manager %s issued warning for intern %s", g.user["id"], intern_id)
    flash("Warning issued.", "success")
    return redirect(request.referrer or url_for("manager.dashboard"))

@manager_bp.route("/warnings/<warning_id>/revoke", methods=["POST"])
@manager_required
def revoke_warning_route(warning_id):
    # Verify manager owns the warning (or is admin)
    all_warnings = ss.get_all_warnings()
    warning = next((w for w in all_warnings if w["warning_id"] == warning_id), None)
    
    if not warning:
        flash("Warning not found.", "error")
        return redirect(request.referrer or url_for("manager.dashboard"))
        
    intern = supa.get_profile(warning.get("intern_id"))
    if not can_manage_intern(g.user, intern):
        flash("You can only revoke warnings for interns in your department.", "error")
        return redirect(request.referrer or url_for("manager.dashboard"))
        
    ss.revoke_warning(warning_id, g.user["name"])
    flash("Warning revoked.", "success")
    return redirect(request.referrer or url_for("manager.dashboard"))


@manager_bp.route("/extensions")
@manager_required
def extensions():
    extensions_list = ss.get_extensions_for_manager(g.user["id"])
    # Sort pending first, then by date descending
    extensions_list.sort(key=lambda ext: (0 if ext["status"] == "pending" else 1, ext.get("created_at", "")), reverse=True)
    
    all_profiles = {u["id"]: u for u in supa.get_profiles_by_manager(g.user["id"])}
    for ext in extensions_list:
        intern = all_profiles.get(ext["intern_id"])
        ext["student_name"] = intern["name"] if intern else "Unknown Intern"
        ext["rgt_id"] = (intern.get("intern_id") or intern.get("rgt_id") if intern else None) or "Not Set"
        
    return render_template("manager/extensions.html", extensions=extensions_list)


@manager_bp.route("/extensions/<extension_id>/decide", methods=["POST"])
@manager_required
def decide_extension(extension_id):
    from services.email_service import send_extension_decision_notification
    
    status = request.form.get("status")
    decision_notes = request.form.get("decision_notes", "").strip()
    
    if status not in ["approved", "rejected"]:
        flash("Invalid decision.", "error")
        return redirect(url_for("manager.extensions"))
        
    ext_list = ss.get_extensions_for_manager(g.user["id"])
    ext = next((e for e in ext_list if e["extension_id"] == extension_id), None)
    
    if not ext:
        flash("Extension request not found.", "error")
        return redirect(url_for("manager.extensions"))
        
    if ext["status"] != "pending":
        flash("Extension request has already been decided.", "error")
        return redirect(url_for("manager.extensions"))
        
    ss.update_extension_status(extension_id, status, decision_notes)
    logger.info("AUDIT: Manager %s %s extension %s", g.user["id"], status, extension_id)
    
    intern = supa.get_profile(ext["intern_id"])
    if intern:
        if status == "approved":
            # Update internship duration
            try:
                current_duration = int(intern.get("internship_duration_months", 0))
                requested_months = int(ext.get("requested_months", 0))
                new_duration = current_duration + requested_months
                
                # Update in Supabase and Sheets via central helper
                supa.update_profile(ext["intern_id"], internship_duration_months=new_duration)
                        
                logger.info("AUDIT: Extended internship for %s to %s months", ext["intern_id"], new_duration)
            except Exception as e:
                logger.error("Error extending internship duration: %s", e)
                flash("Error updating intern's duration.", "error")
                
        # Send decision email
        if intern.get("email"):
            try:
                intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern.get("intern_id") else intern.get("name", "Intern")
                send_extension_decision_notification(
                    intern.get("email"),
                    intern_display,
                    status,
                    ext.get("requested_months"),
                    decision_notes,
                    request.host_url.rstrip('/')
                )
            except Exception as e:
                logger.error("Error sending extension decision email: %s", e)
                
    flash(f"Extension request {status}.", "success")
    return redirect(url_for("manager.extensions"))
@manager_bp.route("/leaves")
@manager_required
def leaves():
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25
    
    leaves_list = ss.get_leaves_for_manager(g.user["id"])
    # Sort pending first, then by date descending
    leaves_list.sort(key=lambda l: (0 if l["status"] == "pending" else 1, l.get("start_date", "")), reverse=True)
    
    all_profiles = {u["id"]: u for u in supa.get_profiles_by_manager(g.user["id"])}
    # Map intern names and calculate leave balance
    for l in leaves_list:
        intern = all_profiles.get(l["intern_id"])
        if intern:
            l["student_name"] = intern["name"]
            try:
                allotted = int(intern.get("leave_allotted_days", 0))
            except ValueError:
                allotted = 0
                
            intern_leaves = ss.get_leaves_for_student(l["intern_id"])
            used = sum(float(il.get("days_requested", 0)) for il in intern_leaves if il["status"] == "approved")
            l["leave_balance"] = f"{allotted - used:g} / {allotted}"
        else:
            l["student_name"] = "Unknown Intern"
            l["leave_balance"] = "—"
            
    if search_query:
        leaves_list = [l for l in leaves_list if search_query in (l.get("student_name") or "").lower() or search_query in (l.get("reason") or "").lower()]
        
    total = len(leaves_list)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_leaves = leaves_list[start:end]
            
    return render_template("manager/leaves.html", leaves=paginated_leaves, search=request.args.get("search", ""), page=page, total_pages=total_pages)


@manager_bp.route("/leaves/<leave_id>")
@manager_required
def leave_detail(leave_id):
    leave = ss.get_leave_by_id(leave_id)
    if not leave:
        flash("Leave request not found.", "error")
        return redirect(url_for("manager.leaves"))
        
    intern = supa.get_profile(leave["intern_id"])
    if not can_manage_intern(g.user, intern):
        flash("Access denied.", "error")
        return redirect(url_for("manager.leaves"))
        
    # Get all pending leaves for navigation
    pending_leaves = [l for l in ss.get_leaves_for_manager(g.user["id"]) if l["status"] == "pending"]
    # Sort them by date descending (same as list view)
    pending_leaves.sort(key=lambda l: l.get("start_date", ""), reverse=True)
    
    prev_leave_id = None
    next_leave_id = None
    
    for i, pl in enumerate(pending_leaves):
        if pl["leave_id"] == leave_id:
            if i > 0:
                prev_leave_id = pending_leaves[i-1]["leave_id"]
            if i < len(pending_leaves) - 1:
                next_leave_id = pending_leaves[i+1]["leave_id"]
            break
            
    leave["student_name"] = intern["name"] if intern else "Unknown Intern"
    return render_template("manager/leave_detail.html", leave=leave, prev_leave_id=prev_leave_id, next_leave_id=next_leave_id)


@manager_bp.route("/leaves/<leave_id>/decide", methods=["POST"])
@manager_required
def decide_leave(leave_id):
    from services.email_service import send_leave_decision_notification
    
    leave = ss.get_leave_by_id(leave_id)
    if not leave:
        flash("Leave request not found.", "error")
        return redirect(url_for("manager.leaves"))
        
    intern = supa.get_profile(leave["intern_id"])
    if not can_manage_intern(g.user, intern):
        flash("Access denied.", "error")
        return redirect(url_for("manager.leaves"))
        
    status = request.form.get("status", "").strip()
    remarks = request.form.get("remarks", "").strip()
    
    if status not in ["approved", "rejected"]:
        flash("Invalid status.", "error")
        return redirect(url_for("manager.leave_detail", leave_id=leave_id))
        
    ss.update_leave_status(leave_id, status, decided_by=g.user["name"], remarks=remarks)
    logger.info("AUDIT: Manager %s %s leave %s", g.user["id"], status, leave_id)
    
    intern_email = intern.get("email")
    if not intern_email:
        logger.error("Cannot send leave decision email — intern %s has no email on file", intern["id"])
    else:
        try:
            intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern.get("intern_id") else intern.get("name", "")
            success = send_leave_decision_notification(intern_email, intern_display, status, leave["start_date"], leave["end_date"], remarks, g.user["name"], request.host_url.rstrip('/'))
            if not success:
                logger.error("Failed to send leave decision email to intern %s", intern["id"])
        except Exception as e:
            logger.error("Exception sending leave decision email to intern %s: %s", intern["id"], str(e))
        
    flash(f"Leave request has been {status}.", "success")
    return redirect(url_for("manager.leaves"))


@manager_bp.route("/reports")
@manager_required
def reports():
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 25
    
    reports_list = ss.get_reports_for_manager(g.user["id"])
    
    # Also include daily progress reports (from Submissions) for this manager's interns
    manager_interns = supa.get_profiles_by_manager(g.user["id"])
    if g.user_role == "admin":
        manager_interns = supa.get_all_profiles()
    intern_map = {str(i["id"]): i for i in manager_interns}
    
    all_subs = ss.get_all_submissions()
    for sub in all_subs:
        if str(sub.get("intern_id")) in intern_map:
            intern = intern_map[str(sub["intern_id"])]
            try:
                data = json.loads(sub.get("notes", "{}"))
                if isinstance(data, dict) and ("given" in data or "done" in data):
                    content_str = f"Task Given:\n{data.get('given', 'N/A')}\n\nWhat Was Done:\n{data.get('done', 'N/A')}\n\nRemaining / Next Steps:\n{data.get('remaining', 'N/A')}"
                else:
                    content_str = sub.get("notes", "")
            except Exception:
                content_str = sub.get("notes", "")
                
            if sub.get("content_link"):
                content_str += f"\n\nLink / Deliverable: {sub.get('content_link')}"
                
            sub_date = sub.get("submitted_at", "")[:10] if sub.get("submitted_at") else ""
            is_reviewed = sub.get("status") == "approved" or bool(sub.get("remarks"))
            
            reports_list.append({
                "report_id": sub.get("submission_id"),
                "intern_id": sub.get("intern_id"),
                "department": intern.get("department", "Unknown"),
                "manager_id": g.user["id"],
                "report_type": "daily",
                "period_start": sub_date,
                "period_end": sub_date,
                "content": content_str,
                "submitted_at": sub.get("submitted_at", ""),
                "reviewed_by": g.user["name"] if is_reviewed else "",
                "review_notes": sub.get("remarks", ""),
                "reviewed_at": "",
                "student_name": intern.get("name", "Unknown Intern"),
                "rgt_id": intern.get("intern_id") or intern.get("rgt_id") or "Not Set"
            })
    
    for r in reports_list:
        if "student_name" not in r or "rgt_id" not in r:
            intern = intern_map.get(str(r["intern_id"]))
            if intern:
                r["student_name"] = intern["name"]
                r["rgt_id"] = intern.get("intern_id") or intern.get("rgt_id") or "Not Set"
            else:
                r.setdefault("student_name", "Unknown Intern")
                r.setdefault("rgt_id", "Not Set")
            
    reports_list.sort(key=lambda r: (0 if not r.get("reviewed_by") else 1, r.get("submitted_at", "")), reverse=True)
        
    if search_query:
        reports_list = [r for r in reports_list if search_query in (r.get("student_name") or "").lower() or search_query in (r.get("report_type") or "").lower() or search_query in (r.get("content") or "").lower()]
        
    total = len(reports_list)
    cnt_rev = len([r for r in reports_list if r.get("reviewed_by")])
    cnt_pen = total - cnt_rev
    cnt_daily = len([r for r in reports_list if r.get("report_type") == "daily"])
    
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_reports = reports_list[start:end]
        
    return render_template("manager/reports.html", reports=paginated_reports, search=request.args.get("search", ""), page=page, total_pages=total_pages, total_count=total, cnt_rev=cnt_rev, cnt_pen=cnt_pen, cnt_daily=cnt_daily)


@manager_bp.route("/reports/<report_id>/review", methods=["POST"])
@manager_required
def review_report(report_id):
    notes = request.form.get("notes", "").strip()
    if not notes:
        flash("Review notes cannot be empty.", "error")
        return redirect(url_for("manager.reports"))
        
    report = ss.get_report_by_id(report_id)
    if report:
        intern = supa.get_profile(report.get("intern_id"))
        if not can_manage_intern(g.user, intern):
            flash("Report not found or access denied.", "error")
            return redirect(url_for("manager.reports"))
            
        ss.review_report(report_id, g.user["name"], notes)
        flash("Report marked as reviewed.", "success")
        return redirect(url_for("manager.reports"))
        
    # If not found in Reports, check Submissions (Daily Reports)
    all_subs = ss.get_all_submissions()
    sub = next((s for s in all_subs if str(s.get("submission_id")) == str(report_id)), None)
    if sub:
        intern = supa.get_profile(sub.get("intern_id"))
        if not can_manage_intern(g.user, intern):
            flash("Report not found or access denied.", "error")
            return redirect(url_for("manager.reports"))
            
        ss.update_submission(report_id, status="approved", remarks=notes)
        flash("Daily report marked as reviewed.", "success")
        return redirect(url_for("manager.reports"))
        
    flash("Report not found or access denied.", "error")
    return redirect(url_for("manager.reports"))

@manager_bp.route("/performance", methods=["GET", "POST"])
@manager_required
def performance():
    interns = supa.get_profiles_by_manager(g.user["id"])
    if request.method == "POST":
        intern_id = request.form.get("intern_id")
        period_start = request.form.get("period_start")
        period_end = request.form.get("period_end")
        
        logger.info(
            "MGR PERF POST: manager=%r submitting report for intern_id=%r (repr: %r) period=%s to %s",
            g.user.get("id"), intern_id, repr(intern_id), period_start, period_end
        )
        
        # 3 official criteria (scored out of 10, total out of 30)
        technical_skill = 0
        communication = 0
        discipline = int(request.form.get("discipline", 0))
        task_completion = int(request.form.get("task_completion", 0))
        initiative = int(request.form.get("initiative", 0))
        teamwork = discipline
        code_quality = 0
        
        strengths = request.form.get("strengths", "")
        areas_improvement = request.form.get("areas_improvement", "")
        overall_comments = request.form.get("overall_comments", "")
        
        total_score = discipline + task_completion + initiative
        percentage = (total_score / 30.0) * 100
        
        if percentage >= 90:
            grade_band = "Outstanding"
        elif percentage >= 80:
            grade_band = "Excellent"
        elif percentage >= 70:
            grade_band = "Good"
        elif percentage >= 60:
            grade_band = "Satisfactory"
        else:
            grade_band = "Needs Improvement"
            
        tc_val = request.form.get("tasks_completed", "").strip()
        ta_val = request.form.get("tasks_assigned", "").strip()
        t_comp = int(tc_val) if tc_val.isdigit() else None
        t_ass = int(ta_val) if ta_val.isdigit() else None
            
        ss.create_performance_report(
            intern_id, g.user["id"], period_start, period_end,
            technical_skill, communication, discipline, task_completion,
            initiative, teamwork, code_quality,
            total_score, grade_band, strengths, areas_improvement, overall_comments,
            tasks_completed=t_comp, tasks_assigned=t_ass
        )
        logger.info(
            "MGR PERF POST: report created for intern_id=%r — intern should now see this on their /intern/performance page",
            intern_id
        )
        flash("Monthly performance report submitted successfully.", "success")
        return redirect(url_for("manager.performance"))
        
    reports = ss.get_performance_reports_for_manager(g.user["id"])
    all_profiles = supa.get_all_profiles()
    for r in reports:
        r["intern_name"] = "Unknown Intern"
        r["intern_code"] = str(r.get("intern_id", "N/A"))
        r["intern_avatar"] = "IN"
        for p in all_profiles:
            if ss._match_user_id(p.get("id"), r.get("intern_id")) or ss._match_user_id(p.get("intern_id"), r.get("intern_id")) or ss._match_user_id(p.get("email"), r.get("intern_id")):
                r["intern_name"] = p.get("name", "Unknown Intern")
                r["intern_code"] = p.get("intern_id") or str(p.get("id", ""))
                r["intern_avatar"] = r["intern_name"][:2].upper() if r["intern_name"] else "IN"
                break
    return render_template("manager/performance.html", interns=interns, reports=reports)

@manager_bp.route("/performance/next-period/<intern_id>")
@manager_required
def get_next_performance_period(intern_id):
    from services.internship_cycle_service import get_internship_cycle
    from flask import jsonify
    from dateutil.relativedelta import relativedelta
    
    all_profiles = supa.get_all_profiles()
    target_intern = next((p for p in all_profiles if ss._match_user_id(p.get("id"), intern_id) or ss._match_user_id(p.get("intern_id"), intern_id) or ss._match_user_id(p.get("email"), intern_id)), None)
    if not target_intern:
        return jsonify({"error": "Intern not found"}), 404
        
    joining_date = target_intern.get("joining_date")
    duration = target_intern.get("internship_duration_months", 6)
    cycle_info = get_internship_cycle(joining_date, duration, target_intern.get("created_at"))
    
    existing_reports = ss.get_performance_reports_for_student(intern_id)
    
    # Determine which cycle months already have a submitted report
    reported_months = set()
    for idx, c in enumerate(cycle_info.get("all_cycles", [])):
        c_start = c["start"]
        c_end = c["end"]
        for r in existing_reports:
            r_start = str(r.get("period_start", ""))[:10]
            r_end = str(r.get("period_end", ""))[:10]
            if (r_start <= c_end and r_end >= c_start) or (r_start == "" and len(existing_reports) > idx):
                reported_months.add(c["month"])
                break
                
    next_cycle = None
    for c in cycle_info.get("all_cycles", []):
        if c["month"] not in reported_months:
            next_cycle = c
            break
            
    if not next_cycle:
        last_m = max([c["month"] for c in cycle_info.get("all_cycles", [])] or [0]) + 1
        j_date = datetime.strptime(cycle_info["joining_date"], "%Y-%m-%d").date()
        c_start = j_date + relativedelta(months=last_m - 1)
        c_end = j_date + relativedelta(months=last_m) - relativedelta(days=1)
        next_cycle = {"month": last_m, "start": c_start.strftime("%Y-%m-%d"), "end": c_end.strftime("%Y-%m-%d")}
        
    completed_months = cycle_info.get("completed_months", 0)
    
    is_due_yet = True
    if next_cycle["month"] > completed_months:
        is_due_yet = False
        message = f"No new monthly report due yet — next report due {next_cycle['end']}"
    else:
        message = f"Report due for Month {next_cycle['month']} ({next_cycle['start']} to {next_cycle['end']})"

    return jsonify({
        "period_start": next_cycle["start"],
        "period_end": next_cycle["end"],
        "month": next_cycle["month"],
        "is_due_yet": is_due_yet,
        "message": message,
        "reported_months": sorted(list(reported_months)),
        "joining_date": cycle_info["joining_date"]
    })


@manager_bp.route("/performance/edit/<report_id>", methods=["POST"])
@manager_required
def edit_performance(report_id):
    # Only process edits within 7 days or if edit_reason is provided
    report = next((r for r in ss.get_performance_reports_for_manager(g.user["id"]) if r["report_id"] == report_id), None)
    if not report:
        flash("Report not found.", "error")
        return redirect(url_for("manager.performance"))
        
    created_at = datetime.fromisoformat(report["submitted_at"].replace("Z", "+00:00"))
    days_old = (datetime.now(timezone.utc) - created_at).days
    
    edit_reason = request.form.get("edit_reason", "").strip()
    if days_old > 7 and not edit_reason:
        flash("Reports older than 7 days require an Amendment Reason.", "error")
        return redirect(url_for("manager.performance"))
        
    technical_skill = int(report.get("technical_skill", 0))
    communication = int(report.get("communication", 0))
    discipline = int(request.form.get("discipline", report["discipline"]))
    task_completion = int(request.form.get("task_completion", report["task_completion"]))
    initiative = int(request.form.get("initiative", report["initiative"]))
    teamwork = int(request.form.get("teamwork", report["teamwork"]))
    code_quality = int(request.form.get("code_quality", report["code_quality"]))
    
    total_score = discipline + task_completion + initiative + teamwork + code_quality
    percentage = (total_score / 50.0) * 100
    
    if percentage >= 90:
        grade_band = "Outstanding"
    elif percentage >= 80:
        grade_band = "Excellent"
    elif percentage >= 70:
        grade_band = "Good"
    elif percentage >= 60:
        grade_band = "Satisfactory"
    else:
        grade_band = "Needs Improvement"
        
    updates = {
        "technical_skill": technical_skill,
        "communication": communication,
        "discipline": discipline,
        "task_completion": task_completion,
        "initiative": initiative,
        "teamwork": teamwork,
        "code_quality": code_quality,
        "total_score": total_score,
        "grade_band": grade_band,
        "strengths": request.form.get("strengths", report["strengths"]),
        "areas_improvement": request.form.get("areas_improvement", report["areas_improvement"]),
        "overall_comments": request.form.get("overall_comments", report["overall_comments"]),
        "edit_reason": edit_reason
    }
    
    ss.update_performance_report(report_id, updates)
    flash("Report updated successfully.", "success")
    return redirect(url_for("manager.performance"))

# ══════════════════════════════════════════════════════════════════════════════
# TASK MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@manager_bp.route("/tasks")
@manager_required
def tasks():
    dept = g.user.get("department", "")
    tasks_list = ss.get_tasks_by_manager(g.user["id"], department=dept)
    interns = supa.get_profiles_by_manager(g.user["id"])
    
    # Get all submissions to cross-reference
    all_subs = ss.get_all_submissions()
    
    # Map tasks with submission counts and intern submission statuses
    for t in tasks_list:
        subs = [s for s in all_subs if s["task_id"] == t["task_id"]]
        t["submission_count"] = len(subs)
        
        intern_statuses = []
        for student in interns:
            sub = next((s for s in subs if s["intern_id"] == student["id"]), None)
            intern_statuses.append({
                "student": student,
                "submission": sub,
                "status": sub["status"] if sub else "pending",
                "submitted_at": sub["submitted_at"] if sub else "-",
                "notes": sub["notes"] if sub else "",
                "content_link": sub["content_link"] if sub else "",
                "submission_id": sub["submission_id"] if sub else None
            })
        t["intern_statuses"] = intern_statuses
            
    # Need categories for the template dropdown
    from config import TASK_CATEGORIES
    
    page = int(request.args.get("page", 1))
    per_page = 25
    total = len(tasks_list)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_tasks = tasks_list[start:end]
    
    return render_template("manager/tasks.html", tasks=paginated_tasks, students=interns, categories=TASK_CATEGORIES, page=page, total_pages=total_pages)

@manager_bp.route("/submissions/<submission_id>/review", methods=["POST"])
@manager_required
def review_submission(submission_id):
    status = request.form.get("status", "reviewed").strip()
    remarks = request.form.get("remarks", "").strip()
    
    ss.update_submission(submission_id, status=status, remarks=remarks)
    flash(f"Submission marked as {status.capitalize()}.", "success")
    return redirect(url_for("manager.tasks"))

@manager_bp.route("/tasks/<task_id>/mark_status", methods=["POST"])
@manager_required
def mark_task_status(task_id):
    intern_id = request.form.get("intern_id")
    status = request.form.get("status", "completed").strip()
    remarks = request.form.get("remarks", "").strip()
    
    if not intern_id:
        flash("Intern ID required.", "error")
        return redirect(url_for("manager.tasks"))
        
    sub = ss.get_submission(task_id, intern_id)
    if sub:
        ss.update_submission(sub["submission_id"], status=status, remarks=remarks)
    else:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ss.create_submission(
            task_id=task_id,
            intern_id=intern_id,
            content_link="Marked directly by Manager",
            notes=remarks or f"Marked as {status} by manager",
            submitted_at=now,
            status=status
        )
    flash(f"Intern task marked as {status.capitalize()}.", "success")
    return redirect(url_for("manager.tasks"))

@manager_bp.route("/tasks/create", methods=["POST"])
@manager_required
def create_task():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "Project Task").strip()
    assigned_to = request.form.get("assigned_to", "All").strip()
    due_date = request.form.get("due_date", "").strip()
    
    if not title or not due_date:
        flash("Title and Due Date are required.", "error")
        return redirect(url_for("manager.tasks"))
        
    department = g.user.get("department", "Unknown")
    
    try:
        ss.create_task(
            title=title,
            description=description,
            category=category,
            department=department,
            assigned_to=assigned_to,
            assigned_by=g.user["id"],
            due_date=due_date
        )
        flash("Task created successfully.", "success")
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        flash(f"Failed to create task: {e}", "error")
        
    return redirect(url_for("manager.tasks"))

@manager_bp.route("/tasks/<task_id>/update", methods=["POST"])
@manager_required
def update_task(task_id):
    # E.g. allowing manager to close/delete or extend due date.
    # We will implement extending due date or closing here.
    action = request.form.get("action")
    if action == "delete":
        # We don't have hard delete right now, but we can set a status if we add one to the schema.
        # Actually, let's just allow extending due date for now.
        new_date = request.form.get("due_date")
        if new_date:
            ss.update_task(task_id, due_date=new_date)
            flash("Task due date updated.", "success")
    
    return redirect(url_for("manager.tasks"))

@manager_bp.route("/performance/save", methods=["POST"])
@manager_required
def save_performance():
    intern_id = request.form.get("intern_id")
    period_start = request.form.get("period_start", "")
    period_end = request.form.get("period_end", "")
    grade_band = request.form.get("grade_band", "")
    strengths = request.form.get("strengths", "")
    areas_improvement = request.form.get("areas_improvement", "")
    overall_comments = request.form.get("overall_comments", "")
    
    # 7 metric scores
    try:
        tech = 0
        comm = 0
        disc = int(request.form.get("discipline", 0))
        task = int(request.form.get("task_completion", 0))
        init = int(request.form.get("initiative", 0))
        team = int(request.form.get("teamwork", 0))
        code = int(request.form.get("code_quality", 0))
        total_score = disc + task + init + team + code
    except ValueError:
        flash("Invalid numerical scores submitted.", "error")
        return redirect(url_for("manager.dashboard"))
        
    try:
        tc_val = request.form.get("tasks_completed", "").strip()
        ta_val = request.form.get("tasks_assigned", "").strip()
        t_comp = int(tc_val) if tc_val.isdigit() else None
        t_ass = int(ta_val) if ta_val.isdigit() else None
        
        ss.create_performance_report(
            intern_id=intern_id,
            manager_id=g.user["id"],
            period_start=period_start,
            period_end=period_end,
            technical_skill=tech,
            communication=comm,
            discipline=disc,
            task_completion=task,
            initiative=init,
            teamwork=team,
            code_quality=code,
            total_score=total_score,
            grade_band=grade_band,
            strengths=strengths,
            areas_improvement=areas_improvement,
            overall_comments=overall_comments,
            tasks_completed=t_comp,
            tasks_assigned=t_ass
        )
        
        # Send email to intern
        intern = supa.get_profile(intern_id)
        if intern and intern.get("email"):
            from services.email_service import send_performance_report_issued_notification
            month_str = f"{period_start} to {period_end}"
            send_performance_report_issued_notification(intern["email"], intern["name"], g.user["name"], month_str, request.host_url.rstrip("/"))
            
        flash("Performance report saved successfully.", "success")
    except Exception as e:
        logger.error(f"Failed to save performance report: {e}")
        flash(f"Failed to save performance: {str(e)}", "error")
        
    return redirect(url_for("manager.dashboard"))


@manager_bp.route("/api/quick_search", methods=["GET"])
@manager_required
def quick_search_api():
    """Returns assigned interns for live Quick Search in the top header."""
    try:
        interns = supa.get_profiles_by_manager(g.user["id"])
        results = []
        for i in interns:
            results.append({
                "id": str(i.get("id", "")),
                "name": str(i.get("name", "Unnamed Intern")),
                "email": str(i.get("email", "")),
                "role": str(i.get("role", "Intern")),
                "avatar": str(i.get("name", "UN")[:2]).upper(),
                "rgt_id": str(i.get("intern_id") or i.get("rgt_id") or "Not Set")
            })
        return {"status": "success", "interns": results}, 200
    except Exception as e:
        logger.error(f"Quick search error: {e}")
        return {"status": "error", "message": str(e)}, 500


@manager_bp.route("/exports/performance-report/<intern_id>")
@manager_required
def export_performance_report_pdf(intern_id):
    from flask import make_response
    from services.pdf_service import generate_internship_report_pdf
    try:
        intern_profile = supa.get_profile(intern_id)
        if not can_manage_intern(g.user, intern_profile):
            flash("Intern not found or not assigned to you.", "error")
            return redirect(url_for("manager.dashboard"))
            
        pdf_bytes = generate_internship_report_pdf(intern_profile, host_url=request.host_url)
        response = make_response(pdf_bytes)
        display_id = intern_profile.get("intern_id") or intern_profile.get("rgt_id") or f"RGTV-INT-{str(intern_profile.get('id', ''))[:4]}"
        filename = f"RGTvertex_Official_Evaluation_Report_{display_id}.pdf"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-Type"] = "application/pdf"
        return response
    except Exception as e:
        logger.error("Failed to export PDF report for intern %s: %s", intern_id, e, exc_info=True)
        flash("Could not generate PDF report. Please try again later.", "error")
        return redirect(url_for("manager.student_detail", intern_id=intern_id))


@manager_bp.route("/student/<student_id>/terminate", methods=["POST"])
@manager_required
def terminate_student(student_id):
    try:
        intern_profile = supa.get_profile(student_id)
        if not can_manage_intern(g.user, intern_profile):
            return jsonify({"success": False, "error": "Unauthorized or intern not found."}), 403
            
        reason = request.form.get("reason", "Termination requested")
        
        # Log request
        try:
            from services import sheets_service as ss
            ss.log_audit(g.user["id"], "request_termination", f"Requested termination for {student_id}: {reason}")
        except Exception as e:
            pass
            
        # Send email to Admin
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@rgtvertex.com")
        from services.email_service import _send_email, _render_branded_email
        subject = f"Termination Request: {intern_profile.get('name', 'Unknown')}"
        html_content = _render_branded_email(subject, f"<p>Manager {g.user['name']} has requested termination for intern {intern_profile.get('name')} ({student_id}).</p><p>Reason: {reason}</p>")
        _send_email(admin_email, subject, "Termination requested.", html_content)
            
        logger.info("AUDIT: Manager %s requested termination for intern %s (Reason: %s)", g.user["id"], student_id, reason)
        return jsonify({"success": True, "message": "Termination request sent to administrator."})
    except Exception as e:
        logger.error("Failed to request termination for intern %s: %s", student_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@manager_bp.route("/export/csv")
@manager_required
def export_csv():
    import csv
    import io
    
    interns = supa.get_profiles_by_manager(g.user["id"])
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "RGT ID", "Name", "Email", "Department", "Joining Date", 
        "Duration (Months)", "Attendance %", "Active Warnings", "Status"
    ])
    
    for intern in interns:
        rgt_id = intern.get("intern_id") or intern.get("rgt_id") or "N/A"
        name = intern.get("name", "Unknown")
        email = intern.get("email", "N/A")
        dept = intern.get("department", "N/A")
        joining_date = intern.get("joining_date", "Not Set")
        duration = intern.get("internship_duration_months", "0")
        
        att_summary = get_student_attendance_summary(intern["id"])
        att_pct = att_summary.get("attendance_percent", 0) if att_summary else 0
        
        warnings = ss.get_warnings_for_student(intern["id"])
        active_warnings = len([w for w in warnings if w.get("status") == "active"])
        
        status = intern.get("status", "active").capitalize()
        
        writer.writerow([rgt_id, name, email, dept, joining_date, duration, f"{att_pct}%", active_warnings, status])
        
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=RGTVertex_Team_Export_{date_str}.csv"
    return response
