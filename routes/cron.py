import os
import logging
from flask import Blueprint, request, jsonify, current_app

cron_bp = Blueprint("cron", __name__, url_prefix="/api/cron")
logger = logging.getLogger(__name__)

def verify_cron_secret():
    """Verify the request comes from Vercel Cron or authorized sender."""
    auth_header = request.headers.get("Authorization")
    cron_secret = os.environ.get("CRON_SECRET")
    
    if not cron_secret:
        # If not set in environment, allow for now (warn in logs)
        logger.warning("CRON_SECRET is not set in environment variables.")
        return True
        
    expected_header = f"Bearer {cron_secret}"
    if auth_header != expected_header:
        # Also check query param as fallback
        if request.args.get("secret") != cron_secret:
            return False
    return True

@cron_bp.before_request
def require_cron_auth():
    if not verify_cron_secret():
        return jsonify({"error": "Unauthorized"}), 401

@cron_bp.route("/daily-attendance", methods=["GET", "POST"])
def daily_attendance():
    from services.attendance_service import evaluate_attendance_for_date, check_and_send_absence_warnings
    logger.info("Cron: running daily attendance evaluation")
    try:
        summary = evaluate_attendance_for_date()
        logger.info("Cron: completed %s", summary)
        
        logger.info("Cron: running absence warning evaluation")
        check_and_send_absence_warnings()
        logger.info("Cron: completed absence warnings")
        return jsonify({"status": "success", "summary": summary})
    except Exception as exc:
        logger.error("Cron: attendance job failed — %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

@cron_bp.route("/monthly-reports", methods=["GET", "POST"])
def monthly_reports():
    from services.attendance_service import check_missing_monthly_reports
    logger.info("Cron: running monthly missing report check")
    try:
        check_missing_monthly_reports()
        logger.info("Cron: completed monthly report check")
        return jsonify({"status": "success"})
    except Exception as exc:
        logger.error("Cron: monthly report check failed — %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

@cron_bp.route("/daily-reminders", methods=["GET", "POST"])
def daily_reminders():
    from services.attendance_service import check_and_send_daily_report_reminders
    logger.info("Cron: running daily report reminders check")
    try:
        check_and_send_daily_report_reminders()
        logger.info("Cron: completed daily report reminders check")
        return jsonify({"status": "success"})
    except Exception as exc:
        logger.error("Cron: daily report reminders check failed — %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

@cron_bp.route("/missing-escalations", methods=["GET", "POST"])
def missing_escalations():
    from services.attendance_service import check_and_send_missing_report_escalations
    logger.info("Cron: running missing report escalations check")
    try:
        check_and_send_missing_report_escalations()
        logger.info("Cron: completed missing report escalations check")
        return jsonify({"status": "success"})
    except Exception as exc:
        logger.error("Cron: missing report escalations check failed — %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

@cron_bp.route("/completion-reminders", methods=["GET", "POST"])
def completion_reminders():
    from services import supabase_service as supa
    from services.internship_cycle_service import get_cycle_info
    from services.email_service import send_internship_completion_reminder
    from datetime import datetime
    
    logger.info("Cron: running completion reminders check")
    try:
        interns = supa.get_all_interns()
        notified = 0
        for intern in interns:
            if intern.get("status") != "active":
                continue
                
            cycle_info = get_cycle_info(intern.get("joining_date", ""), intern.get("internship_duration_months", 0))
            completion_date_str = cycle_info.get("expected_completion_date")
            
            if not completion_date_str or completion_date_str == "N/A":
                continue
                
            try:
                comp_dt = datetime.strptime(completion_date_str, "%Y-%m-%d").date()
                days_left = (comp_dt - datetime.today().date()).days
                
                # Trigger emails on exactly 14 and 7 days prior
                if days_left in [14, 7]:
                    # Send to intern
                    if intern.get("email"):
                        send_internship_completion_reminder(intern["email"], intern["name"], "intern", completion_date_str, intern["name"], os.environ.get("HOST_URL", "https://attendance-portal-theta-bice.vercel.app"))
                    
                    # Send to manager
                    if intern.get("manager_id"):
                        mgr = supa.get_profile(intern.get("manager_id"))
                        if mgr and mgr.get("email"):
                            send_internship_completion_reminder(mgr["email"], mgr["name"], "manager", completion_date_str, intern["name"], os.environ.get("HOST_URL", "https://attendance-portal-theta-bice.vercel.app"))
                    
                    notified += 1
            except Exception as e:
                logger.error(f"Error processing completion date for {intern.get('id')}: {e}")
                
        logger.info(f"Cron: completion reminders check finished, notified {notified} users")
        return jsonify({"status": "success", "notified": notified})
    except Exception as exc:
        logger.error("Cron: completion reminders check failed — %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500

