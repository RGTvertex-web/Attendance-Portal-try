"""
auth_helpers.py - Decorators for role-based access control using Flask's g object.
"""
from functools import wraps
from flask import g, redirect, url_for, flash, request

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user_role != "admin":
            flash("Administrator access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function

def manager_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user_role not in ["manager", "admin"]:
            flash("Manager access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function

def intern_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        # Admins and managers shouldn't normally be accessing intern endpoints, 
        # but you can decide if they should be allowed. 
        # For this portal, we restrict it to interns.
        if g.user_role != "intern":
            flash("Intern access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function

def can_manage_intern(manager_user, intern_profile):
    """
    Check if a manager or admin has permission to manage an intern.
    In department-wise management, all managers in a department have equal access to all interns in that department.
    """
    if not manager_user or not intern_profile:
        return False
    if manager_user.get("role") == "admin":
        return True
    if manager_user.get("role") == "manager":
        # Check direct manager_id match
        if str(intern_profile.get("manager_id", "")).strip().lower() == str(manager_user.get("id", "")).strip().lower():
            return True
        # Check department match (co-managers in same department have full access)
        mgr_dept = str(manager_user.get("department", "")).strip().lower()
        int_dept = str(intern_profile.get("department", "")).strip().lower()
        if mgr_dept and int_dept and mgr_dept == int_dept:
            return True
    # An intern can only access their own data
    if str(intern_profile.get("id", "")).strip().lower() == str(manager_user.get("id", "")).strip().lower():
        return True
    return False
