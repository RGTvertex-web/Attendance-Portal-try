"""
routes/intern.py — Intern routes + daily report API + weekly/monthly reports + leave
"""
import logging
import json
from datetime import datetime, timezone
import pytz

from flask import Blueprint, flash, redirect, render_template, request, url_for, g, make_response
from extensions import limiter
from services import sheets_service as ss
from services.attendance_service import get_student_attendance_summary, get_attendance_trend_for_target
from services.auth_helpers import intern_required

intern_bp = Blueprint("intern", __name__)
logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

@intern_bp.route("/dashboard")
@intern_required
def dashboard():
    from services.internship_cycle_service import get_internship_cycle
    
    cycle_data = get_internship_cycle(
        g.user.get("joining_date"),
        g.user.get("internship_duration_months"),
        g.user.get("created_at")
    )
    
    # Fetch intern's submissions
    att_summary = get_student_attendance_summary(g.user["id"])
    trend_data = get_attendance_trend_for_target({g.user["id"]})
    warnings = ss.get_warnings_for_student(g.user["id"])
    submissions = ss.get_submissions_for_student(g.user["id"])
    
    # Force fresh performance data — invalidate before fetching so dashboard shows newly submitted reports
    from services.cache_service import global_cache
    global_cache.invalidate("Performance")
    perf_reports = ss.get_performance_reports_for_student(g.user["id"])
    logger.info(
        "DASH: Intern %s (id=%r) loaded dashboard with %d performance reports",
        g.user.get("name"), g.user.get("id"), len(perf_reports)
    )
    
    # Parse JSON notes if possible
    for sub in submissions:
        try:
            sub["report_data"] = json.loads(sub.get("notes", "{}"))
        except:
            sub["report_data"] = {"given": "Unknown", "done": sub.get("notes", ""), "remaining": "Unknown"}
            
    # Sort submissions by date descending
    submissions.sort(key=lambda s: s.get("submitted_at", ""), reverse=True)
    perf_reports.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    
    # Manager contact info
    from services import supabase_service as supa
    manager_profile = None
    if g.user.get("manager_id"):
        manager_profile = supa.get_user_by_id(g.user["manager_id"])
        
    # Check for today's report to allow editing
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    virtual_task_id = f"REPORT-{today_str}"
    today_report = next((s for s in submissions if str(s.get("task_id")) == virtual_task_id), None)

    # Fetch Announcements
    announcements = ss.get_all_announcements()
    
    return render_template("intern/dashboard.html",
                           att_summary=att_summary,
                           warnings=warnings,
                           submissions=submissions,
                           perf_reports=perf_reports,
                           trend=trend_data,
                           cycle_data=cycle_data,
                           manager_profile=manager_profile,
                           today_report=today_report,
                           announcements=announcements)

@intern_bp.route("/attendance")
@intern_required
def attendance():
    import calendar
    from datetime import date
    
    today = date.today()
    year = today.year
    month = today.month
    
    # Get all attendance records for this intern
    all_att = ss.get_all_attendance()
    my_att = [a for a in all_att if str(a.get("intern_id")) == str(g.user["id"])]
    
    # Map dates to status for quick lookup
    # Format: YYYY-MM-DD -> status
    att_map = {}
    for a in my_att:
        att_map[a.get("date")] = a.get("status", "Present")
        
    cal = calendar.Calendar(firstweekday=0) # Monday first
    month_days = cal.monthdatescalendar(year, month)
    
    # Pass summary too if we want
    att_summary = get_student_attendance_summary(g.user["id"])
    
    return render_template("intern/attendance.html",
                           month_days=month_days,
                           att_map=att_map,
                           current_month_name=calendar.month_name[month],
                           current_year=year,
                           today=today,
                           att_summary=att_summary)

@intern_bp.route("/api/submit-report", methods=["POST"])
@intern_required
@limiter.limit("10 per hour")
def submit_report():
    task_given = request.form.get("task_given", "").strip()
    task_done = request.form.get("task_done", "").strip()
    task_remaining = request.form.get("task_remaining", "").strip()
    source_page = request.form.get("source_page", "dashboard").strip()
    redirect_target = "intern.reports" if source_page == "reports" else "intern.dashboard"

    if not task_given or not task_done or not task_remaining:
        flash("Please fill in what task was given, what is done, and what is remaining.", "error")
        return redirect(url_for(redirect_target))

    report_data = {
        "given": task_given[:1000],
        "done": task_done[:2000],
        "remaining": task_remaining[:2000]
    }
    notes_json = json.dumps(report_data)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    virtual_task_id = f"REPORT-{date_str}"
    submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Check if a report for today already exists
    existing_submissions = ss.get_submissions_for_student(g.user["id"])
    today_report = next((s for s in existing_submissions if str(s.get("task_id")) == virtual_task_id), None)
    
    if today_report:
        ss.update_submission(today_report["submission_id"], content_link="", notes=notes_json, status="submitted")
        logger.info("AUDIT: Intern %s updated daily report for %s", g.user["id"], date_str)
        flash("Daily report updated successfully! ✅", "success")
    else:
        ss.create_submission(virtual_task_id, g.user["id"], "", notes_json, submitted_at, "submitted")
        logger.info("AUDIT: Intern %s submitted daily report for %s", g.user["id"], date_str)
        flash("Daily report submitted successfully! ✅", "success")
        
    return redirect(url_for(redirect_target))

@intern_bp.route("/extension/request", methods=["GET", "POST"])
@intern_required
def extension_request():
    from services.internship_cycle_service import get_internship_cycle
    from services.email_service import send_extension_request_notification
    from services import supabase_service as supa
    
    cycle_data = get_internship_cycle(
        g.user.get("joining_date"),
        g.user.get("internship_duration_months"),
        g.user.get("created_at")
    )
    
    if request.method == "POST":
        requested_months = request.form.get("requested_months", "").strip()
        reason = request.form.get("reason", "").strip()
        
        if not requested_months or not requested_months.isdigit() or int(requested_months) < 1:
            flash("Please enter a valid number of months for the extension.", "error")
            return redirect(url_for("intern.extension_request"))
            
        if not reason:
            flash("Please provide a reason for the extension request.", "error")
            return redirect(url_for("intern.extension_request"))
            
        manager_id = g.user.get("manager_id")
        
        # Save to Sheets
        try:
            ss.create_extension_request(
                intern_id=g.user["id"],
                manager_id=manager_id,
                current_duration=cycle_data["duration_months"],
                requested_months=requested_months,
                reason=reason
            )
        except RuntimeError as e:
            flash(str(e), "error")
            return redirect(url_for("intern.extension_request"))
        
        # Notify all department managers
        department = g.user.get("department")
        if department:
            managers = supa.get_managers_by_department(department)
            intern_display = f"{g.user['name']} ({g.user['intern_id']})" if g.user.get("intern_id") else g.user.get("name")
            for mgr in managers:
                mgr_email = mgr.get("email")
                if mgr_email:
                    try:
                        send_extension_request_notification(
                            mgr_email,
                            mgr.get("name", "Manager"),
                            intern_display,
                            requested_months,
                            reason,
                            request.host_url.rstrip('/')
                        )
                    except Exception as e:
                        logger.error(f"Error sending extension request email to {mgr_email}: {e}")
        else:
            logger.warning(f"Intern {g.user['id']} has no department set; no extension emails sent.")
            
        flash("Extension request submitted successfully! It is now pending manager approval.", "success")
        return redirect(url_for("intern.dashboard"))
        
    return render_template("intern/extension_request.html", cycle_data=cycle_data)


@intern_bp.route("/leave", methods=["GET", "POST"])
@intern_required
def leave():
    from services.email_service import send_leave_request_notification
    from services import supabase_service as supa
    
    # Calculate leave balance
    all_leaves = ss.get_leaves_for_student(g.user["id"])
    approved_leaves = [l for l in all_leaves if l["status"] == "approved"]
    
    total_allotted = g.user.get("leave_allotted_days", 0)
    
    used_days = 0
    for l in approved_leaves:
        try:
            used_days += int(l.get("days_requested", 0))
        except ValueError:
            pass
            
    current_month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    days_taken_this_month = 0
    for l in approved_leaves:
        if (l.get("start_date") or "").startswith(current_month_prefix):
            try:
                days_taken_this_month += int(l.get("days_requested", 0))
            except ValueError:
                pass
                
    remaining_days = max(0, total_allotted - used_days)
    
    if request.method == "POST":
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        reason = request.form.get("reason", "").strip()
        leave_category = request.form.get("leave_category", "").strip()
        
        if not start_date or not end_date or not reason or not leave_category:
            flash("All fields are required.", "error")
            return redirect(url_for("intern.leave"))
            
        manager_id = g.user.get("manager_id")
        if not manager_id:
            flash("You do not have a manager assigned.", "error")
            return redirect(url_for("intern.leave"))
            
        # Calculate days requested
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            ed = datetime.strptime(end_date, "%Y-%m-%d")
            days_requested = (ed - sd).days + 1
            if days_requested <= 0:
                flash("End date must be on or after start date.", "error")
                return redirect(url_for("intern.leave"))
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for("intern.leave"))
            
        if days_requested > remaining_days:
            flash(f"Warning: Requesting {days_requested} days exceeds your remaining balance of {remaining_days}. Your manager may reject this.", "warning")
            
        # Create leave
        leave_record = ss.create_leave_request(g.user["id"], g.user.get("department", "Unknown"), manager_id, start_date, end_date, days_requested, reason, leave_category)
        logger.info("AUDIT: Intern %s requested leave (%s) from %s to %s", g.user["id"], leave_category, start_date, end_date)
        
        # Notify all department managers
        host_url = request.host_url.rstrip("/")
        department = g.user.get("department")
        
        if department:
            managers = supa.get_managers_by_department(department)
            intern_display = f"{g.user['name']} ({g.user['intern_id']})" if g.user.get("intern_id") else g.user.get("name")
            
            for mgr in managers:
                mgr_email = mgr.get("email")
                if mgr_email:
                    try:
                        success = send_leave_request_notification(
                            mgr_email, 
                            intern_display, 
                            leave_record["leave_id"], 
                            start_date, 
                            end_date, 
                            reason, 
                            host_url
                        )
                        if not success:
                            logger.error(f"Failed to send leave request email to {mgr_email}")
                    except Exception as e:
                        logger.error(f"Exception sending leave request email to {mgr_email}: {e}")
        else:
            logger.warning(f"Intern {g.user['id']} has no department set; no leave emails sent.")
            
        flash("Leave request submitted successfully.", "success")
        return redirect(url_for("intern.leave"))
        
    all_leaves.sort(key=lambda l: l.get("start_date", ""), reverse=True)
    return render_template("intern/leave.html", leaves=all_leaves, total_allotted=total_allotted, used_days=used_days, remaining_days=remaining_days, days_taken_this_month=days_taken_this_month)

@intern_bp.route("/leave/<leave_id>/withdraw", methods=["POST"])
@intern_required
def withdraw_leave(leave_id):
    from services.sheets_service import get_leaves_for_student, update_leave
    all_leaves = get_leaves_for_student(g.user["id"])
    leave = next((l for l in all_leaves if str(l["leave_id"]) == str(leave_id)), None)
    
    if not leave:
        flash("Leave request not found.", "error")
        return redirect(url_for("intern.leave"))
        
    if leave["status"] != "pending":
        flash("You can only withdraw pending leave requests.", "error")
        return redirect(url_for("intern.leave"))
        
    try:
        update_leave(leave_id, "withdrawn", g.user["id"], "Withdrawn by intern.")
        logger.info("AUDIT: Intern %s withdrew leave request %s", g.user["id"], leave_id)
        flash("Leave request withdrawn successfully.", "success")
    except Exception as e:
        logger.error("Failed to withdraw leave: %s", e)
        flash("Error withdrawing leave request.", "error")
        
    return redirect(url_for("intern.leave"))


@intern_bp.route("/reports")
@intern_required
def reports():
    search_query = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 10

    submissions = ss.get_submissions_for_student(g.user["id"])
    
    # Parse JSON notes
    for sub in submissions:
        try:
            sub["report_data"] = json.loads(sub.get("notes", "{}"))
        except:
            sub["report_data"] = {"given": "Unknown", "done": sub.get("notes", ""), "remaining": "Unknown"}
            
    submissions.sort(key=lambda s: s.get("submitted_at", ""), reverse=True)
    
    if search_query:
        submissions = [s for s in submissions if search_query in (s["report_data"].get("done") or "").lower() or search_query in (s["report_data"].get("given") or "").lower() or search_query in (s["report_data"].get("remaining") or "").lower() or search_query in (s.get("submitted_at") or "")]
        
    total = len(submissions)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_submissions = submissions[start:end]
    
    # Check for today's report
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    virtual_task_id = f"REPORT-{today_str}"
    today_report = next((s for s in submissions if str(s.get("task_id")) == virtual_task_id), None)

    return render_template("intern/reports.html", submissions=paginated_submissions, today_report=today_report, search=request.args.get("search", ""), page=page, total_pages=total_pages)

@intern_bp.route("/performance")
@intern_required
def performance():
    import traceback
    try:
        # Force fresh read to catch newly submitted reports
        from services.cache_service import global_cache
        global_cache.invalidate("Performance")
        
        user_id = g.user.get("id") if g.user else None
        if not user_id:
            logger.error("PERF: No user ID found in g.user context: %r", g.user)
            flash("User session invalid. Please log in again.", "error")
            return redirect(url_for("auth.login"))
            
        reports = ss.get_performance_reports_for_student(user_id) or []
        logger.info(
            "PERF: Intern %s (id=%r) fetched %d performance reports",
            g.user.get("name"), user_id, len(reports)
        )
        
        # Defensive sorting (handles None values safely)
        reports.sort(
            key=lambda r: (r.get("submitted_at") or "") if isinstance(r, dict) else "",
            reverse=True
        )
        
        from services.internship_cycle_service import get_internship_cycle
        j_date = g.user.get("joining_date")
        try:
            duration = int(g.user.get("internship_duration_months", 3))
        except (ValueError, TypeError):
            duration = 3
        
        cycle_info = get_internship_cycle(j_date, duration, g.user.get("created_at")) or {}
        
        perf_by_month = {}
        for r in reports:
            if isinstance(r, dict):
                k = str(r.get("period_start") or "")[:7]
                if k:
                    perf_by_month[k] = r
            
        carousel_slides = []
        all_cycles = cycle_info.get("all_cycles", []) if isinstance(cycle_info, dict) else []
        for cyc in all_cycles:
            if isinstance(cyc, dict):
                k = str(cyc.get("start") or "")[:7]
                r = perf_by_month.get(k)
                carousel_slides.append({
                    "cycle": cyc,
                    "report": r
                })
            
        return render_template("intern/performance.html", reports=reports, carousel_slides=carousel_slides)

    except Exception as e:
        err_tb = traceback.format_exc()
        logger.error(
            "UNHANDLED EXCEPTION in /intern/performance route for user %r:\nError: %s\nTraceback:\n%s",
            g.user.get("id") if g.user else "Unknown", e, err_tb
        )
        flash(f"An error occurred loading your performance report: {str(e)}", "error")
        try:
            return render_template("intern/performance.html", reports=[], carousel_slides=[])
        except Exception as render_err:
            logger.error("Failed to render fallback performance.html: %s", render_err)
            return redirect(url_for("intern.dashboard"))

@intern_bp.route("/performance/acknowledge/<report_id>", methods=["POST"])
@intern_required
def acknowledge_performance(report_id):
    report = next((r for r in ss.get_performance_reports_for_student(g.user["id"]) if r["report_id"] == report_id), None)
    if not report:
        flash("Report not found.", "error")
        return redirect(url_for("intern.performance"))
        
    updates = {
        "intern_acknowledged": "True",
        "intern_ack_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    ss.update_performance_report(report_id, updates)
    
    # Send email to manager
    manager_id = report.get("manager_id")
    if manager_id:
        mgr = supa.get_profile(manager_id)
        if mgr and mgr.get("email"):
            from services.email_service import send_performance_acknowledged_notification
            month_str = f"{report.get('period_start')} to {report.get('period_end')}"
            send_performance_acknowledged_notification(mgr["email"], mgr["name"], g.user["name"], month_str, request.host_url.rstrip("/"))
            
    flash("Report acknowledged.", "success")
    return redirect(url_for("intern.performance"))

@intern_bp.route("/performance-report/pdf")
@intern_required
def download_performance_report_pdf():
    from services.pdf_service import generate_internship_report_pdf
    try:
        pdf_bytes = generate_internship_report_pdf(g.user, host_url=request.host_url)
        response = make_response(pdf_bytes)
        intern_id = g.user.get("intern_id") or g.user.get("rgt_id") or f"RGTV-INT-{str(g.user.get('id', ''))[:4]}"
        filename = f"RGTvertex_Official_Evaluation_Report_{intern_id}.pdf"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-Type"] = "application/pdf"
        return response
    except Exception as e:
        logger.error(
            "Failed to generate PDF report for intern %s: %s",
            g.user.get("id"), e, exc_info=True
        )
        flash(f"Could not generate PDF report. Error: {str(e)}", "error")
        return redirect(url_for("intern.performance"))
