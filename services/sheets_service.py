

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from threading import Lock
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials
from services.cache_service import global_cache
from services.performance_service import get_performance_summary

logger = logging.getLogger(__name__)

# ── Column maps (0-indexed) ────────────────────────────────────────────────────
_TASKS_COLS = {
    "task_id": 0, "title": 1, "description": 2, "category": 3,
    "department": 4, "assigned_to": 5, "assigned_by": 6, "due_date": 7, "created_at": 8,
}
_SUBMISSIONS_COLS = {
    "submission_id": 0, "task_id": 1, "intern_id": 2, "submitted_at": 3,
    "status": 4, "content_link": 5, "notes": 6, "remarks": 7,
}
_ATTENDANCE_COLS = {
    "attendance_id": 0, "intern_id": 1, "department": 2, "date": 3, "status": 4,
    "category": 5, "linked_task_id": 6, "marked_at": 7, "notes": 8,
}
_WARNINGS_COLS = {
    "warning_id": 0, "intern_id": 1, "department": 2, "date": 3, "reason": 4,
    "issued_by": 5, "acknowledged": 6, "status": 7
}
_LEAVES_COLS = {
    "leave_id": 0, "intern_id": 1, "department": 2, "manager_id": 3, "start_date": 4,
    "end_date": 5, "days_requested": 6, "reason": 7, "status": 8, "decided_by": 9,
    "decided_at": 10, "remarks": 11, "leave_category": 12
}
_REPORTS_COLS = {
    "report_id": 0, "intern_id": 1, "department": 2, "manager_id": 3,
    "report_type": 4, "period_start": 5, "period_end": 6, "content": 7,
    "submitted_at": 8, "reviewed_by": 9, "review_notes": 10, "reviewed_at": 11,
}
_MANAGER_NOTES_COLS = {
    "note_id": 0, "intern_id": 1, "manager_id": 2, "timestamp": 3, "content": 4,
}
_PERFORMANCE_COLS = {
    "report_id": 0, "intern_id": 1, "manager_id": 2, "period_start": 3, "period_end": 4,
    "work_quality": 5, "old_task_completion": 6, "learning_ability": 7, "old_teamwork": 8, "old_discipline": 9, "behaviour": 10, "overall": 11,
    "total_score": 12, "grade_band": 13, "strengths": 14, "areas_improvement": 15, "overall_comments": 16, "submitted_at": 17,
    "edit_reason": 18, "intern_acknowledged": 19, "intern_ack_date": 20,
    "technical_skill": 21, "communication": 22, "discipline": 23, "task_completion": 24, "initiative": 25, "teamwork": 26, "code_quality": 27
}
_INVITES_COLS = {
    "invite_id": 0, "email": 1, "department": 2, "role": 3, "token": 4, 
    "invited_by": 5, "created_at": 6, "expires_at": 7, "used": 8,
}

SHEET_HEADERS = {
    "Users":       ["id", "email", "name", "role", "department", "manager_id", "internship_duration_months", "leave_allotted_days", "status", "joining_date", "intern_id", "deactivation_reason", "phone", "college_name"],
    "Tasks":       ["task_id", "title", "description", "category", "department", "assigned_to", "assigned_by", "due_date", "created_at"],
    "Submissions": ["submission_id", "task_id", "intern_id", "submitted_at", "status", "content_link", "notes", "remarks"],
    "Attendance":  ["attendance_id", "intern_id", "department", "date", "status", "category", "linked_task_id", "marked_at", "notes"],
    "Warnings":    ["warning_id", "intern_id", "department", "date", "reason", "issued_by", "acknowledged", "status"],
    "Leaves":      ["leave_id", "intern_id", "department", "manager_id", "start_date", "end_date", "days_requested", "reason", "status", "decided_by", "decided_at", "remarks", "leave_category"],
    "Reports":     ["report_id", "intern_id", "department", "manager_id", "report_type", "period_start", "period_end", "content", "submitted_at", "reviewed_by", "review_notes", "reviewed_at"],
    "Performance": ["report_id", "intern_id", "manager_id", "period_start", "period_end", "work_quality", "old_task_completion", "learning_ability", "old_teamwork", "old_discipline", "behaviour", "overall", "total_score", "grade_band", "strengths", "areas_improvement", "overall_comments", "submitted_at", "edit_reason", "intern_acknowledged", "intern_ack_date", "technical_skill", "communication", "discipline", "task_completion", "initiative", "teamwork", "code_quality"],
    "Invites":     ["invite_id", "email", "department", "role", "token", "invited_by", "created_at", "expires_at", "used"],
    "ManagerNotes": ["note_id", "intern_id", "manager_id", "timestamp", "content"],
    "Holidays":    ["date", "name"],
    "Settings":    ["key", "value"],
    "AuditLog":    ["timestamp", "actor_id", "action", "details"],
    "Extensions":  ["extension_id", "intern_id", "manager_id", "current_duration", "requested_months", "reason", "status", "created_at", "decision_notes"],
}

_USERS_COLS = {
    "id": 0, "email": 1, "name": 2, "role": 3, 
    "department": 4, "manager_id": 5, "internship_duration_months": 6, 
    "leave_allotted_days": 7, "status": 8, "joining_date": 9,
    "intern_id": 10, "deactivation_reason": 11, "phone": 12, "college_name": 13
}

_EXTENSIONS_COLS = {
    "extension_id": 0, "intern_id": 1, "manager_id": 2, "current_duration": 3,
    "requested_months": 4, "reason": 5, "status": 6, "created_at": 7, "decision_notes": 8,
}

_HOLIDAYS_COLS = {"date": 0, "name": 1}
_SETTINGS_COLS = {"key": 0, "value": 1}
_AUDIT_COLS = {"timestamp": 0, "actor_id": 1, "action": 2, "details": 3}

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Thread-safe client singleton
_client: Optional[gspread.Client] = None
_client_lock = Lock()


def _get_client() -> gspread.Client:
    global _client
    with _client_lock:
        if _client is None:
            creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                raise RuntimeError("GOOGLE_CREDENTIALS_JSON env var not set")
            creds_info = json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_info, scopes=_SCOPES)
            _client = gspread.authorize(creds)
            logger.info("Google Sheets client initialised")
    return _client


_spreadsheet: Optional[gspread.Spreadsheet] = None
_spreadsheet_lock = Lock()

def _get_spreadsheet() -> gspread.Spreadsheet:
    global _spreadsheet
    if _spreadsheet is None:
        with _spreadsheet_lock:
            if _spreadsheet is None:
                spreadsheet_id = os.environ.get("SPREADSHEET_ID")
                if not spreadsheet_id:
                    raise RuntimeError("SPREADSHEET_ID env var not set")
                for attempt in range(4):
                    try:
                        _spreadsheet = _get_client().open_by_key(spreadsheet_id)
                        break
                    except Exception as e:
                        if attempt < 3 and any(x in str(e) for x in ("500", "502", "503", "504", "429", "APIError", "connection", "timeout")):
                            time.sleep(0.5 * (2 ** attempt))
                            continue
                        raise
    return _spreadsheet


class WorksheetProxy:
    """Proxy around gspread.Worksheet that automatically retries intermittent API 500/429 errors."""
    def __init__(self, worksheet: gspread.Worksheet):
        self._worksheet = worksheet

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._worksheet, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                for attempt in range(4):
                    try:
                        return attr(*args, **kwargs)
                    except Exception as e:
                        if attempt < 3 and any(x in str(e) for x in ("500", "502", "503", "504", "429", "APIError", "connection", "timeout")):
                            logger.warning("Google Sheets API error on %s (attempt %d): %s. Retrying...", name, attempt + 1, e)
                            time.sleep(0.5 * (2 ** attempt))
                            continue
                        raise
            return wrapper
        return attr


@lru_cache(maxsize=32)
def _get_sheet(name: str) -> Any:
    for attempt in range(4):
        try:
            ws = _get_spreadsheet().worksheet(name)
            return WorksheetProxy(ws)
        except gspread.exceptions.WorksheetNotFound:
            logger.info("Sheet tab '%s' not found in spreadsheet. Auto-creating...", name)
            try:
                ss = _get_spreadsheet()
                headers = SHEET_HEADERS.get(name, ["id", "created_at"])
                ws = ss.add_worksheet(title=name, rows=1000, cols=max(len(headers) + 5, 26))
                if headers:
                    ws.insert_row(headers, index=1)
                return WorksheetProxy(ws)
            except Exception as create_err:
                logger.error("Failed to auto-create worksheet '%s': %s", name, create_err)
                raise
        except Exception as e:
            if "WorksheetNotFound" in str(type(e)):
                logger.info("Sheet tab '%s' not found in spreadsheet. Auto-creating...", name)
                try:
                    ss = _get_spreadsheet()
                    headers = SHEET_HEADERS.get(name, ["id", "created_at"])
                    ws = ss.add_worksheet(title=name, rows=1000, cols=max(len(headers) + 5, 26))
                    if headers:
                        ws.insert_row(headers, index=1)
                    return WorksheetProxy(ws)
                except Exception as create_err:
                    logger.error("Failed to auto-create worksheet '%s': %s", name, create_err)
                    raise
            if attempt < 3 and any(x in str(e) for x in ("500", "502", "503", "504", "429", "APIError", "connection", "timeout")):
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise


def _rows_to_dicts(rows: List[List[str]], col_map: Dict[str, int]) -> List[Dict[str, Any]]:
    """Convert raw row list to list of dicts using column map."""
    result = []
    for row in rows:
        # Pad short rows
        padded = row + [""] * (max(col_map.values()) + 1 - len(row))
        result.append({key: padded[idx] for key, idx in col_map.items()})
    return result


def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _ensure_sheet_columns(sheet_name: str) -> None:
    try:
        sheet = _get_sheet(sheet_name)
        expected_headers = SHEET_HEADERS.get(sheet_name, [])
        if not expected_headers:
            return
        if hasattr(sheet, "_worksheet") and hasattr(sheet._worksheet, "col_count"):
            if sheet._worksheet.col_count < len(expected_headers):
                sheet._worksheet.resize(cols=max(sheet._worksheet.col_count, len(expected_headers)))
        rows = sheet.get_all_values()
        if not rows:
            sheet.append_row(expected_headers, value_input_option="RAW", table_range="A1")
        elif len(rows[0]) < len(expected_headers) or rows[0][:len(expected_headers)] != expected_headers:
            padded_header = expected_headers
            def _col_letter(idx):
                res = ""
                while idx > 0:
                    idx, rem = divmod(idx - 1, 26)
                    res = chr(ord('A') + rem) + res
                return res or "A"
            end_col_letter = _col_letter(len(expected_headers))
            sheet.update(f"A1:{end_col_letter}1", [padded_header], value_input_option="RAW")
    except Exception as e:
        logger.warning(f"Could not ensure sheet columns for {sheet_name}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_users() -> List[Dict]:
    cached = global_cache.get("Users")
    if cached is not None:
        return cached

    try:
        sheet = _get_sheet("Users")
        rows = sheet.get_all_values()[1:]
        res = _rows_to_dicts(rows, _USERS_COLS)
        global_cache.set("Users", res)
        return res
    except Exception as e:
        logger.error(f"Error getting all users from Sheets: {e}")
        return []

def add_user_to_sheet(user_dict: Dict) -> None:
    try:
        sheet = _get_sheet("Users")
        row = [
            str(user_dict.get("id", "")),
            str(user_dict.get("email", "")),
            str(user_dict.get("name", "")),
            str(user_dict.get("role", "")),
            str(user_dict.get("department", "")),
            str(user_dict.get("manager_id", "")),
            str(user_dict.get("internship_duration_months", "")),
            str(user_dict.get("leave_allotted_days", "")),
            str(user_dict.get("status", "")),
            str(user_dict.get("joining_date", "")),
            str(user_dict.get("intern_id", "")),
            str(user_dict.get("deactivation_reason", "")),
            str(user_dict.get("phone", "")),
            str(user_dict.get("college_name", ""))
        ]
        try:
            sheet.append_row(row, value_input_option="RAW", table_range="A1")
            global_cache.invalidate("Users")
            logger.info("Added user %s to Users sheet", user_dict.get("id"))
        except Exception as e:
            logger.error(f"Failed to add user {user_dict.get('id')} to Sheets: {str(e)}")
            raise ValueError(f"Could not save user to Google Sheets. Please try again later.")
    except Exception as e:
        logger.error(f"Failed to append user to Sheets: {e}")

def update_user_in_sheet(user_id: str, **fields) -> bool:
    try:
        sheet = _get_sheet("Users")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[0] == user_id:
                for field, value in fields.items():
                    if field in _USERS_COLS:
                        sheet.update_cell(i, _USERS_COLS[field] + 1, str(value) if value is not None else "")
                global_cache.invalidate("Users")
                logger.info("Updated user %s in Sheets", user_id)
                return True
        return False
    except Exception as e:
        logger.error(f"Error updating user {user_id} in sheet: {e}")
        return False

def delete_user_from_sheet(user_id: str) -> bool:
    try:
        sheet = _get_sheet("Users")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[0] == user_id:  # id is index 0
                sheet.delete_rows(i)
                global_cache.invalidate("Users")
                logger.info("Deleted user %s from Users sheet", user_id)
                return True
        return False
    except Exception as e:
        logger.error(f"Error deleting user from sheet: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_tasks() -> List[Dict]:
    cached = global_cache.get("Tasks")
    if cached is not None:
        return cached

    sheet = _get_sheet("Tasks")
    rows = sheet.get_all_values()[1:]
    res = _rows_to_dicts(rows, _TASKS_COLS)
    
    global_cache.set("Tasks", res)
    return res


def get_task_by_id(task_id: str) -> Optional[Dict]:
    for t in get_all_tasks():
        if t["task_id"] == task_id:
            return t
    return None


def get_tasks_for_student(intern_id: str, department: str) -> List[Dict]:
    """Return tasks assigned to this intern or to 'all' in their department."""
    return [t for t in get_all_tasks()
            if t["department"] == department and t["assigned_to"] in (intern_id, "all")]


def get_tasks_by_manager(manager_id: str, department: str = None) -> List[Dict]:
    all_tasks = get_all_tasks()
    my_intern_ids = _get_manager_intern_ids(manager_id) if not department else set()
    res = []
    for t in all_tasks:
        if _match_user_id(t.get("assigned_by"), manager_id):
            res.append(t)
        elif department and t.get("department") in (department, "All", "all", ""):
            if t not in res:
                res.append(t)
        elif my_intern_ids and any(_match_user_id(t.get("assigned_to"), iid) for iid in my_intern_ids):
            if t not in res:
                res.append(t)
        elif str(t.get("assigned_by", "")).lower().startswith("admin"):
            if not department or t.get("department") in (department, "All", "all", ""):
                if t not in res:
                    res.append(t)
    return res


def create_task(title: str, description: str, category: str, department: str,
                assigned_to: str, assigned_by: str, due_date: str) -> Dict:
    sheet = _get_sheet("Tasks")
    task_id = _new_id()
    row = [task_id, title.strip(), description.strip(), category.strip(), department,
           assigned_to, assigned_by, due_date, _now_utc_str()]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Created task %s '%s' due=%s", task_id, title, due_date)
    global_cache.invalidate("Tasks")
    return get_task_by_id(task_id)


def update_task(task_id: str, **fields) -> bool:
    sheet = _get_sheet("Tasks")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if row[_TASKS_COLS["task_id"]] == task_id:
            for field, value in fields.items():
                if field in _TASKS_COLS:
                    sheet.update_cell(i, _TASKS_COLS[field] + 1, value)
            global_cache.invalidate("Tasks")
            return True
    return False


def delete_task(task_id: str) -> bool:
    try:
        sheet = _get_sheet("Tasks")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_TASKS_COLS["task_id"]] == task_id:
                sheet.delete_rows(i)
                global_cache.invalidate("Tasks")
                return True
        return False
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SUBMISSIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_submissions() -> List[Dict]:
    cached = global_cache.get("Submissions")
    if cached is not None:
        return cached

    sheet = _get_sheet("Submissions")
    rows = sheet.get_all_values()[1:]
    res = _rows_to_dicts(rows, _SUBMISSIONS_COLS)

    global_cache.set("Submissions", res)
    return res


def get_submission(task_id: str, intern_id: str) -> Optional[Dict]:
    for s in get_all_submissions():
        if s["task_id"] == task_id and s["intern_id"] == intern_id:
            return s
    return None


def get_submissions_for_student(intern_id: str) -> List[Dict]:
    return [s for s in get_all_submissions() if _match_user_id(s.get("intern_id"), intern_id)]


def get_submissions_for_task(task_id: str) -> List[Dict]:
    return [s for s in get_all_submissions() if s["task_id"] == task_id]


def get_pending_submissions_for_manager(manager_id: str, intern_ids: set) -> List[Dict]:
    """Return tasks where at least one intern hasn't submitted."""
    tasks = get_tasks_by_manager(manager_id)
    pending = []
    for task in tasks:
        subs = {s["intern_id"] for s in get_submissions_for_task(task["task_id"])}
        not_submitted = intern_ids - subs
        if not_submitted:
            task["missing_count"] = len(not_submitted)
            pending.append(task)
    return pending


def create_submission(task_id: str, intern_id: str, content_link: str,
                      notes: str, submitted_at: str, status: str) -> Dict:
    sheet = _get_sheet("Submissions")
    sub_id = _new_id()
    row = [sub_id, task_id, intern_id, submitted_at, status,
           content_link.strip(), notes.strip(), ""]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Submission %s task=%s intern=%s status=%s", sub_id, task_id, intern_id, status)
    global_cache.invalidate("Submissions")
    return {"submission_id": sub_id, "task_id": task_id, "intern_id": intern_id,
            "submitted_at": submitted_at, "status": status, "content_link": content_link,
            "notes": notes, "remarks": ""}


def update_submission(submission_id: str, **fields) -> bool:
    sheet = _get_sheet("Submissions")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if row[_SUBMISSIONS_COLS["submission_id"]] == submission_id:
            for field, value in fields.items():
                if field in _SUBMISSIONS_COLS:
                    sheet.update_cell(i, _SUBMISSIONS_COLS[field] + 1, value)
            global_cache.invalidate("Submissions")
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# ATTENDANCE
# ══════════════════════════════════════════════════════════════════════════════

def get_all_attendance() -> List[Dict]:
    cached = global_cache.get("Attendance")
    if cached is not None:
        return cached

    sheet = _get_sheet("Attendance")
    rows = sheet.get_all_values()[1:]
    res = _rows_to_dicts(rows, _ATTENDANCE_COLS)
    
    global_cache.set("Attendance", res)
    return res


def get_attendance_for_student(intern_id: str) -> List[Dict]:
    return [a for a in get_all_attendance() if _match_user_id(a.get("intern_id"), intern_id)]


def get_attendance_for_date(date_str: str) -> List[Dict]:
    return [a for a in get_all_attendance() if a["date"] == date_str]


def upsert_attendance(intern_id: str, department: str, date_str: str, status: str,
                      category: str, linked_task_id: str, notes: str = "") -> Dict:
    """Insert or update a single attendance record."""
    sheet = _get_sheet("Attendance")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        padded = row + [""] * 9
        if (padded[_ATTENDANCE_COLS["intern_id"]] == intern_id and
                padded[_ATTENDANCE_COLS["date"]] == date_str and
                padded[_ATTENDANCE_COLS["linked_task_id"]] == linked_task_id):
            # Update existing
            sheet.update_cell(i, _ATTENDANCE_COLS["status"] + 1, status)
            sheet.update_cell(i, _ATTENDANCE_COLS["marked_at"] + 1, _now_utc_str())
            sheet.update_cell(i, _ATTENDANCE_COLS["notes"] + 1, notes)
            logger.info("Updated attendance intern=%s date=%s status=%s", intern_id, date_str, status)
            global_cache.invalidate("Attendance")
            return {"intern_id": intern_id, "date": date_str, "status": status}

    # Insert new
    att_id = _new_id()
    row = [att_id, intern_id, department, date_str, status, category,
           linked_task_id, _now_utc_str(), notes]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Inserted attendance intern=%s date=%s status=%s", intern_id, date_str, status)
    global_cache.invalidate("Attendance")
    return {"attendance_id": att_id, "intern_id": intern_id, "date": date_str, "status": status}


def override_attendance(intern_id: str, date_str: str, linked_task_id: str,
                        new_status: str, admin_id: str, notes: str = "") -> bool:
    sheet = _get_sheet("Attendance")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        padded = row + [""] * 9
        if (padded[_ATTENDANCE_COLS["intern_id"]] == intern_id and
                padded[_ATTENDANCE_COLS["date"]] == date_str and
                padded[_ATTENDANCE_COLS["linked_task_id"]] == linked_task_id):
            sheet.update_cell(i, _ATTENDANCE_COLS["status"] + 1, new_status)
            sheet.update_cell(i, _ATTENDANCE_COLS["marked_at"] + 1, _now_utc_str())
            sheet.update_cell(i, _ATTENDANCE_COLS["notes"] + 1, f"[Override by {admin_id}] {notes}")
            logger.info("AUDIT: Admin %s overrode attendance intern=%s date=%s → %s",
                        admin_id, intern_id, date_str, new_status)
            global_cache.invalidate("Attendance")
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# WARNINGS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_warnings() -> List[Dict]:
    cached = global_cache.get("Warnings")
    if cached is not None:
        return cached

    sheet = _get_sheet("Warnings")
    rows = sheet.get_all_values()[1:]
    res = _rows_to_dicts(rows, _WARNINGS_COLS)
    
    global_cache.set("Warnings", res)
    return res


def get_warnings_for_student(intern_id: str) -> List[Dict]:
    return [w for w in get_all_warnings() if _match_user_id(w.get("intern_id"), intern_id)]


def get_unacknowledged_warnings() -> List[Dict]:
    return [w for w in get_all_warnings() if w["acknowledged"] != "yes"]


def create_warning(intern_id: str, department: str, date_str: str, reason: str,
                   issued_by: str = "system") -> Dict:
    sheet = _get_sheet("Warnings")
    warning_id = _new_id()
    row = [warning_id, intern_id, department, date_str, reason, issued_by, "no", "active"]
    try:
        sheet.append_row(row, value_input_option="RAW", table_range="A1")
        logger.info("Warning created intern=%s reason=%s by=%s", intern_id, reason, issued_by)
        global_cache.invalidate("Warnings")
    except Exception as e:
        logger.error(f"Failed to create warning for intern {intern_id}: {str(e)}")
        raise ValueError("Could not save warning to Google Sheets. Please try again later.")
        
    return {"warning_id": warning_id, "intern_id": intern_id, "date": date_str,
            "reason": reason, "issued_by": issued_by, "acknowledged": "no", "status": "active"}

def update_warning_status(warning_id: str, status: str, admin_id: str) -> bool:
    sheet = _get_sheet("Warnings")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) > _WARNINGS_COLS["warning_id"] and row[_WARNINGS_COLS["warning_id"]] == warning_id:
            sheet.update_cell(i, _WARNINGS_COLS["status"] + 1, status)
            logger.info("AUDIT: Admin %s changed warning %s status to %s", admin_id, warning_id, status)
            global_cache.invalidate("Warnings")
            return True
    return False

def revoke_warning(warning_id: str, manager_id: str) -> bool:
    sheet = _get_sheet("Warnings")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) > _WARNINGS_COLS["warning_id"] and row[_WARNINGS_COLS["warning_id"]] == warning_id:
            # We can check if it was issued by this manager, but we do that in the route.
            sheet.update_cell(i, _WARNINGS_COLS["status"] + 1, "revoked")
            logger.info("AUDIT: Manager %s revoked warning %s", manager_id, warning_id)
            global_cache.invalidate("Warnings")
            return True
    return False


def acknowledge_warning(warning_id: str) -> bool:
    sheet = _get_sheet("Warnings")
    all_rows = sheet.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) > _WARNINGS_COLS["warning_id"] and row[_WARNINGS_COLS["warning_id"]] == warning_id:
            sheet.update_cell(i, _WARNINGS_COLS["acknowledged"] + 1, "yes")
            global_cache.invalidate("Warnings")
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# LEAVES
# ══════════════════════════════════════════════════════════════════════════════

def get_all_leaves() -> List[Dict]:
    cached = global_cache.get("Leaves")
    if cached is not None:
        return cached

    try:
        sheet = _get_sheet("Leaves")
        rows = sheet.get_all_values()[1:]
        res = _rows_to_dicts(rows, _LEAVES_COLS)
        global_cache.set("Leaves", res)
        return res
    except Exception as e:
        logger.warning(f"Failed to fetch Leaves sheet. Have you created the 'Leaves' tab? Error: {e}")
        return []


def get_leaves_for_student(intern_id: str) -> List[Dict]:
    return [l for l in get_all_leaves() if _match_user_id(l.get("intern_id"), intern_id)]


def get_leaves_for_manager(manager_id: str) -> List[Dict]:
    my_intern_ids = _get_manager_intern_ids(manager_id)
    res = []
    for l in get_all_leaves():
        if _match_user_id(l.get("manager_id"), manager_id):
            res.append(l)
        elif my_intern_ids and any(_match_user_id(l.get("intern_id"), iid) for iid in my_intern_ids):
            if l not in res:
                res.append(l)
    return res


def get_leave_by_id(leave_id: str) -> Optional[Dict]:
    for l in get_all_leaves():
        if l["leave_id"] == leave_id:
            return l
    return None


def create_leave_request(intern_id: str, department: str, manager_id: str, start_date: str, end_date: str, days_requested: int, reason: str, leave_category: str = "") -> Dict:
    try:
        sheet = _get_sheet("Leaves")
    except Exception as e:
        logger.error(f"Failed to access Leaves sheet: {e}")
        raise RuntimeError("Please create a 'Leaves' tab in your Google Sheet first.")
        
    leave_id = _new_id()
    row = [leave_id, intern_id, department, manager_id, start_date, end_date, str(days_requested), reason.strip(), "pending", "", "", "", leave_category]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Leave request created for intern=%s start=%s end=%s", intern_id, start_date, end_date)
    global_cache.invalidate("Leaves")
    return {
        "leave_id": leave_id, "intern_id": intern_id, "manager_id": manager_id,
        "start_date": start_date, "end_date": end_date, "reason": reason,
        "status": "pending", "decided_by": "", "decided_at": "", "remarks": "", "leave_category": leave_category
    }


def update_leave_status(leave_id: str, status: str, decided_by: str, remarks: str = "") -> bool:
    try:
        sheet = _get_sheet("Leaves")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_LEAVES_COLS["leave_id"]] == leave_id:
                sheet.update_cell(i, _LEAVES_COLS["status"] + 1, status)
                sheet.update_cell(i, _LEAVES_COLS["decided_by"] + 1, decided_by)
                sheet.update_cell(i, _LEAVES_COLS["decided_at"] + 1, _now_utc_str())
                sheet.update_cell(i, _LEAVES_COLS["remarks"] + 1, remarks)
                logger.info("Leave %s status updated to %s by %s", leave_id, status, decided_by)
                global_cache.invalidate("Leaves")
                return True
    except Exception as e:
        logger.error(f"Failed to update leave status: {e}")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_reports() -> List[Dict]:
    cached = global_cache.get("Reports")
    if cached is not None:
        return cached

    try:
        sheet = _get_sheet("Reports")
        rows = sheet.get_all_values()[1:]
        res = _rows_to_dicts(rows, _REPORTS_COLS)
        global_cache.set("Reports", res)
        return res
    except Exception as e:
        logger.warning(f"Failed to fetch Reports sheet. Have you created the 'Reports' tab? Error: {e}")
        return []


def get_reports_for_intern(intern_id: str) -> List[Dict]:
    return [r for r in get_all_reports() if r["intern_id"] == intern_id]


def get_reports_for_manager(manager_id: str) -> List[Dict]:
    my_intern_ids = _get_manager_intern_ids(manager_id)
    res = []
    for r in get_all_reports():
        if _match_user_id(r.get("manager_id"), manager_id):
            res.append(r)
        elif my_intern_ids and any(_match_user_id(r.get("intern_id"), iid) for iid in my_intern_ids):
            if r not in res:
                res.append(r)
    return res


def get_report_by_id(report_id: str) -> Optional[Dict]:
    for r in get_all_reports():
        if r["report_id"] == report_id:
            return r
    return None


def create_report(intern_id: str, department: str, manager_id: str, report_type: str, period_start: str, period_end: str, content: str) -> Dict:
    try:
        sheet = _get_sheet("Reports")
    except Exception as e:
        logger.error(f"Failed to access Reports sheet: {e}")
        raise RuntimeError("Please create a 'Reports' tab in your Google Sheet first.")
        
    report_id = _new_id()
    submitted_at = _now_utc_str()
    row = [report_id, intern_id, department, manager_id, report_type, period_start, period_end, content.strip(), submitted_at, "", "", ""]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Report created for intern=%s type=%s", intern_id, report_type)
    global_cache.invalidate("Reports")
    return {
        "report_id": report_id, "intern_id": intern_id, "department": department, "manager_id": manager_id,
        "report_type": report_type, "period_start": period_start, "period_end": period_end,
        "content": content, "submitted_at": submitted_at, "reviewed_by": "", "review_notes": "", "reviewed_at": ""
    }


def review_report(report_id: str, reviewed_by: str, review_notes: str) -> bool:
    try:
        sheet = _get_sheet("Reports")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_REPORTS_COLS["report_id"]] == report_id:
                sheet.update_cell(i, _REPORTS_COLS["reviewed_by"] + 1, reviewed_by)
                sheet.update_cell(i, _REPORTS_COLS["review_notes"] + 1, review_notes)
                sheet.update_cell(i, _REPORTS_COLS["reviewed_at"] + 1, _now_utc_str())
                logger.info("Report %s reviewed by %s", report_id, reviewed_by)
                global_cache.invalidate("Reports")
                return True
    except Exception as e:
        logger.error(f"Failed to review report: {e}")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE (Legacy/Admin only)
# ══════════════════════════════════════════════════════════════════════════════

def get_all_performance_reports() -> List[Dict]:
    cached = global_cache.get("Performance")
    if cached is not None:
        return cached

    _ensure_sheet_columns("Performance")
    sheet = _get_sheet("Performance")
    rows = sheet.get_all_values()[1:]
    res = _rows_to_dicts(rows, _PERFORMANCE_COLS)
    
    all_subs = get_all_submissions()
    all_tasks = get_all_tasks()

    def _to_float(val, default=8.0):
        try:
            if val is None or str(val).strip() in ("", "-", "N/A", "None"):
                return default
            return float(str(val).strip())
        except Exception:
            return default

    def _to_int(val, default=0):
        try:
            if val is None or str(val).strip() in ("", "-", "N/A", "None"):
                return default
            return int(float(str(val).strip()))
        except Exception:
            return default

    for p in res:
        p["report_type"] = p.get("report_type") or f"Monthly ({p.get('period_start', '')} to {p.get('period_end', '')})"
        p["rating"] = p.get("rating") or p.get("grade_band") or f"{p.get('total_score', '')}/30"
        p["created_at"] = p.get("created_at") or p.get("submitted_at", "")
        # ── Tasks Completed / Assigned Tracking ──
        p_start = str(p.get("period_start", ""))[:10]
        p_end = str(p.get("period_end", ""))[:10]
        i_id = str(p.get("intern_id", ""))
        
        comp_cnt = 0
        ass_cnt = 0
        override_raw = str(p.get("old_task_completion", "")).strip()
        if "/" in override_raw:
            try:
                p1, p2 = override_raw.split("/", 1)
                comp_cnt = _to_int(p1, 0)
                ass_cnt = _to_int(p2, 0)
            except Exception:
                pass
                
        if ass_cnt == 0 and comp_cnt == 0:
            comp_cnt = sum(1 for s in all_subs if str(s.get("intern_id")) == i_id and p_start <= str(s.get("submitted_at", ""))[:10] <= p_end and str(s.get("status")).lower() in ("reviewed", "approved", "completed"))
            ass_cnt = sum(1 for t in all_tasks if str(t.get("assigned_to")) == i_id and p_start <= str(t.get("due_date", "") or t.get("created_at", ""))[:10] <= p_end)
            if ass_cnt == 0 and comp_cnt > 0:
                ass_cnt = comp_cnt
            
            if ass_cnt == 0 and comp_cnt == 0:
                tc_raw = str(p.get("task_completion", "")).strip()
                if "/" in tc_raw:
                    try:
                        p1, p2 = tc_raw.split("/", 1)
                        comp_cnt = _to_int(p1, 0)
                        ass_cnt = _to_int(p2, 0)
                    except Exception:
                        pass
                else:
                    val = _to_int(tc_raw, 0)
                    if val > 0:
                        comp_cnt = val
                        ass_cnt = 10 if val <= 10 else val
                        
        p["derived_tasks_completed"] = comp_cnt
        p["derived_tasks_assigned"] = ass_cnt
        p["derived_completion_pct"] = f"{round((comp_cnt / ass_cnt) * 100, 1)}%" if ass_cnt > 0 else "0%"

        # ── Centralized Derived 5-Field Summary (Initiative, Task Completion, Behav Index, Obtained Score, Standing) ──
        summary = get_performance_summary(p)
        p["summary"] = summary
        p["derived_initiative"] = summary["initiative_grade"]
        p["derived_communication"] = summary["communication"]
        p["derived_behavioral_index"] = summary["behavioral_index"]
        p["derived_obtained_score"] = summary["obtained_score"]
        p["derived_standing"] = summary["milestone_standing"]
        
        # Strictly synchronize total_score to the 3-criteria sum (out of 30) so all tables and views match 100%
        try:
            init_v = float(p.get("initiative") or 0)
            task_v = float(p.get("task_completion") or 0)
            disc_v = float(p.get("discipline") or 0)
            three_sum = int(round(init_v + task_v + disc_v))
            if three_sum > 0:
                p["total_score"] = three_sum
            elif summary["obtained_score"] != "–" and float(summary["obtained_score"]) > 0:
                p["total_score"] = int(round(float(summary["obtained_score"]) * 3.0))
        except (ValueError, TypeError):
            pass
    
    global_cache.set("Performance", res)
    return res

def _get_manager_intern_ids(manager_id: str) -> set:
    try:
        from services import supabase_service as supa
        my_interns = supa.get_profiles_by_manager(manager_id)
        my_ids = {str(i.get("id", "")).strip().lower() for i in my_interns} | {str(i.get("intern_id", "")).strip().lower() for i in my_interns} | {str(i.get("email", "")).strip().lower() for i in my_interns}
        my_ids.discard("")
        return my_ids
    except Exception:
        return set()

def _match_user_id(val1, val2) -> bool:
    if val1 is None or val2 is None:
        return False
    s1 = str(val1).strip().lower()
    s2 = str(val2).strip().lower()
    if not s1 or not s2:
        return False
    if s1 == s2:
        return True
    try:
        from services import supabase_service as supa
        profiles = supa.get_all_profiles()
        ids1 = {s1}
        ids2 = {s2}
        for u in profiles:
            u_ids = {str(u.get("id", "")).strip().lower(), str(u.get("intern_id", "")).strip().lower(), str(u.get("rgt_id", "")).strip().lower(), str(u.get("email", "")).strip().lower()}
            u_ids.discard("")
            if s1 in u_ids:
                ids1.update(u_ids)
            if s2 in u_ids:
                ids2.update(u_ids)
        return bool(ids1 & ids2)
    except Exception:
        return False

def get_performance_reports_for_student(intern_id: str) -> List[Dict]:
    """Get all performance reports for a student, matching by UUID (direct) or via profile cross-reference."""
    intern_id_clean = str(intern_id).strip().lower()
    all_reports = get_all_performance_reports()
    
    # Build the intern's full identity set (UUID, rgt_id, email) for robust matching
    intern_ids = {intern_id_clean}
    try:
        from services import supabase_service as supa
        profile = supa.get_profile(intern_id)
        if profile:
            for field in ("id", "intern_id", "rgt_id", "email"):
                val = str(profile.get(field, "")).strip().lower()
                if val:
                    intern_ids.add(val)
    except Exception as e:
        logger.warning("Could not enrich intern ID set for %r: %s", intern_id, e)
    
    res = []
    for p in all_reports:
        p_intern = str(p.get("intern_id", "")).strip().lower()
        if p_intern and p_intern in intern_ids:
            res.append(p)
    
    logger.info(
        "Fetched %d performance reports for intern_id=%r (identity set: %s)",
        len(res), intern_id, intern_ids
    )
    return res

def get_performance_reports_for_manager(manager_id: str) -> List[Dict]:
    try:
        from services import supabase_service as supa
        my_interns = supa.get_profiles_by_manager(manager_id)
        my_intern_ids = {str(i.get("id", "")).strip().lower() for i in my_interns} | {str(i.get("intern_id", "")).strip().lower() for i in my_interns} | {str(i.get("email", "")).strip().lower() for i in my_interns}
        my_intern_ids.discard("")
    except Exception:
        my_intern_ids = set()

    res = []
    for p in get_all_performance_reports():
        if _match_user_id(p.get("manager_id"), manager_id):
            res.append(p)
        elif my_intern_ids and any(_match_user_id(p.get("intern_id"), iid) for iid in my_intern_ids):
            if p not in res:
                res.append(p)
    return res

def create_performance_report(intern_id: str, manager_id: str, period_start: str, period_end: str,
                              technical_skill: int, communication: int, discipline: int, task_completion: int,
                              initiative: int, teamwork: int, code_quality: int, total_score: int, 
                              grade_band: str, strengths: str, areas_improvement: str, overall_comments: str,
                              tasks_completed: Optional[int] = None, tasks_assigned: Optional[int] = None) -> Dict:
    _ensure_sheet_columns("Performance")
    sheet = _get_sheet("Performance")
    report_id = _new_id()
    created_at = _now_utc_str()
    
    override_str = ""
    if tasks_completed is not None and tasks_assigned is not None and str(tasks_completed).strip() != "" and str(tasks_assigned).strip() != "":
        override_str = f"{tasks_completed}/{tasks_assigned}"
        
    row = [
        report_id, intern_id, manager_id, period_start, period_end,
        str(technical_skill if technical_skill else (task_completion if task_completion else 0)), # 5: work_quality
        override_str if override_str else str(task_completion if task_completion else 0), # 6: old_task_completion
        str(initiative if initiative else 0), # 7: learning_ability
        str(teamwork if teamwork else 0), # 8: old_teamwork
        str(discipline if discipline else 0), # 9: old_discipline
        str(communication if communication else (discipline if discipline else 0)), # 10: behaviour
        str(total_score), # 11: overall
        total_score, grade_band, strengths.strip(), areas_improvement.strip(), overall_comments.strip(), created_at,
        "", "False", "", # 18-20: edit_reason, intern_acknowledged, intern_ack_date
        technical_skill, communication, discipline, task_completion, initiative, teamwork, code_quality
    ]
    logger.info(
        "Creating performance report: report_id=%s intern_id=%r manager_id=%r period=%s to %s total=%s grade=%s",
        report_id, intern_id, manager_id, period_start, period_end, total_score, grade_band
    )
    try:
        sheet.append_row(row, value_input_option="RAW", table_range="A1")
        global_cache.invalidate("Performance")
        logger.info("Performance report %s created successfully for intern_id=%r", report_id, intern_id)
    except Exception as e:
        logger.error(f"Failed to create performance report for intern {intern_id}: {str(e)}")
        raise ValueError(f"Could not save performance report to Google Sheets. Please try again later. ({str(e)})")
    
    return {"report_id": report_id}

def update_performance_report(report_id: str, updates: Dict) -> bool:
    _ensure_sheet_columns("Performance")
    sheet = _get_sheet("Performance")
    rows = sheet.get_all_values()
    header = rows[0]
    
    for idx, row in enumerate(rows[1:], start=1):
        if row and row[0] == report_id:
            while len(row) < 28:
                row.append("")
            # Keep legacy columns in sync when updating new rubric columns
            if "technical_skill" in updates or "task_completion" in updates:
                updates["work_quality"] = str(updates.get("technical_skill", updates.get("task_completion", row[5])))
            if "initiative" in updates:
                updates["learning_ability"] = str(updates["initiative"])
            if "teamwork" in updates:
                updates["old_teamwork"] = str(updates["teamwork"])
            if "discipline" in updates:
                updates["old_discipline"] = str(updates["discipline"])
                if "communication" not in updates:
                    updates["behaviour"] = str(updates["discipline"])
            if "communication" in updates:
                updates["behaviour"] = str(updates["communication"])
            if "total_score" in updates:
                updates["overall"] = str(updates["total_score"])
                
            for key, value in updates.items():
                if key in _PERFORMANCE_COLS:
                    col_idx = _PERFORMANCE_COLS[key]
                    row[col_idx] = str(value)
            sheet.update(f"A{idx+1}:AB{idx+1}", [row[:28]], value_input_option="RAW")
            global_cache.invalidate("Performance")
            return True
    return False
# ══════════════════════════════════════════════════════════════════════════════
# INVITES
# ══════════════════════════════════════════════════════════════════════════════

def create_invite(email: str, department: str, token: str, admin_id: str) -> None:
    from datetime import timedelta
    sheet = _get_sheet("Invites")
    invite_id = _new_id()
    created_at = _now_utc_str()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    row = [
        invite_id,
        email,
        department,
        "manager",
        token,
        admin_id,
        created_at,
        expires_at,
        "no"
    ]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Created invite for %s", email)

def get_invite_by_token(token: str) -> Optional[Dict]:
    try:
        sheet = _get_sheet("Invites")
        rows = sheet.get_all_values()[1:]
        invites = _rows_to_dicts(rows, _INVITES_COLS)
        for inv in invites:
            if inv["token"] == token:
                inv["used"] = str(inv.get("used", "")).lower() in ("yes", "true", "1")
                return inv
        return None
    except Exception as e:
        logger.error(f"Error fetching invite by token: {e}")
        return None

def mark_invite_used(token: str) -> bool:
    try:
        sheet = _get_sheet("Invites")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_INVITES_COLS["token"]] == token:
                sheet.update_cell(i, _INVITES_COLS["used"] + 1, "yes")
                return True
        return False
    except Exception as e:
        logger.error(f"Error marking invite as used: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# MANAGER NOTES
# ══════════════════════════════════════════════════════════════════════════════

def get_manager_notes(intern_id: str) -> List[Dict]:
    try:
        sheet = _get_sheet("ManagerNotes")
        rows = sheet.get_all_values()[1:]
        all_notes = _rows_to_dicts(rows, _MANAGER_NOTES_COLS)
        intern_notes = [n for n in all_notes if n["intern_id"] == intern_id]
        # Sort by timestamp descending
        intern_notes.sort(key=lambda x: x["timestamp"], reverse=True)
        return intern_notes
    except Exception as e:
        logger.error(f"Error fetching manager notes for {intern_id}: {e}")
        return []

def create_manager_note(intern_id: str, manager_id: str, content: str) -> Dict:
    try:
        sheet = _get_sheet("ManagerNotes")
    except Exception:
        # If sheet doesn't exist, create it (fallback for initialization)
        client = _get_client()
        ss = client.open_by_key(os.environ["SPREADSHEET_ID"])
        sheet = ss.add_worksheet(title="ManagerNotes", rows=100, cols=20)
        sheet.insert_row(SHEET_HEADERS["ManagerNotes"], index=1)
        
    note_id = _new_id()
    timestamp = _now_utc_str()
    row = [note_id, intern_id, manager_id, timestamp, content.strip()]
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Manager %s created note %s for intern %s", manager_id, note_id, intern_id)
    return {
        "note_id": note_id,
        "intern_id": intern_id,
        "manager_id": manager_id,
        "timestamp": timestamp,
        "content": content.strip()
    }

# ══════════════════════════════════════════════════════════════════════════════
# HOLIDAYS & SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_settings() -> Dict[str, str]:
    cached = global_cache.get("Settings")
    if cached is not None:
        return cached

    try:
        sheet = _get_sheet("Settings")
        rows = sheet.get_all_values()[1:]
        res = {row[_SETTINGS_COLS["key"]]: row[_SETTINGS_COLS["value"]] for row in rows if len(row) > 1}
        global_cache.set("Settings", res)
        return res
    except Exception as e:
        logger.warning(f"Failed to fetch Settings sheet: {e}")
        return {}

def update_setting(key: str, value: str) -> bool:
    try:
        sheet = _get_sheet("Settings")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_SETTINGS_COLS["key"]] == key:
                sheet.update_cell(i, _SETTINGS_COLS["value"] + 1, value)
                global_cache.invalidate("Settings")
                return True
        # If not found, append it
        sheet.append_row([key, value], table_range="A1")
        global_cache.invalidate("Settings")
        return True
    except Exception as e:
        logger.error(f"Failed to update setting {key}: {e}")
        return False

def get_all_announcements() -> List[Dict]:
    import json
    settings = get_all_settings()
    raw = settings.get("announcements", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return [
        {
            "id": "1",
            "title": "Monthly Performance Review Window Open",
            "category": "Evaluation Cycle",
            "status": "Active Now",
            "color": "#7c3aed",
            "content": "All managers are requested to complete formal evaluation rubrics and grading for their assigned interns. Ensure all 7 metric criteria (Discipline, Code Quality, Teamwork, etc.) are recorded.",
            "created_at": "2026-07-25"
        },
        {
            "id": "2",
            "title": "Global Live Team Search & Action Hub Released",
            "category": "Portal Upgrade",
            "status": "System Notice",
            "color": "#16a34a",
            "content": "The manager portal has been upgraded with instant search by Name, Email ID, or Intern ID in the top navigation bar, allowing one-click access to profile histories, attendance marking, and task assignments.",
            "created_at": "2026-07-25"
        },
        {
            "id": "3",
            "title": "Weekend Attendance & Leave Exemption Protocol",
            "category": "Policy Reminder",
            "status": "HR Guidelines",
            "color": "#2563eb",
            "content": "Official weekends and national holidays are automatically exempt from leave deductions. Daily progress reports should be reviewed within 48 hours of submission to keep intern records current.",
            "created_at": "2026-07-25"
        }
    ]

def save_announcements(announcements: List[Dict]) -> bool:
    import json
    return update_setting("announcements", json.dumps(announcements))

# ══════════════════════════════════════════════════════════════════════════════
# HOLIDAYS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_holidays() -> List[Dict]:
    cached = global_cache.get("Holidays")
    if cached is not None:
        return cached

    try:
        _ensure_sheet_columns("Holidays")
        sheet = _get_sheet("Holidays")
        rows = sheet.get_all_values()[1:]
        res = _rows_to_dicts(rows, _HOLIDAYS_COLS)
        # Filter out empty rows
        res = [r for r in res if r.get("date") and r.get("name")]
        global_cache.set("Holidays", res)
        return res
    except Exception as e:
        logger.warning(f"Failed to fetch Holidays sheet: {e}")
        return []

def add_holiday(date_str: str, name: str) -> bool:
    try:
        _ensure_sheet_columns("Holidays")
        sheet = _get_sheet("Holidays")
        
        # Manually find the next empty row to avoid append_row jumping to row 1000
        all_rows = sheet.get_all_values()
        next_row_idx = len(all_rows) + 1
        
        # update the cells directly
        sheet._worksheet.update(f"A{next_row_idx}:B{next_row_idx}", [[date_str, name]], value_input_option="RAW")
        
        global_cache.invalidate("Holidays")
        return True
    except Exception as e:
        logger.error(f"Failed to add holiday: {e}")
        return False

def delete_holiday(date_str: str) -> bool:
    try:
        sheet = _get_sheet("Holidays")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_HOLIDAYS_COLS["date"]] == date_str:
                sheet.delete_rows(i)
                global_cache.invalidate("Holidays")
                return True
        return False
    except Exception as e:
        logger.error(f"Failed to delete holiday: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════

def get_all_audit_logs() -> List[Dict]:
    try:
        sheet = _get_sheet("AuditLog")
        rows = sheet.get_all_values()[1:]
        res = _rows_to_dicts(rows, _AUDIT_COLS)
        return res
    except Exception as e:
        logger.warning(f"Failed to fetch AuditLog sheet: {e}")
        return []

def log_audit(actor_id: str, action: str, details: str) -> None:
    # We also log to standard stdout
    logger.info(f"AUDIT: [{actor_id}] {action} - {details}")
    try:
        sheet = _get_sheet("AuditLog")
        sheet.append_row([_now_utc_str(), actor_id, action, details], table_range="A1")
    except Exception as e:
        logger.error(f"Failed to write to AuditLog sheet: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# EXTENSIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_extensions() -> List[Dict]:
    cached = global_cache.get("Extensions")
    if cached is not None:
        return cached
    try:
        sheet = _get_sheet("Extensions")
        rows = sheet.get_all_values()[1:]
        res = _rows_to_dicts(rows, _EXTENSIONS_COLS)
        global_cache.set("Extensions", res)
        return res
    except Exception as e:
        logger.warning(f"Failed to fetch Extensions sheet: {e}")
        return []

def get_extensions_for_manager(manager_id: str) -> List[Dict]:
    my_intern_ids = _get_manager_intern_ids(manager_id)
    res = []
    for ext in get_all_extensions():
        if _match_user_id(ext.get("manager_id"), manager_id):
            res.append(ext)
        elif my_intern_ids and any(_match_user_id(ext.get("intern_id"), iid) for iid in my_intern_ids):
            if ext not in res:
                res.append(ext)
    return res

def get_extensions_for_student(intern_id: str) -> List[Dict]:
    return [ext for ext in get_all_extensions() if _match_user_id(ext.get("intern_id"), intern_id)]

def create_extension_request(intern_id: str, manager_id: str, current_duration: int, requested_months: int, reason: str) -> Dict:
    try:
        sheet = _get_sheet("Extensions")
    except Exception as e:
        logger.error(f"Failed to access Extensions sheet: {e}")
        raise RuntimeError("Please create an 'Extensions' tab in your Google Sheet first.")
        
    ext_id = _new_id()
    created_at = _now_utc_str()
    status = "pending"
    
    row = [
        ext_id, intern_id, manager_id, str(current_duration),
        str(requested_months), reason, status, created_at, ""
    ]
    
    sheet.append_row(row, value_input_option="RAW", table_range="A1")
    logger.info("Extension request created for intern %s", intern_id)
    global_cache.invalidate("Extensions")
    return {
        "extension_id": ext_id, "intern_id": intern_id, "manager_id": manager_id,
        "current_duration": current_duration, "requested_months": requested_months,
        "reason": reason, "status": status, "created_at": created_at, "decision_notes": ""
    }

def update_extension_status(extension_id: str, status: str, decision_notes: str = "") -> bool:
    try:
        sheet = _get_sheet("Extensions")
        all_rows = sheet.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):
            if row[_EXTENSIONS_COLS["extension_id"]] == extension_id:
                sheet.update_cell(i, _EXTENSIONS_COLS["status"] + 1, status)
                sheet.update_cell(i, _EXTENSIONS_COLS["decision_notes"] + 1, decision_notes)
                logger.info("Extension %s marked as %s", extension_id, status)
                global_cache.invalidate("Extensions")
                return True
        return False
    except Exception as e:
        logger.error(f"Failed to update extension status: {e}")
        return False
