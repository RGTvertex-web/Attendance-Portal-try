import logging
from datetime import datetime
from dateutil.relativedelta import relativedelta
from config import UTC

logger = logging.getLogger(__name__)

def get_internship_cycle(joining_date_str: str, duration_months: int, created_at_str: str = None) -> dict:
    """
    Computes the internship cycle details.
    Uses dateutil.relativedelta to correctly handle month arithmetic (e.g., Jan 31 -> Feb 28).
    
    Returns:
    - current_month: (1-indexed) which month they are currently in. If completed, might be > duration_months.
    - completed_months: number of fully completed months.
    - remaining_months: duration_months - current_month (or 0 if over).
    - current_cycle_start: string (YYYY-MM-DD)
    - current_cycle_end: string (YYYY-MM-DD)
    - expected_completion_date: string (YYYY-MM-DD)
    - all_cycles: list of dicts {month: X, start: Y, end: Z}
    """
    is_estimated = False
    if not joining_date_str and created_at_str:
        joining_date_str = created_at_str[:10]
        logger.info(f"No joining_date provided. Falling back to created_at: {joining_date_str}")
        is_estimated = True
    elif not joining_date_str:
        joining_date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        is_estimated = True

    try:
        joining_date = datetime.strptime(str(joining_date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        joining_date = datetime.now(UTC).date()
        logger.error(f"Invalid joining_date '{joining_date_str}'. Defaulting to today.")

    try:
        if duration_months:
            duration_months = int(float(str(duration_months).strip().split()[0]))
        else:
            duration_months = 0
    except (ValueError, TypeError, IndexError):
        duration_months = 0
    
    all_cycles = []
    expected_completion_date = joining_date + relativedelta(months=duration_months) - relativedelta(days=1)
    
    current_date = datetime.now(UTC).date()
    
    # Compute all theoretical cycles up to duration
    for m in range(1, duration_months + 1):
        c_start = joining_date + relativedelta(months=m-1)
        c_end = joining_date + relativedelta(months=m) - relativedelta(days=1)
        all_cycles.append({
            "month": m,
            "start": c_start.strftime("%Y-%m-%d"),
            "end": c_end.strftime("%Y-%m-%d")
        })
        
    # Determine which cycle we are currently in
    current_month = 1
    current_cycle_start = joining_date
    current_cycle_end = joining_date + relativedelta(months=1) - relativedelta(days=1)
    completed_months = 0

    if current_date < joining_date:
        # Intern hasn't started yet
        pass
    else:
        # Find which month we're in
        for m in range(1, 999): # Safe upper bound
            c_start = joining_date + relativedelta(months=m-1)
            c_end = joining_date + relativedelta(months=m) - relativedelta(days=1)
            if c_start <= current_date <= c_end:
                current_month = m
                current_cycle_start = c_start
                current_cycle_end = c_end
                completed_months = m - 1
                break
            if current_date > c_end:
                completed_months = m

    remaining_months = max(0, duration_months - current_month + 1)
    if current_date > (joining_date + relativedelta(months=duration_months) - relativedelta(days=1)):
        remaining_months = 0
        
    return {
        "joining_date": joining_date.strftime("%Y-%m-%d"),
        "current_month": current_month,
        "completed_months": completed_months,
        "remaining_months": remaining_months,
        "current_cycle_start": current_cycle_start.strftime("%Y-%m-%d"),
        "current_cycle_end": current_cycle_end.strftime("%Y-%m-%d"),
        "expected_completion_date": expected_completion_date.strftime("%Y-%m-%d") if duration_months > 0 else "N/A",
        "all_cycles": all_cycles,
        "duration_months": duration_months,
        "is_estimated_joining_date": is_estimated
    }
