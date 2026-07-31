

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytz

from config import get_at_risk_settings, UTC
from services import sheets_service as ss
from services import supabase_service as supa

logger = logging.getLogger(__name__)


def evaluate_attendance_for_date(target_date: Optional[datetime] = None) -> Dict:
    """
    Main daily job. Evaluates all active tasks whose due_date falls on target_date
    (UTC date), marks attendance for each assigned student, creates warnings for
    absences, and checks At-Risk status.

    Returns a summary dict with counts.
    """
    if target_date is None:
        target_date = datetime.now(UTC)

    date_str = target_date.strftime("%Y-%m-%d")
    logger.info("=== Attendance evaluation for %s ===", date_str)

    all_tasks = ss.get_all_tasks()
    all_profiles = supa.get_all_profiles()
    active_students = {u["id"]: u for u in all_profiles
                       if u.get("role") == "intern" and u.get("status") in ("active", "at_risk")}

    summary = {"date": date_str, "evaluated": 0, "present": 0,
               "present_late": 0, "absent": 0, "warnings_created": 0, "at_risk_flagged": 0}

    all_leaves = ss.get_all_leaves()
    weekday = target_date.weekday()

    all_holidays = ss.get_all_holidays()
    holiday_names = {h["date"]: h["name"] for h in all_holidays}
    if date_str in holiday_names:
        holiday_name = holiday_names[date_str]
        logger.info("Holiday detected: %s. Auto-marking all active interns as on leave.", holiday_name)
        for student_id, student in active_students.items():
            ss.upsert_attendance(student_id, student.get("department", "Unknown"), date_str, "on_leave", "General", "", notes=f"holiday: {holiday_name}")
            summary["evaluated"] += 1
        return summary

    if weekday == 6:  # Sunday
        logger.info("Sunday detected. Auto-marking all active interns as on leave.")
        for student_id, student in active_students.items():
            ss.upsert_attendance(student_id, student.get("department", "Unknown"), date_str, "on_leave", "General", "", notes="weekend")
            summary["evaluated"] += 1
        return summary

    if weekday == 5:  # Saturday
        logger.info("Saturday detected. Auto-marking all active interns as on leave (will evaluate reports separately).")
        for student_id, student in active_students.items():
            ss.upsert_attendance(student_id, student.get("department", "Unknown"), date_str, "on_leave", "General", "", notes="weekend")

    due_today = []
    for task in all_tasks:
        due_raw = task.get("due_date", "")
        if not due_raw:
            continue
        try:
            if "T" in due_raw:
                due_dt = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            else:
                due_dt = datetime.strptime(due_raw, "%Y-%m-%d").replace(tzinfo=UTC)
            if due_dt.strftime("%Y-%m-%d") == date_str:
                due_today.append((task, due_dt))
        except ValueError:
            logger.warning("Could not parse due_date '%s' for task %s", due_raw, task["task_id"])

    if not due_today:
        logger.info("No tasks due on %s — nothing to evaluate", date_str)
        return summary

    for task, due_dt in due_today:
        task_id = task["task_id"]
        category = task.get("category", "General")
        assigned_to = task.get("assigned_to", "")

        if assigned_to == "all":
            target_students = list(active_students.values())
        elif assigned_to in active_students:
            target_students = [active_students[assigned_to]]
        else:
            target_students = [s for s in active_students.values()
                                if s.get("manager_id") == assigned_to]

        for student in target_students:
            student_id = student["id"]
            submission = ss.get_submission(task_id, student_id)
            if weekday != 5:
                summary["evaluated"] += 1

            if submission:
                submitted_at_raw = submission.get("submitted_at", "")
                try:
                    submitted_dt = datetime.fromisoformat(submitted_at_raw.replace("Z", "+00:00"))
                except ValueError:
                    submitted_dt = None

                if submitted_dt and submitted_dt <= due_dt:
                    att_status = "present"
                    sub_status = "submitted"
                    if weekday != 5:
                        summary["present"] += 1
                else:
                    att_status = "present_late"
                    sub_status = "late"
                    if weekday != 5:
                        summary["present_late"] += 1

                ss.update_submission(submission["submission_id"], status=sub_status)
            else:
                if weekday == 5:
                    # Saturday specific warning
                    reason = f"Missing Saturday report for task '{task['title']}' (category: {category}) due {date_str}"
                    ss.create_warning(student_id, date_str, reason, issued_by="system")
                    summary["warnings_created"] += 1
                    att_status = "on_leave"
                else:
                    is_on_leave = any(
                        l["intern_id"] == student_id and 
                        l["status"] == "approved" and 
                        l["start_date"] <= date_str <= l["end_date"]
                        for l in all_leaves
                    )
                    
                    if is_on_leave:
                        att_status = "on_leave"
                    else:
                        att_status = "absent"
                        summary["absent"] += 1
                        reason = f"No submission for task '{task['title']}' (category: {category}) due {date_str}"
                        ss.create_warning(student_id, date_str, reason, issued_by="system")
                        summary["warnings_created"] += 1

            if weekday != 5:
                ss.upsert_attendance(
                    student_id=student_id,
                    date_str=date_str,
                    status=att_status,
                    category=category,
                    linked_task_id=task_id,
                )

                if _check_and_flag_at_risk(student_id, category):
                    summary["at_risk_flagged"] += 1

    logger.info("Evaluation complete: %s", summary)
    return summary


def _check_and_flag_at_risk(student_id: str, category: str) -> bool:
    attendance = [
        a for a in ss.get_attendance_for_student(student_id)
        if a.get("category") == category
    ]

    try:
        attendance.sort(key=lambda x: x["date"], reverse=True)
    except Exception:
        return False

    if not attendance:
        return False

    consecutive_threshold, rolling_window, rolling_count = get_at_risk_settings()

    consecutive = 0
    for record in attendance:
        if record["status"] == "absent":
            consecutive += 1
            if consecutive >= consecutive_threshold:
                logger.warning("AUDIT: Student %s flagged At-Risk (consecutive) in %s", student_id, category)
                supa.update_profile(student_id, status="at_risk")
                return True
        elif record["status"] == "on_leave":
            continue
        else:
            break

    cutoff = (datetime.now(UTC) - timedelta(days=rolling_window)).strftime("%Y-%m-%d")
    recent_absences = sum(
        1 for a in attendance
        if a["status"] == "absent" and a["date"] >= cutoff
    )
    if recent_absences >= rolling_count:
        logger.warning("AUDIT: Student %s flagged At-Risk (rolling) in %s", student_id, category)
        supa.update_profile(student_id, status="at_risk")
        return True

    return False


def get_attendance_percentage(student_id: str) -> float:
    from services.internship_cycle_service import get_internship_cycle
    student = supa.get_profile(student_id)
    if not student:
        return 0.0
    
    cycle = get_internship_cycle(student.get("joining_date"), student.get("internship_duration_months"), student.get("created_at"))
    cycle_start = cycle["current_cycle_start"]
    cycle_end = cycle["current_cycle_end"]
    
    records = [a for a in ss.get_attendance_for_student(student_id) if cycle_start <= a["date"] <= cycle_end]
    evaluable_records = [a for a in records if a["status"] != "on_leave"]
    if not evaluable_records:
        return 0.0
    present_count = sum(1 for a in evaluable_records if a["status"] in ("present", "present_late"))
    return round((present_count / len(evaluable_records)) * 100, 1)


def get_student_attendance_summary(student_id: str) -> Dict:
    from services.internship_cycle_service import get_internship_cycle
    student = supa.get_profile(student_id)
    cycle_start = "1970-01-01"
    cycle_end = "2099-12-31"
    
    if student:
        cycle = get_internship_cycle(student.get("joining_date"), student.get("internship_duration_months"), student.get("created_at"))
        cycle_start = cycle["current_cycle_start"]
        cycle_end = cycle["current_cycle_end"]

    all_records = ss.get_attendance_for_student(student_id)
    records = [a for a in all_records if cycle_start <= a["date"] <= cycle_end]
    
    total = len(records)
    present = sum(1 for a in records if a["status"] == "present")
    late = sum(1 for a in records if a["status"] == "present_late")
    absent = sum(1 for a in records if a["status"] == "absent")
    on_leave = sum(1 for a in records if a["status"] == "on_leave")
    percentage = get_attendance_percentage(student_id)
    
    return {
        "total": total, "present": present, "late": late,
        "absent": absent, "on_leave": on_leave, "percentage": percentage,
        "records": sorted(all_records, key=lambda x: x["date"], reverse=True),
        "cycle_records": sorted(records, key=lambda x: x["date"], reverse=True)
    }


def get_org_summary(department: Optional[str] = None) -> Dict:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    all_att = ss.get_all_attendance()
    
    all_profiles = supa.get_all_profiles()
    if department:
        all_profiles = [u for u in all_profiles if u.get("department") == department]
        
    student_ids = {u["id"] for u in all_profiles if u.get("role") == "intern"}
    today_records = [a for a in all_att if a["date"] == today and a.get("intern_id") in student_ids]

    total_students = len([u for u in all_profiles if u.get("role") == "intern" and u.get("status") != "inactive"])
    
    warnings_list = [w for w in ss.get_all_warnings() if w.get("intern_id") in student_ids]
    
    at_risk = []
    for u in all_profiles:
        if u.get("role") != "intern" or u.get("status") == "inactive": continue
        w_count = len([w for w in warnings_list if w.get("intern_id") == u["id"] and w.get("status") == "active"])
        pct = get_attendance_percentage(u["id"])
        if u.get("status") == "at_risk" or w_count > 0 or (pct is not None and pct < 80):
            at_risk.append(u)
    warnings = [w for w in ss.get_all_warnings() if w.get("intern_id") in student_ids]

    today_present = sum(1 for a in today_records if a["status"] in ("present", "present_late"))
    today_absent = sum(1 for a in today_records if a["status"] == "absent")
    today_on_leave = sum(1 for a in today_records if a["status"] == "on_leave")

    return {
        "total_students": total_students,
        "today_present": today_present,
        "today_absent": today_absent,
        "today_on_leave": today_on_leave,
        "at_risk_students": at_risk,
        "total_warnings": len(warnings),
        "unack_warnings": len([w for w in ss.get_unacknowledged_warnings() if w.get("intern_id") in student_ids]),
    }


def get_manager_summary(manager_id: str) -> Dict:
    students = supa.get_profiles_by_manager(manager_id)
    student_ids = {s["id"] for s in students}
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    today_att = {a["intern_id"]: a["status"]
                 for a in ss.get_attendance_for_date(today)
                 if a["intern_id"] in student_ids}

    at_risk = []
    for s in students:
        warnings_count = len([w for w in ss.get_warnings_for_student(s["id"]) if w.get("status") == "active"])
        pct = get_attendance_percentage(s["id"])
        if s.get("status") == "at_risk" or warnings_count > 0 or (pct is not None and pct < 80):
            at_risk.append(s)
    pending = ss.get_pending_submissions_for_manager(manager_id, student_ids)
    
    # Get pending leaves
    manager_leaves = ss.get_leaves_for_manager(manager_id)
    pending_leaves = [l for l in manager_leaves if l.get("status") == "pending"]

    student_summaries = []
    for s in students:
        student_summaries.append({
            **s,
            "attendance_pct": get_attendance_percentage(s["id"]),
            "today_status": today_att.get(s["id"], "—"),
            "warnings": len(ss.get_warnings_for_student(s["id"])),
        })

    return {
        "students": student_summaries,
        "at_risk": at_risk,
        "pending_tasks": pending,
        "pending_leaves": pending_leaves,
        "total_students": len(students),
    }

def get_attendance_trend_for_target(student_ids: Optional[set] = None, days: int = 7) -> Dict:
    """
    Returns a dict with 'labels' (e.g. ['Mon', 'Tue', ...]) and 'values' (percentages).
    If student_ids is None, calculates for all interns.
    """
    labels = []
    values = []
    today = datetime.now(UTC).date()
    
    # Pre-fetch all attendance to avoid multiple calls
    all_att = ss.get_all_attendance()
    
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        labels.append(d.strftime("%a"))  # e.g. 'Mon'
        
        # Filter for this date
        day_records = [a for a in all_att if a["date"] == d_str]
        if student_ids is not None:
            day_records = [a for a in day_records if a["intern_id"] in student_ids]
            
        if not day_records:
            values.append(0)
            continue
            
        present = sum(1 for a in day_records if a["status"].lower() in ("present", "present_late", "present today"))
        pct = round((present / len(day_records)) * 100, 1)
        values.append(pct)
        
    return {"labels": labels, "values": values}

def get_org_performance_analytics(department: Optional[str] = None) -> Dict:
    """
    Calculates overall performance score, grade band distribution,
    and department-wise averages. Optionally filtered by department.
    """
    try:
        reports = ss.get_all_performance_reports()
        
        # Fetch profiles for department mapping
        all_users = supa.get_all_profiles()
        intern_dept_map = {u["id"]: u.get("department", "Unknown") for u in all_users}
        manager_name_map = {u["id"]: u.get("name", "Unknown") for u in all_users if u.get("role") in ("manager", "admin")}
        
        if department:
            reports = [r for r in reports if intern_dept_map.get(r.get("intern_id")) == department]
        
        if not reports:
            return {
                "average_score": 0,
                "grade_distribution": {"Outstanding": 0, "Excellent": 0, "Good": 0, "Satisfactory": 0, "Needs Improvement": 0},
                "department_averages": {},
                "manager_leaderboard": [],
                "total_reports": 0
            }
            
        total_score = 0
        grade_distribution = {"Outstanding": 0, "Excellent": 0, "Good": 0, "Satisfactory": 0, "Needs Improvement": 0}
        dept_scores = {}
        manager_scores = {}
        
        for r in reports:
            try:
                score = float(r.get("total_score", 0))
                total_score += score
                
                # Grade band distribution
                grade = r.get("grade_band", "Needs Improvement")
                if grade in grade_distribution:
                    grade_distribution[grade] += 1
                else:
                    grade_distribution["Needs Improvement"] += 1
                    
                # Department averages
                intern_id = r.get("intern_id")
                dept = intern_dept_map.get(intern_id, "Unknown")
                if dept not in dept_scores:
                    dept_scores[dept] = {"total": 0, "count": 0}
                dept_scores[dept]["total"] += score
                dept_scores[dept]["count"] += 1
                
                
                # Manager averages
                manager_id = r.get("manager_id")
                if manager_id:
                    if manager_id not in manager_scores:
                        manager_scores[manager_id] = {"total": 0, "count": 0}
                    manager_scores[manager_id]["total"] += score
                    manager_scores[manager_id]["count"] += 1
                    
            except ValueError:
                pass
                
        avg_score = round(total_score / len(reports), 1)
        
        dept_averages = {
            dept: round(data["total"] / data["count"], 1)
            for dept, data in dept_scores.items()
        }
        
        manager_averages = []
        for m_id, data in manager_scores.items():
            manager_averages.append({
                "name": manager_name_map.get(m_id, "Unknown Manager"),
                "score": round(data["total"] / data["count"], 1),
                "reports": data["count"]
            })
            
        manager_averages = sorted(manager_averages, key=lambda x: x["score"], reverse=True)
        
        return {
            "average_score": avg_score,
            "grade_distribution": grade_distribution,
            "department_averages": dept_averages,
            "manager_leaderboard": manager_averages,
            "total_reports": len(reports)
        }
    except Exception as e:
        logging.getLogger(__name__).error("get_org_performance_analytics failed: %s", e)
        return {
            "average_score": 0,
            "grade_distribution": {"Outstanding": 0, "Excellent": 0, "Good": 0, "Satisfactory": 0, "Needs Improvement": 0},
            "department_averages": {},
            "manager_leaderboard": [],
            "total_reports": 0
        }

def check_and_send_absence_warnings():
    
    from services.email_service import send_absence_warning_notification
    
    logger.info("=== Running check_and_send_absence_warnings ===")
    
    # 1. Fetch interns
    all_profiles = supa.get_all_profiles()
    active_interns = [u for u in all_profiles if u.get("role") == "intern" and u.get("status") in ("active", "at_risk")]
    
    if not active_interns:
        logger.info("No active interns found.")
        return
        
    managers = {u["id"]: u for u in all_profiles if u.get("role") == "manager"}
    
    # 2. Fetch attendance, leaves, and warnings
    all_attendance = ss.get_all_attendance()
    all_leaves = ss.get_all_leaves()
    all_warnings = ss.get_all_warnings()
    
    # 3. Determine the last 3 days
    today = datetime.now(UTC).date()
    target_dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
    
    warnings_sent = 0
    
    for intern in active_interns:
        intern_id = intern["id"]
        
        # Check if absent for all 3 target dates
        consecutive_absent = 0
        for d_str in target_dates:
            # Check attendance records for this date
            day_records = [a for a in all_attendance if a["intern_id"] == intern_id and a["date"] == d_str]
            has_present = any(a["status"] in ("present", "present_late") for a in day_records)
            
            # Check leaves for this date
            has_leave = any(
                l["intern_id"] == intern_id and 
                l["status"] == "approved" and 
                l["start_date"] <= d_str <= l["end_date"]
                for l in all_leaves
            )
            
            if not has_present and not has_leave:
                consecutive_absent += 1
                
        if consecutive_absent == 3:
            # Check if warning was already sent for this streak
            intern_warnings = [w for w in all_warnings if w["intern_id"] == intern_id and "[ABSENCE_WARNING_EMAIL]" in w.get("reason", "")]
            
            should_send = True
            if intern_warnings:
                # Sort warnings by date descending to get the latest
                intern_warnings.sort(key=lambda x: x["date"], reverse=True)
                latest_warning = intern_warnings[0]
                warning_date = latest_warning["date"]
                
                # Check if there are any present/leave records AFTER the warning date
                # If so, the streak was broken and this is a NEW streak.
                # If not, it's the SAME streak.
                recent_presents = [
                    a for a in all_attendance 
                    if a["intern_id"] == intern_id 
                    and a["status"] in ("present", "present_late") 
                    and a["date"] > warning_date
                ]
                
                recent_leaves = [
                    l for l in all_leaves 
                    if l["intern_id"] == intern_id 
                    and l["status"] == "approved" 
                    and l["end_date"] > warning_date
                ]
                
                if not recent_presents and not recent_leaves:
                    # Still on the same streak, do not resend
                    should_send = False
                    
            if should_send:
                manager_id = intern.get("manager_id")
                manager = managers.get(manager_id)
                manager_name = manager["name"] if manager else "Admin"
                department = intern.get("department", "Unknown")
                
                logger.info(f"Triggering absence warning email for intern {intern['name']} ({intern_id})")
                
                try:
                    # Log the warning first
                    ss.create_warning(
                        intern_id=intern_id,
                        date_str=today.strftime("%Y-%m-%d"),
                        reason="[ABSENCE_WARNING_EMAIL] 3 consecutive days unapproved absence",
                        issued_by="system"
                    )
                    
                    # Only send email if logging succeeded
                    send_absence_warning_notification(
                        target_email=intern["email"],
                        student_name=intern["name"],
                        department=department,
                        days_absent=3,
                        manager_name=manager_name,
                        host_url=os.environ.get("HOST_URL", "https://attendance-portal-theta-bice.vercel.app")
                    )
                    
                    warnings_sent += 1
                except Exception as e:
                    logger.error(f"Failed to process absence warning for {intern_id}: {str(e)}")
                    
    logger.info(f"=== check_and_send_absence_warnings completed: {warnings_sent} warnings sent ===")


def check_and_send_daily_report_reminders():
    """
    Called at 10:30 PM (IST) to check if active interns have submitted 
    their daily reports. If not, send a reminder email.
    """
    logger.info("=== check_and_send_daily_report_reminders starting ===")
    
    import pytz
    from config import IST
    import os
    from services.email_service import send_daily_report_reminder_email
    
    now_ist = datetime.now(UTC).astimezone(IST)
    date_str = now_ist.strftime("%Y-%m-%d")
    
    # We only care about working days (Mon-Fri) for reminders
    if now_ist.weekday() >= 5:
        logger.info("Weekend detected. Skipping daily report reminders.")
        return
        
    # Check if today is a holiday
    all_holidays = ss.get_all_holidays()
    holiday_names = {h["date"]: h["name"] for h in all_holidays}
    if date_str in holiday_names:
        logger.info(f"Holiday ({holiday_names[date_str]}) detected. Skipping daily report reminders.")
        return

    all_profiles = supa.get_all_profiles()
    active_interns = [u for u in all_profiles if u.get("role") == "intern" and u.get("status") in ("active", "at_risk")]
    
    all_leaves = ss.get_all_leaves()
    all_logs = ss.get_all_audit_logs()
    
    # Identify who already got a reminder today to avoid spamming
    reminded_intern_ids = set()
    today_prefix = f"daily_report_reminder_{date_str}"
    for log in all_logs:
        if log.get("action") == today_prefix:
            # actor_id is the intern_id in this case
            reminded_intern_ids.add(log.get("actor_id"))

    reminders_sent = 0
    base_url = os.environ.get("SITE_URL", "http://localhost:5000").rstrip("/")
    report_url = f"{base_url}/intern/reports"
    
    for intern in active_interns:
        intern_id = intern["id"]
        
        if intern_id in reminded_intern_ids:
            continue
            
        # Skip if intern is on leave today
        is_on_leave = any(
            l["intern_id"] == intern_id and 
            l["status"] == "approved" and 
            l["start_date"] <= date_str <= l["end_date"]
            for l in all_leaves
        )
        if is_on_leave:
            continue
            
        # Check if they submitted a report today
        submissions = ss.get_submissions_for_student(intern_id)
        has_report = any(s["task_id"] == f"REPORT-{date_str}" or s.get("submitted_at", "")[:10] == date_str for s in submissions)
        
        if not has_report:
            try:
                # Log it first to prevent duplicates
                ss.log_audit(intern_id, today_prefix, f"Sent daily report reminder for {date_str}")
                
                # Send email
                intern_email = intern.get("email")
                if intern_email:
                    send_daily_report_reminder_email(
                        target_email=intern_email,
                        name=intern.get("name", "Intern"),
                        department=intern.get("department", "Unknown"),
                        date_str=date_str,
                        report_url=report_url
                    )
                    reminders_sent += 1
            except Exception as e:
                logger.error(f"Failed to send daily report reminder to {intern_id}: {str(e)}")
                
    logger.info(f"=== check_and_send_daily_report_reminders completed: {reminders_sent} reminders sent ===")


def check_and_send_missing_report_escalations():
    """
    Called at Midnight (IST) to check if active interns have STILL not submitted 
    their daily reports. If not, send an escalation email to their manager.
    """
    logger.info("=== check_and_send_missing_report_escalations starting ===")
    
    import pytz
    from config import IST
    import os
    from services.email_service import send_missing_report_escalation
    
    # If run exactly at midnight, we want to check the *previous* day's report
    # Since it might trigger at 00:00:01, let's subtract 1 hour to get the logical "today" in IST
    now_ist = datetime.now(UTC).astimezone(IST)
    logical_today = now_ist - timedelta(hours=1)
    date_str = logical_today.strftime("%Y-%m-%d")
    
    # We only care about working days (Mon-Fri)
    if logical_today.weekday() >= 5:
        logger.info("Weekend detected. Skipping report escalations.")
        return
        
    # Check if the logical day was a holiday
    all_holidays = ss.get_all_holidays()
    holiday_names = {h["date"]: h["name"] for h in all_holidays}
    if date_str in holiday_names:
        logger.info(f"Holiday ({holiday_names[date_str]}) detected. Skipping report escalations.")
        return

    all_profiles = supa.get_all_profiles()
    active_interns = [u for u in all_profiles if u.get("role") == "intern" and u.get("status") in ("active", "at_risk")]
    
    # Pre-fetch all managers to easily find an intern's manager
    all_managers = {u["id"]: u for u in all_profiles if u.get("role") in ("manager", "admin")}
    
    all_leaves = ss.get_all_leaves()
    all_logs = ss.get_all_audit_logs()
    
    # Identify who already got an escalation today to avoid spamming
    escalated_intern_ids = set()
    escalation_prefix = f"missing_report_escalation_{date_str}"
    for log in all_logs:
        if log.get("action") == escalation_prefix:
            escalated_intern_ids.add(log.get("actor_id"))

    escalations_sent = 0
    
    for intern in active_interns:
        intern_id = intern["id"]
        
        if intern_id in escalated_intern_ids:
            continue
            
        # Skip if intern was on leave
        is_on_leave = any(
            l["intern_id"] == intern_id and 
            l["status"] == "approved" and 
            l["start_date"] <= date_str <= l["end_date"]
            for l in all_leaves
        )
        if is_on_leave:
            continue
            
        # Check if they submitted a report
        submissions = ss.get_submissions_for_student(intern_id)
        has_report = any(s["task_id"] == f"REPORT-{date_str}" or s.get("submitted_at", "")[:10] == date_str for s in submissions)
        
        if not has_report:
            try:
                # Find the manager
                manager_id = intern.get("manager_id")
                department = intern.get("department", "Unknown")
                manager = all_managers.get(manager_id)
                
                # If no specific manager, find ANY manager in the same department
                if not manager:
                    dept_managers = [m for m in all_managers.values() if m.get("department") == department and m.get("role") == "manager"]
                    if dept_managers:
                        manager = dept_managers[0]
                
                # If STILL no manager, fallback to admin
                if not manager:
                    admin_managers = [m for m in all_managers.values() if m.get("role") == "admin"]
                    if admin_managers:
                        manager = admin_managers[0]
                        
                if manager and manager.get("email"):
                    # Log it first to prevent duplicates
                    ss.log_audit(intern_id, escalation_prefix, f"Sent missing report escalation for {date_str}")
                    
                    intern_display = f"{intern.get('name', 'Intern')} ({intern.get('intern_id')})" if intern.get("intern_id") else intern.get("name", "Intern")
                    send_missing_report_escalation(
                        manager_email=manager.get("email"),
                        manager_name=manager.get("name", "Manager"),
                        intern_name=intern_display,
                        department=intern.get("department", "Unassigned"),
                        date_str=date_str,
                        host_url=os.environ.get("HOST_URL", "https://attendance-portal-theta-bice.vercel.app")
                    )
                    escalations_sent += 1
            except Exception as e:
                logger.error(f"Failed to send missing report escalation for {intern_id}: {str(e)}")
                
    logger.info(f"=== check_and_send_missing_report_escalations completed: {escalations_sent} escalations sent ===")
