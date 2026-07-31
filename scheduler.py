"""
scheduler.py — APScheduler daily attendance job
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

logger = logging.getLogger(__name__)


def start_scheduler(app):
    """Start the background scheduler attached to the Flask app context."""
    hour_utc   = app.config.get("ATTENDANCE_JOB_HOUR_UTC", 13)
    minute_utc = app.config.get("ATTENDANCE_JOB_MINUTE_UTC", 0)

    scheduler = BackgroundScheduler(timezone=pytz.utc)

    def run_daily_job():
        with app.app_context():
            from services.attendance_service import evaluate_attendance_for_date, check_and_send_absence_warnings
            logger.info("Scheduler: running daily attendance evaluation")
            try:
                summary = evaluate_attendance_for_date()
                logger.info("Scheduler: completed %s", summary)
                
                logger.info("Scheduler: running absence warning evaluation")
                check_and_send_absence_warnings()
                logger.info("Scheduler: completed absence warnings")
            except Exception as exc:
                logger.error("Scheduler: attendance job failed — %s", exc, exc_info=True)

    scheduler.add_job(
        run_daily_job,
        trigger=CronTrigger(hour=hour_utc, minute=minute_utc, timezone=pytz.utc),
        id="daily_attendance",
        name="Daily Attendance Evaluation",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow up to 1hr late firing
    )
    
    def run_monthly_report_check():
        with app.app_context():
            from services.attendance_service import check_missing_monthly_reports
            logger.info("Scheduler: running monthly missing report check")
            try:
                check_missing_monthly_reports()
                logger.info("Scheduler: completed monthly report check")
            except Exception as exc:
                logger.error("Scheduler: monthly report check failed — %s", exc, exc_info=True)

    # Run on the 1st of every month at 8:00 AM UTC
    scheduler.add_job(
        run_monthly_report_check,
        trigger=CronTrigger(day=1, hour=8, minute=0, timezone=pytz.utc),
        id="monthly_missing_reports",
        name="Monthly Missing Reports Check",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    def run_daily_report_reminders():
        with app.app_context():
            from services.attendance_service import check_and_send_daily_report_reminders
            logger.info("Scheduler: running daily report reminders check")
            try:
                check_and_send_daily_report_reminders()
                logger.info("Scheduler: completed daily report reminders check")
            except Exception as exc:
                logger.error("Scheduler: daily report reminders check failed — %s", exc, exc_info=True)

    ist_tz = pytz.timezone("Asia/Kolkata")
    
    # Read deadline time from config
    deadline_str = app.config.get("REPORT_DEADLINE_TIME", "22:30")
    try:
        hr, mn = map(int, deadline_str.split(":"))
    except:
        hr, mn = 22, 30
        
    # Run every weekday at the deadline time IST
    scheduler.add_job(
        run_daily_report_reminders,
        trigger=CronTrigger(day_of_week='mon-fri', hour=hr, minute=mn, timezone=ist_tz),
        id="daily_report_reminders",
        name="Daily Report Reminders Check",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    def run_missing_report_escalations():
        with app.app_context():
            from services.attendance_service import check_and_send_missing_report_escalations
            logger.info("Scheduler: running missing report escalations check")
            try:
                check_and_send_missing_report_escalations()
                logger.info("Scheduler: completed missing report escalations check")
            except Exception as exc:
                logger.error("Scheduler: missing report escalations check failed — %s", exc, exc_info=True)
                
    # Run every weekday at midnight IST
    scheduler.add_job(
        run_missing_report_escalations,
        trigger=CronTrigger(day_of_week='mon-fri', hour=0, minute=0, timezone=ist_tz),
        id="missing_report_escalations",
        name="Missing Report Escalations Check",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("APScheduler started — attendance job at %02d:%02d UTC daily, daily report reminders at %02d:%02d IST (Mon-Fri), missing report escalations at midnight IST (Mon-Fri), monthly reports check on 1st of month", hour_utc, minute_utc, hr, mn)
    return scheduler
