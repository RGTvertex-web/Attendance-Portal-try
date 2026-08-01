

import logging
import os
from flask import Flask, session, g, redirect, url_for, request
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import get_config

from extensions import csrf, limiter


from werkzeug.middleware.proxy_fix import ProxyFix

def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config.from_object(get_config())

    # ── Jinja ──────────────────────────────────────────────────────────────────
    import jinja2
    app.jinja_env.undefined = jinja2.Undefined

    # ── Logging ────────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=app.config.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app.logger.setLevel(app.config.get("LOG_LEVEL", "INFO"))

    # ── Extensions ─────────────────────────────────────────────────────────────
    csrf.init_app(app)
    limiter.init_app(app)

    # ── Register Blueprints ────────────────────────────────────────────────────
    from routes.auth import auth_bp
    from routes.admin import admin_bp
    from routes.manager import manager_bp
    from routes.cron import cron_bp
    from routes.intern import intern_bp
    from routes.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(manager_bp, url_prefix="/manager")
    app.register_blueprint(intern_bp, url_prefix="/intern")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(cron_bp, url_prefix="/internal/cron")

    # ── Auth & Timing Hooks ───────────────────────────────────────────────────
    import time
    
    @app.before_request
    def load_user_from_session():
        g.start_time = time.time()
        g.user = None
        g.user_role = None
        
        # Don't try to auth static assets
        if request.endpoint and request.endpoint.startswith('static'):
            return

        user_id = session.get("user_id")
        session_token = session.get("session_token")
        if user_id:
            try:
                from services.supabase_service import get_profile
                user_data = get_profile(user_id)
                if user_data:
                    # Session hijacking / password change invalidation check
                    db_session_token = user_data.get("session_token")
                    if db_session_token and str(db_session_token) != str(session_token):
                        app.logger.info(f"Invalid session token for user {user_id}. Logging out.")
                        session.clear()
                        return
                    
                    g.user = user_data
                    g.user_role = user_data.get("role")
            except Exception as e:
                app.logger.warning(f"Session user invalid: {e}")
                session.clear()

    @app.after_request
    def log_request_timing(response):
        if hasattr(g, 'start_time') and not (request.endpoint and request.endpoint.startswith('static')):
            duration = time.time() - g.start_time
            app.logger.info("Route %s took %.2fs", request.path, duration)
        return response

    # ── APScheduler ────────────────────────────────────────────────────────────
    # Do not start background threads on Vercel serverless runtime
    # (Background scheduler removed for Vercel Serverless compatibility)
    # Instead, we use Vercel Crons hitting /api/cron endpoints

    # ── No-Cache Headers ───────────────────────────────────────────────────────
    @app.after_request
    def add_no_cache_headers(response):
        # Don't disable caching for static assets
        if request.path and not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    # ── Root redirect ──────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        if getattr(g, "user", None):
            role = g.user_role
            if role == "admin":
                return redirect(url_for("admin.dashboard"))
            elif role == "manager":
                return redirect(url_for("manager.dashboard"))
            elif role == "intern":
                return redirect(url_for("intern.dashboard"))
        return redirect(url_for("auth.login"))

    @app.route("/api/health")
    def health_check():
        try:
            from services import supabase_service as supa
            from services import sheets_service as ss
            
            # 1. Check Supabase
            client = supa.get_supabase_client()
            client.table("users").select("id").limit(1).execute()
            
            # 2. Check Sheets
            ss._get_spreadsheet()
            
            return {"status": "ok", "supabase": "connected", "sheets": "connected"}, 200
        except Exception as e:
            app.logger.error(f"Health check failed: {e}")
            return {"status": "error", "message": str(e)}, 503

    @app.context_processor
    def inject_global_vars():
        from config import get_departments
        from services import sheets_service as ss
        from services import supabase_service as supa
        from flask import url_for
        
        def get_notifications():
            notifications = []
            try:
                if getattr(g, "user", None):
                    if g.user_role == "manager":
                        manager_leaves = ss.get_leaves_for_manager(g.user["id"])
                        pending_leaves = [l for l in manager_leaves if l.get("status") == "pending"]
                        if pending_leaves:
                            notifications.append({"text": f"{len(pending_leaves)} pending leave requests", "link": url_for("manager.leaves")})
                        
                        students = [s["id"] for s in supa.get_profiles_by_manager(g.user["id"])]
                        pending_reports = ss.get_pending_submissions_for_manager(g.user["id"], set(students))
                        if pending_reports:
                            notifications.append({"text": f"{len(pending_reports)} reports to review", "link": url_for("manager.reports")})
                    elif g.user_role == "intern":
                        # Warnings
                        unack = [w for w in ss.get_warnings_for_student(g.user["id"]) if w.get("acknowledged") != "yes"]
                        if unack:
                            notifications.append({"text": f"You have {len(unack)} unacknowledged warning(s)", "link": url_for("intern.dashboard")})
                        
                        # Leaves (decided recently)
                        from datetime import datetime, timedelta, timezone
                        now = datetime.now(timezone.utc)
                        recent_leaves = [l for l in ss.get_leaves_for_student(g.user["id"]) if l.get("status") in ("approved", "rejected") and l.get("decided_at")]
                        # Filter to last 3 days
                        for l in recent_leaves:
                            try:
                                decided_dt = datetime.fromisoformat(l["decided_at"].replace("Z", "+00:00"))
                                if now - decided_dt <= timedelta(days=3):
                                    notifications.append({"text": f"Leave from {l['start_date']} was {l['status']}", "link": url_for("intern.leave")})
                            except:
                                pass
                        
                        # Performance Reports (recent)
                        recent_perfs = [p for p in ss.get_performance_reports_for_student(g.user["id"]) if p.get("submitted_at")]
                        for p in recent_perfs:
                            try:
                                sub_dt = datetime.fromisoformat(p["submitted_at"].replace("Z", "+00:00"))
                                if now - sub_dt <= timedelta(days=3):
                                    notifications.append({"text": f"New performance report for {p.get('period_start', '')}", "link": url_for("intern.performance")})
                            except:
                                pass
                        
                        # Attendance marked today
                        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        att = [a for a in ss.get_all_attendance() if str(a.get("intern_id")) == str(g.user["id"]) and a.get("date") == today_str]
                        if att:
                            notifications.append({"text": f"Attendance marked as {att[0].get('status', '')} today", "link": url_for("intern.attendance")})
                    elif g.user_role == "admin":
                        from datetime import datetime, timedelta, timezone
                        now = datetime.now(timezone.utc)
                        
                        # 1. New intern signups (recent, last 3 days)
                        all_users = supa.get_all_profiles()
                        recent_interns = 0
                        for u in all_users:
                            if u.get("role") == "intern" and u.get("created_at"):
                                try:
                                    c_dt = datetime.fromisoformat(u["created_at"].replace("Z", "+00:00"))
                                    if now - c_dt <= timedelta(days=3):
                                        recent_interns += 1
                                except:
                                    pass
                        if recent_interns:
                            notifications.append({"text": f"{recent_interns} new intern(s) joined recently", "link": url_for("admin.users")})
                            
                        # 2. Pending internship extension requests (graceful check)
                        try:
                            all_exts = ss.get_all_extensions() if hasattr(ss, "get_all_extensions") else []
                            pending_exts = sum(1 for e in all_exts if e.get("status") == "pending")
                            if pending_exts:
                                notifications.append({"text": f"{pending_exts} pending extension request(s)", "link": url_for("admin.dashboard")})
                        except Exception:
                            pass
                        
                        # 3. System-issued absence warnings (recent, last 3 days)
                        all_warnings = ss.get_all_warnings()
                        recent_warnings = 0
                        for w in all_warnings:
                            if w.get("date"):
                                try:
                                    w_dt = datetime.strptime(w["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                                    if now - w_dt <= timedelta(days=3):
                                        recent_warnings += 1
                                except:
                                    pass
                        if recent_warnings:
                            notifications.append({"text": f"{recent_warnings} auto-warning(s) sent for unapproved absence", "link": url_for("admin.dashboard")}) # Update to admin.warnings when route exists
                            
                        # 4. Leave requests still pending after 48 hours (or starting soon)
                        all_leaves = ss.get_all_leaves()
                        stale_leaves = 0
                        for l in all_leaves:
                            if l.get("status") == "pending":
                                is_stale = False
                                # Prefer created_at if it gets added later
                                c_dt_str = l.get("created_at") or l.get("submitted_at")
                                if c_dt_str:
                                    try:
                                        c_dt = datetime.fromisoformat(c_dt_str.replace("Z", "+00:00"))
                                        if now - c_dt >= timedelta(hours=48):
                                            is_stale = True
                                    except: pass
                                elif l.get("start_date"):
                                    # Fallback: leave starts in less than 48 hours and is STILL pending
                                    try:
                                        s_dt = datetime.strptime(l["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                                        if s_dt - now <= timedelta(hours=48):
                                            is_stale = True
                                    except: pass
                                
                                if is_stale:
                                    stale_leaves += 1
                        if stale_leaves:
                            notifications.append({"text": f"{stale_leaves} leave request(s) pending manager review for 48+ hours", "link": url_for("admin.dashboard")})
                            
                        # 5. Departments with no manager
                        depts = get_departments()
                        active_mgr_depts = {u.get("department") for u in all_users if u.get("role") == "manager" and u.get("status") == "active"}
                        for d in depts:
                            if d not in active_mgr_depts:
                                notifications.append({"text": f"{d} has no active manager", "link": url_for("admin.users")})
                                
            except Exception as e:
                app.logger.error("Notification context processor failed: %s", e)
            return notifications

        def get_announcements():
            try:
                return ss.get_all_announcements()
            except Exception:
                return []
                
        def get_today_report():
            try:
                if getattr(g, "user", None) and g.user_role == "intern":
                    submissions = ss.get_submissions_for_student(g.user["id"])
                    from datetime import datetime, timezone
                    import json
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    virtual_task_id = f"REPORT-{today_str}"
                    for sub in submissions:
                        try:
                            sub["report_data"] = json.loads(sub.get("notes", "{}"))
                        except:
                            sub["report_data"] = {"given": "Unknown", "done": sub.get("notes", ""), "remaining": "Unknown"}
                    return next((s for s in submissions if str(s.get("task_id")) == virtual_task_id), None)
            except Exception as context_processor_err:
                app.logger.error("Failed to get today_report in context processor: %s", context_processor_err)
            return None

        from werkzeug.local import LocalProxy
        return dict(
            DEPARTMENTS=LocalProxy(get_departments), 
            notifications=LocalProxy(get_notifications), 
            announcements=LocalProxy(get_announcements),
            today_report=LocalProxy(get_today_report)
        )

    @app.errorhandler(404)
    def handle_404(e):
        from flask import render_template
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def handle_405(e):
        from flask import render_template
        return render_template("errors/404.html"), 405

    @app.errorhandler(500)
    def handle_500(e):
        from flask import render_template
        app.logger.error("Unhandled exception: %s", e, exc_info=True)
        return render_template("errors/500.html"), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
