# Centralized Performance Summary Calculations for RGTvertex
# Ensures 1:1 parity between Intern View, Manager View, and PDF Export.

def _to_float(val, default=0.0):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _to_int(val, default=0):
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

def get_performance_summary(report):
    """
    Takes a raw performance report dictionary (from sheets/db or cycle item)
    and returns a standardized summary dictionary containing the 5 derived PDF fields.
    
    If report is None or empty (e.g., month with no evaluation issued yet),
    returns placeholder values matching the official PDF specification: 0, 0%, –, –.
    """
    if not report:
        return {
            "has_report": False,
            "initiative_grade": "0",
            "task_completion": "0",
            "behavioral_index": "–",
            "obtained_score": "–",
            "milestone_standing": "–",
            "tasks_completed": 0,
            "tasks_assigned": 0,
            "completion_pct": "0%",
            "communication": "–"
        }
    
    # Extract criteria scores — use explicit None check so a score of 0 doesn't fall through to legacy fields
    def _first_valid(*vals):
        """Return the first value that is not None and not empty string."""
        for v in vals:
            if v is not None and str(v).strip() not in ("", "None", "N/A", "-", "\u2013"):
                # Return as-is; _to_float will handle type conversion
                return v
        return None
    
    init_raw  = _first_valid(report.get("initiative"), report.get("derived_initiative"))
    task_raw  = _first_valid(report.get("task_completion"))   # column 24 — do NOT fall back to old_task_completion (it's a "5/10" string)
    disc_raw  = _first_valid(report.get("discipline"), report.get("old_discipline"))
    team_raw  = _first_valid(report.get("teamwork"), report.get("old_teamwork"))
    comm_raw  = _first_valid(report.get("communication"), report.get("derived_communication"))
    tech_raw  = _first_valid(report.get("technical_skill"))
    code_raw  = _first_valid(report.get("code_quality"))

    init_val = _to_float(init_raw, 0.0)
    task_val = _to_float(task_raw, 0.0)
    disc_val = _to_float(disc_raw, 0.0)
    team_val = _to_float(team_raw, 0.0)
    comm_val = _to_float(comm_raw, 0.0)
    tech_val = _to_float(tech_raw, 0.0)
    code_val = _to_float(code_raw, 0.0)
    
    # 1. Initiative Grade (used directly)
    initiative_grade = round(init_val, 1)
    if initiative_grade % 1 == 0:
        initiative_grade = int(initiative_grade)
        
    # 2. Task Completion (used directly)
    task_completion = round(task_val, 1)
    if task_completion % 1 == 0:
        task_completion = int(task_completion)
        
    # 3. Behavioral Index (discipline/conduct, or average of discipline + teamwork if both distinct)
    if team_val == 0.0 or team_val == disc_val:
        behavioral_index = round(disc_val, 1)
    else:
        behavioral_index = round((disc_val + team_val) / 2.0, 1)
    
    # 4. Obtained Score (out of 10) - consistent across all views and PDF
    raw_tot = _to_float(report.get("total_score"), 0.0)
    if raw_tot > 70.0:
        obtained_score = round(raw_tot / 10.0, 1)
    elif raw_tot > 50.0:
        obtained_score = round(raw_tot / 7.0, 1)
    elif raw_tot > 30.0:
        obtained_score = round(raw_tot / 5.0, 1)
    elif raw_tot > 0.0:
        obtained_score = round(raw_tot / 3.0, 1)
    else:
        obtained_score = round((init_val + task_val + disc_val) / 3.0, 1)
        
    # 5. Milestone Standing
    milestone_standing = "PASS" if obtained_score >= 5.0 else "FAIL"
    
    # Task tracking counts
    comp_cnt = _to_int(report.get("derived_tasks_completed") or report.get("tasks_completed"), 0)
    ass_cnt = _to_int(report.get("derived_tasks_assigned") or report.get("tasks_assigned"), 0)
    comp_pct = str(report.get("derived_completion_pct") or ("0%" if ass_cnt == 0 else f"{round((comp_cnt/ass_cnt)*100, 1)}%"))
    
    comm_format = round(comm_val, 1)
    if comm_format % 1 == 0:
        comm_format = int(comm_format)

    return {
        "has_report": True,
        "initiative_grade": initiative_grade,
        "task_completion": task_completion,
        "behavioral_index": behavioral_index,
        "obtained_score": obtained_score,
        "milestone_standing": milestone_standing,
        "tasks_completed": comp_cnt,
        "tasks_assigned": ass_cnt,
        "completion_pct": comp_pct,
        "communication": comm_format
    }
