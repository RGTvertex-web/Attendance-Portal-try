import io
import os
import html
import logging
from datetime import datetime, date, timezone
import pytz
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas
from services import sheets_service as ss
from services.internship_cycle_service import get_internship_cycle
from services.performance_service import get_performance_summary
import config

logger = logging.getLogger(__name__)

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to add headers, footers, QR code, and 'Page X of Y' on every page.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        width, height = self._pagesize
        
        # Draw bottom separator line
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.75)
        self.line(25, 48, width - 25, 48)
        
        # Left-aligned verification date (no ID line, no page number)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748b"))
        v_date = getattr(self, "verification_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        self.drawString(25, 35, f"Verification Date: {v_date}")
        
        # Draw QR Code in center-right footer
        intern_id = getattr(self, "intern_id", "RGTV-INT-0000")
        verify_url = getattr(self, "verify_url", f"/verify/{intern_id}")
        try:
            from reportlab.graphics.barcode import qr
            from reportlab.graphics.shapes import Drawing
            from reportlab.graphics import renderPDF
            qr_code = qr.QrCodeWidget(verify_url)
            bounds = qr_code.getBounds()
            width_qr = bounds[2] - bounds[0]
            height_qr = bounds[3] - bounds[1]
            d = Drawing(35, 35, transform=[35/width_qr, 0, 0, 35/height_qr, 0, 0])
            d.add(qr_code)
            renderPDF.draw(d, self, width - 25 - 35, 10)
        except Exception as e:
            logger.warning("Could not draw QR code on PDF: %s", e)
            self.setFont("Helvetica-Oblique", 7)
            self.drawCentredString(width / 2.0, 30, f"Verify: {verify_url}")
            
        self.restoreState()


def compute_working_days(start_str: str, end_str: str) -> int:
    """Compute working days (Monday-Friday) between start_str and min(end_str, today)."""
    if not start_str or not end_str:
        return 0
    try:
        start_date = datetime.strptime(start_str[:10], "%Y-%m-%d").date()
        end_date = datetime.strptime(end_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
        
    today = datetime.now(timezone.utc).date()
    effective_end = min(end_date, today)
    if effective_end < start_date:
        return 0
        
    holidays_data = ss.get_all_holidays()
    holiday_dates = {h.get("date", "")[:10] for h in holidays_data}
    
    working_days = 0
    curr = start_date
    while curr <= effective_end:
        if curr.weekday() < 5 and curr.strftime("%Y-%m-%d") not in holiday_dates:
            working_days += 1
        curr = date.fromordinal(curr.toordinal() + 1)
    return working_days


def safe_str(val, default="N/A") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def escape_xml(val, default="N/A") -> str:
    return html.escape(safe_str(val, default=default))


def safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return default
    try:
        s = s.replace(",", ".").split()[0]
        return float(s)
    except (ValueError, TypeError, IndexError):
        return default


def safe_int(val, default=0) -> int:
    if val is None:
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    s = str(val).strip()
    if not s:
        return default
    try:
        s = s.replace(",", ".").split()[0]
        return int(float(s))
    except (ValueError, TypeError, IndexError):
        return default

def get_graduation_verdict(status: str, deact_reason: str, duration: int, valid_scores: list, cum_score: float, att_rate: float, min_att: float, min_perf: float) -> tuple[str, str]:
    """Helper to determine graduation verdict and color to avoid duplicated logic."""
    status = status.lower()
    if status == "inactive" and deact_reason:
        return "TERMINATED", "#991b1b"
    elif status in ("completed", "graduated") or (len(valid_scores) >= duration and cum_score >= min_perf and att_rate >= min_att):
        return "COMPLETED", "#15803d" # or "#166534"
    else:
        return "IN PROCESS", "#b45309"

def generate_internship_report_pdf(intern_profile: dict, host_url: str = "") -> bytes:
    """
    Generates the Official RGTvertex Internship Performance & Evaluation Report PDF.
    Returns the raw PDF bytes.
    """
    try:
        return _generate_internship_report_pdf_inner(intern_profile, host_url)
    except Exception as e:
        logger.error(
            "PDF generation failed for intern %r: %s",
            intern_profile.get("id") if intern_profile else "unknown",
            e, exc_info=True
        )
        raise


def _generate_internship_report_pdf_inner(intern_profile: dict, host_url: str = "") -> bytes:
    buffer = io.BytesIO()

    styles = getSampleStyleSheet()

    def style(name, **kw):
        base = kw.pop("parent", styles["Normal"])
        return ParagraphStyle(name, parent=base, **kw)

    # ── Styles ──────────────────────────────────────────────────────────────
    s_normal     = style("N",  fontSize=8,   textColor=colors.HexColor("#111111"), fontName="Helvetica")
    s_bold       = style("B",  fontSize=8,   textColor=colors.HexColor("#111111"), fontName="Helvetica-Bold")
    s_faint      = style("F",  fontSize=7,   textColor=colors.HexColor("#737373"), fontName="Helvetica")
    s_th         = style("TH", fontSize=7,   textColor=colors.white,               fontName="Helvetica-Bold", alignment=1)
    s_td_c       = style("TC", fontSize=7.5, textColor=colors.HexColor("#111111"), fontName="Helvetica",      alignment=1)
    s_td_bc      = style("TBC",fontSize=7.5, textColor=colors.HexColor("#111111"), fontName="Helvetica-Bold", alignment=1)
    s_sec        = style("S",  fontSize=7.5, textColor=colors.white,               fontName="Helvetica-Bold")
    s_disc       = style("D",  fontSize=6,   textColor=colors.HexColor("#4b4b4b"), fontName="Helvetica", leading=8)
    s_banner_t   = style("BT", fontSize=13,  textColor=colors.white,               fontName="Helvetica-Bold", alignment=1)
    s_banner_s   = style("BS", fontSize=8,   textColor=colors.HexColor("#b0b0b0"), fontName="Helvetica",      alignment=1, spaceAfter=2)
    s_tagline    = style("TG", fontSize=7.5, textColor=colors.HexColor("#737373"), fontName="Helvetica",      alignment=1, spaceAfter=2)

    PAGE_W = A4[0] - 50   # effective content width (25mm margin each side)

    story = []

    # ── Logo ────────────────────────────────────────────────────────────────
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_path = None
    for p in [
        os.path.join(base_dir, "static", "img", "brand", "RGTlogo_only.jpeg"),
        os.path.join(base_dir, "static", "img", "brand", "RGTlogo.jpeg"),
        "static/img/brand/RGTlogo_only.jpeg",
        "static/img/brand/RGTlogo.jpeg",
    ]:
        if os.path.exists(p):
            logo_path = p
            break

    if logo_path:
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(logo_path).getSize()
            tw = 2.4 * cm
            img = Image(logo_path, width=tw, height=tw * (ih / float(iw or 1)))
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 2))
        except Exception as e:
            logger.warning("Could not add logo: %s", e)

    story.append(Paragraph("Reliable AI. Scalable Growth. Intelligent Technology", s_tagline))

    # ── Header Banner ───────────────────────────────────────────────────────
    banner = Table([[
        [Paragraph("RGTVERTEX INTERNSHIP PROGRAMME – 2026", s_banner_s),
         Paragraph("OFFICIAL INTERNSHIP PERFORMANCE & EVALUATION REPORT", s_banner_t)]
    ]], colWidths=[PAGE_W])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#111111")),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(banner)
    story.append(Spacer(1, 8))

    # ── Data ─────────────────────────────────────────────────────────────────
    if not intern_profile or not isinstance(intern_profile, dict):
        intern_profile = {}

    # Fallback to Google Sheets for fields that might not be in Supabase schema
    try:
        from services.sheets_service import get_all_users
        all_users = get_all_users()
        sheet_user = next((u for u in all_users if u.get("id") == intern_profile.get("id")), {})
    except Exception:
        sheet_user = {}

    intern_id     = safe_str(intern_profile.get("intern_id") or intern_profile.get("rgt_id") or f"RGTV-INT-{str(intern_profile.get('id','0000'))[:4]}", "RGTV-INT-0000")
    name          = safe_str(intern_profile.get("name"),          "Unknown Intern")
    email         = safe_str(intern_profile.get("email"),         "N/A")
    phone         = safe_str(intern_profile.get("phone") or sheet_user.get("phone"), "N/A")
    duration      = safe_int(intern_profile.get("internship_duration_months"), 3) or 3
    department    = safe_str(intern_profile.get("department"),    "General")
    college_name  = safe_str(intern_profile.get("college_name") or sheet_user.get("college_name"), "N/A")
    status        = safe_str(intern_profile.get("status"),        "active").lower()
    deact_reason  = safe_str(intern_profile.get("deactivation_reason"), "")
    
    cycle_info = get_internship_cycle(intern_profile.get("joining_date"), duration, intern_profile.get("created_at"))
    joining_date  = safe_str(cycle_info.get("joining_date") or sheet_user.get("joining_date"), "N/A")

    # ── SECTION I: Intern Details (two-column) ──────────────────────────────
    sec1_hdr = Paragraph("<b>SECTION I — INTERN REGISTRATION & ENROLLMENT DETAILS</b>", s_sec)
    COL = PAGE_W / 2 - 2

    def field(label, value, bold_val=False):
        v = f"<b>{escape_xml(str(value))}</b>" if bold_val else escape_xml(str(value))
        return [Paragraph(label, s_faint), Paragraph(v, s_bold if bold_val else s_normal)]

    sec1_data = [
        [sec1_hdr, ""],
        [Paragraph("RGT Unique ID", s_faint),
         Paragraph(f"<b><font name='Courier-Bold'>{escape_xml(intern_id)}</font></b>", s_bold),
         Paragraph("Programme", s_faint),
         Paragraph("<b>RGTvertex Internship Programme 2026</b>", s_bold)],
        [Paragraph("Full Name", s_faint),
         Paragraph(f"<b>{escape_xml(name)}</b>", s_bold),
         Paragraph("Department / Domain", s_faint),
         Paragraph(f"<b>{escape_xml(department)}</b>", s_bold)],
        [Paragraph("Email", s_faint),
         Paragraph(escape_xml(email), s_normal),
         Paragraph("Joining Date", s_faint),
         Paragraph(f"<b>{escape_xml(joining_date)}</b>", s_bold)],
        [Paragraph("Phone", s_faint),
         Paragraph(escape_xml(phone), s_normal),
         Paragraph("Duration", s_faint),
         Paragraph(f"<b>{duration} Months</b>", s_bold)],
        [Paragraph("University / College", s_faint),
         Paragraph(escape_xml(college_name), s_normal),
         Paragraph("Status", s_faint),
         Paragraph(f"<b>{escape_xml(status.upper())}</b>", s_bold)],
    ]
    sec1_col = [PAGE_W * 0.14, PAGE_W * 0.36, PAGE_W * 0.16, PAGE_W * 0.34]
    sec1 = Table(sec1_data, colWidths=sec1_col)
    sec1.setStyle(TableStyle([
        ("SPAN",           (0,0), (3,0)),
        ("BACKGROUND",     (0,0), (3,0),  colors.HexColor("#1e3a5f")),  # deep navy
        ("BACKGROUND",     (0,1), (3,-1), colors.HexColor("#fafafa")),
        ("ROWBACKGROUNDS", (0,2), (-1,-1), [colors.white, colors.HexColor("#f0f4fa")]),
        ("GRID",           (0,0), (-1,-1), 0.4, colors.HexColor("#d0daea")),
        ("VALIGN",         (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",     (0,0), (-1,-1), 3.5),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 3.5),
        ("LEFTPADDING",    (0,0), (-1,-1), 5),
        ("RIGHTPADDING",   (0,0), (-1,-1), 5),
    ]))
    story.append(sec1)
    story.append(Spacer(1, 14))

    # ── SECTION II: Performance Scorecard ───────────────────────────────────
    sec2_hdr = Paragraph("<b>SECTION II — MONTHLY PERFORMANCE EVALUATION SCORECARD</b>", s_sec)

    cycle_info  = get_internship_cycle(joining_date, duration, intern_profile.get("created_at"))
    all_cycles  = cycle_info.get("all_cycles", [])

    from services.cache_service import global_cache
    global_cache.invalidate("Performance")
    perf_reports = sorted(
        ss.get_performance_reports_for_student(str(intern_profile.get("id", ""))),
        key=lambda x: str(x.get("period_start") or x.get("submitted_at") or "")
    )
    perf_by_month = {}
    for r in perf_reports:
        k = str(r.get("period_start", ""))[:7]
        if k and k not in perf_by_month:
            perf_by_month[k] = r

    submissions = ss.get_submissions_for_student(str(intern_profile.get("id", "")))

    th = [
        Paragraph("Period",         s_th),
        Paragraph("Initiative\n/10",s_th),
        Paragraph("Task Comp\n/10", s_th),
        Paragraph("Behavioral\n/10",s_th),
        Paragraph("Score\n/10",     s_th),
        Paragraph("Standing",       s_th),
    ]
    sec2_data = [[sec2_hdr, "", "", "", "", ""], th]
    valid_scores = []

    cyc_rows = []
    for idx, cyc in enumerate(all_cycles):
        k = str(cyc.get("start",""))[:7]
        rep = perf_by_month.get(k)
        if rep is None and idx < len(perf_reports):
            c = perf_reports[idx]
            if str(c.get("period_start",""))[:7] == k:
                rep = c
        summary = get_performance_summary(rep)
        cyc_rows.append((idx, cyc, rep, summary))

    i = 0
    while i < len(cyc_rows):
        idx, cyc, rep, summary = cyc_rows[i]
        if summary.get("has_report"):
            obtained = str(summary.get("obtained_score", "–"))
            standing = str(summary.get("milestone_standing", "–"))
            s_color  = "#166534" if standing == "PASS" else "#991b1b"
            try:
                valid_scores.append(safe_float(summary.get("obtained_score"), 0.0))
            except Exception:
                pass
            m_label = f"Month {cyc.get('month', idx+1)}\n({str(cyc.get('start',''))[:7]})"
            sec2_data.append([
                Paragraph(escape_xml(m_label).replace("\n","<br/>"), s_td_c),
                Paragraph(str(summary.get("initiative_grade","–")), s_td_c),
                Paragraph(str(summary.get("task_completion","–")), s_td_c),
                Paragraph(str(summary.get("behavioral_index","–")), s_td_c),
                Paragraph(f"<b>{obtained}</b>", s_td_bc),
                Paragraph(f"<b><font color='{s_color}'>{standing}</font></b>", s_td_c),
            ])
            i += 1
        else:
            j = i
            while j < len(cyc_rows) and not cyc_rows[j][3].get("has_report"):
                j += 1
            m_label = f"Months {cyc_rows[i][1].get('month',i+1)}\u2013{cyc_rows[j-1][1].get('month',j)}\n(In Progress)"
            sec2_data.append([
                Paragraph(escape_xml(m_label).replace("\n","<br/>"), s_td_c),
                Paragraph("—", s_td_c), Paragraph("—", s_td_c), Paragraph("—", s_td_c),
                Paragraph("—", s_td_c),
                Paragraph("<b><font color='#b45309'>IN PROGRESS</font></b>", s_td_c),
            ])
            i = j

    cw2 = [PAGE_W*0.18, PAGE_W*0.13, PAGE_W*0.13, PAGE_W*0.13, PAGE_W*0.13, PAGE_W*0.30]
    sec2 = Table(sec2_data, colWidths=cw2)
    t2_styles = [
        ("SPAN",       (0,0), (-1,0)),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e4a4a")),  # dark teal
        ("BACKGROUND", (0,1), (-1,1), colors.HexColor("#2d6a6a")),  # teal column headers
        ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#c8dede")),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 2.5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2.5),
        ("LEFTPADDING", (0,0), (-1,-1), 2.5),
        ("RIGHTPADDING",(0,0), (-1,-1), 2.5),
    ]
    for r in range(2, len(sec2_data)):
        bg = colors.HexColor("#f0fafa") if r % 2 == 0 else colors.white
        t2_styles.append(("BACKGROUND", (0,r), (-1,r), bg))
    sec2.setStyle(TableStyle(t2_styles))
    story.append(sec2)
    story.append(Spacer(1, 14))

    # ── SECTION III: Attendance + Verdict (side by side) ────────────────────
    sec3_hdr = Paragraph("<b>SECTION III — ATTENDANCE & FINAL VERDICT</b>", s_sec)

    # Use the proper service functions that correctly match intern IDs
    intern_uuid   = str(intern_profile.get("id", ""))
    intern_rgt_id = str(intern_profile.get("intern_id") or intern_profile.get("rgt_id") or intern_uuid)
    
    # Try both the UUID and the RGTV-INT-XXXX id to get attendance
    intern_att = ss.get_attendance_for_student(intern_uuid)
    if not intern_att and intern_rgt_id != intern_uuid:
        intern_att = ss.get_attendance_for_student(intern_rgt_id)
    
    holidays_data  = ss.get_all_holidays()
    holiday_dates  = {h.get("date","")[:10] for h in holidays_data}

    def _is_wd(d):
        try:
            obj = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
            return obj.weekday() < 5 and str(d)[:10] not in holiday_dates
        except Exception:
            return False

    total_present  = sum(1 for a in intern_att if str(a.get("status","")).lower() in ("present","present_late") and _is_wd(a.get("date","")))
    total_sched    = compute_working_days(cycle_info.get("joining_date",""), cycle_info.get("expected_completion_date",""))
    
    # Use service function for leaves too
    leaves_list    = ss.get_leaves_for_student(intern_uuid)
    if not leaves_list and intern_rgt_id != intern_uuid:
        leaves_list = ss.get_leaves_for_student(intern_rgt_id)
    leaves_availed = sum(safe_int(l.get("days_requested"),1) for l in leaves_list if str(l.get("status","")).lower() == "approved")
    min_att        = safe_float(getattr(config,"MIN_ATTENDANCE_PASSING_LIMIT",75.0),75.0)
    att_rate       = round((total_present / total_sched)*100, 1) if total_sched > 0 else (100.0 if total_present > 0 else 0.0)
    att_color      = "#166534" if att_rate >= min_att else "#991b1b"

    min_perf = safe_float(getattr(config, "MIN_PERFORMANCE_PASSING_LIMIT", 5.0), 5.0)

    cum_score = round(sum(valid_scores)/len(valid_scores), 1) if valid_scores else 0.0
    grade_band = (
        "Outstanding"            if cum_score >= 9.0 else
        "Excellent"              if cum_score >= 8.0 else
        "Good"                   if cum_score >= 7.0 else
        "Satisfactory"           if cum_score >= 5.0 else
        "Needs Improvement"      if valid_scores else
        "N/A (Pending)"
    )
    
    verdict, v_color = get_graduation_verdict(status, deact_reason, duration, valid_scores, cum_score, att_rate, min_att, min_perf)

    # Pull leave allotted from intern profile
    leave_allotted = safe_int(intern_profile.get("leave_allotted_days"), 0)

    # Attendance table — 5 data rows (left half)
    att_data = [
        [Paragraph("<b>Attendance Summary</b>", s_sec), ""],
        [Paragraph("Days Present",            s_faint), Paragraph(f"{total_present} days", s_bold)],
        [Paragraph("Leaves Allotted",         s_faint), Paragraph(f"{leave_allotted} days", s_normal)],
        [Paragraph("Leaves Availed",          s_faint), Paragraph(f"{leaves_availed} days", s_bold)],
        [Paragraph("Min. Required Attendance",s_faint), Paragraph(f"{min_att}%",            s_normal)],
        [Paragraph("Attendance Rate",         s_faint), Paragraph(f"<b><font color='{att_color}'>{att_rate}%</font></b>", s_bold)],
    ]
    half = (PAGE_W - 8) / 2   # 8pt gap between tables
    att_t = Table(att_data, colWidths=[half * 0.60, half * 0.40])
    att_t.setStyle(TableStyle([
        ("SPAN",          (0,0),(1,0)),
        ("BACKGROUND",    (0,0),(1,0),  colors.HexColor("#1e3a5f")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f0f4fa")]),
        ("GRID",          (0,0),(-1,-1),0.4, colors.HexColor("#d0daea")),
        ("VALIGN",        (0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1),4),
        ("BOTTOMPADDING", (0,0),(-1,-1),4),
        ("LEFTPADDING",   (0,0),(-1,-1),6),
        ("RIGHTPADDING",  (0,0),(-1,-1),6),
    ]))

    # Verdict table — 5 data rows (right half)
    vrd_data = [
        [Paragraph("<b>Graduation Verdict</b>", s_sec), ""],
        [Paragraph("Cumulative Score",          s_faint), Paragraph(f"<b>{cum_score} / 10.0</b>", s_bold)],
        [Paragraph("Performance Grade",         s_faint), Paragraph(f"<b>{escape_xml(grade_band)}</b>", s_bold)],
        [Paragraph("Min. Passing Score",        s_faint), Paragraph(f"{min_perf} / 10.0",           s_normal)],
        [Paragraph("Min. Passing Attendance",   s_faint), Paragraph(f"{min_att}%",                   s_normal)],
        [Paragraph("Final Verdict",             s_faint), Paragraph(f"<b><font color='{v_color}'>{escape_xml(verdict)}</font></b>", s_bold)],
    ]
    vrd_t = Table(vrd_data, colWidths=[half * 0.60, half * 0.40])
    vrd_t.setStyle(TableStyle([
        ("SPAN",          (0,0),(1,0)),
        ("BACKGROUND",    (0,0),(1,0),  colors.HexColor("#1e4a4a")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f0fafa")]),
        ("GRID",          (0,0),(-1,-1),0.4, colors.HexColor("#c8dede")),
        ("VALIGN",        (0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1),4),
        ("BOTTOMPADDING", (0,0),(-1,-1),4),
        ("LEFTPADDING",   (0,0),(-1,-1),6),
        ("RIGHTPADDING",  (0,0),(-1,-1),6),
    ]))

    combined_hdr = Table([[sec3_hdr]], colWidths=[PAGE_W])
    combined_hdr.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), colors.HexColor("#2d2d2d")),
        ("GRID",          (0,0),(-1,-1), 0.4, colors.HexColor("#e8e8e8")),
        ("TOPPADDING",    (0,0),(-1,-1), 3.5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3.5),
        ("LEFTPADDING",   (0,0),(-1,-1), 6),
    ]))
    story.append(combined_hdr)
    story.append(Spacer(1, 1))

    # Side-by-side: equal halves with 8pt gap in middle
    outer = Table([[att_t, "", vrd_t]], colWidths=[half, 8, half])
    outer.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
        ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0),
        ("BOTTOMPADDING", (0,0),(-1,-1), 0),
    ]))
    story.append(outer)
    story.append(Spacer(1, 14))

    # ── Disclaimer + Footer Strip ───────────────────────────────────────────
    verify_url = f"{host_url.rstrip('/')}/verify/{intern_id}" if host_url else f"/verify/{intern_id}"
    ist_tz = pytz.timezone('Asia/Kolkata')
    v_date = datetime.now(ist_tz).strftime("%Y-%m-%d %H:%M IST")

    disc_lines = [
        Paragraph("<b>IMPORTANT VALIDATION & DISCLAIMERS</b>", style("DH", fontSize=7, fontName="Helvetica-Bold", textColor=colors.HexColor("#111111"), spaceAfter=2)),
        Paragraph(f"<b>1. Authenticity:</b> This report is generated from the RGTvertex Intern Portal and reflects verifiable evaluations as of {v_date}.", s_disc),
        Paragraph(f"<b>2. Passing Standards:</b> Graduation requires ≥{min_att}% attendance and ≥{min_perf}/10.0 cumulative score.", s_disc),
        Paragraph("<b>3. Data Accuracy:</b> Records are submitted by the intern's manager and verified by the RGTvertex administration.", s_disc),
        Spacer(1, 3),
        Paragraph("<i>Approved by: Administration Team & Programme Director — RGTvertex</i>",
                  style("AB", fontSize=6.5, fontName="Helvetica-Oblique", textColor=colors.HexColor("#737373"), alignment=2)),
    ]

    disc_t = Table([[d] for d in disc_lines], colWidths=[PAGE_W * 0.72])
    disc_t.setStyle(TableStyle([
        ("TOPPADDING",    (0,0),(-1,-1), 1.5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 1.5),
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
    ]))

    # QR column
    qr_cell_content = []
    try:
        from reportlab.graphics.barcode import qr
        from reportlab.graphics.shapes import Drawing
        
        qr_w = qr.QrCodeWidget(verify_url)
        bds = qr_w.getBounds()
        wq = bds[2] - bds[0]; hq = bds[3] - bds[1]
        sz = 55
        
        d = Drawing(sz, sz, transform=[sz/wq, 0, 0, sz/hq, 0, 0])
        d.add(qr_w)
        d.hAlign = "CENTER"
        qr_cell_content.append(d)
    except Exception as e:
        logger.warning("QR generation error: %s", e)
        qr_cell_content.append(Spacer(55, 55))

    qr_cell_content.append(Spacer(1, 2))
    qr_cell_content.append(Paragraph("Scan to Verify", style("SV", fontSize=6, fontName="Helvetica-Bold", textColor=colors.HexColor("#737373"), alignment=1)))
    qr_cell_content.append(Paragraph(escape_xml(intern_id), style("SI", fontSize=6, fontName="Courier-Bold", textColor=colors.HexColor("#111111"), alignment=1)))

    footer_row = Table(
        [[disc_t, qr_cell_content]],
        colWidths=[PAGE_W * 0.72, PAGE_W * 0.28]
    )
    footer_row.setStyle(TableStyle([
        ("VALIGN", (0,0),(-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0),(-1,-1), 0),
        ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",   (0,0),(-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 0),
        ("ALIGN",        (1,0),(1,0), "CENTER"),
    ]))

    story.append(KeepTogether([
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e8e8e8"), spaceBefore=4, spaceAfter=4),
        footer_row
    ]))

    # ── Build PDF ────────────────────────────────────────────────────────────
    class CleanCanvas(canvas.Canvas):
        def draw_page_decorations(self, *a):
            pass  # no footer decoration — all info is in the story itself

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25, rightMargin=25,
        topMargin=20, bottomMargin=22,
    )
    doc.build(story)
    return buffer.getvalue()


    normal_style.fontName = "Helvetica"
    normal_style.fontSize = 9
    normal_style.textColor = colors.HexColor("#0f172a")
    
    bold_style = ParagraphStyle(
        "BoldText",
        parent=normal_style,
        fontName="Helvetica-Bold"
    )
    
    title_style = ParagraphStyle(
        "BannerTitle",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=colors.white,
        alignment=1, # Center
        spaceAfter=4
    )
    
    subtitle_style = ParagraphStyle(
        "BannerSubTitle",
        parent=normal_style,
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#cbd5e1"),
        alignment=1 # Center
    )
    
    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=9.5,
        textColor=colors.white,
        alignment=0 # Left
    )
    
    th_style = ParagraphStyle(
        "TableHeader",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=7.5,
        textColor=colors.white,
        alignment=1 # Center
    )
    
    td_style = ParagraphStyle(
        "TableCell",
        parent=normal_style,
        fontSize=7.5,
        alignment=0 # Left
    )
    
    td_center = ParagraphStyle(
        "TableCellCenter",
        parent=normal_style,
        fontSize=7.5,
        alignment=1 # Center
    )
    
    td_bold_center = ParagraphStyle(
        "TableCellBoldCenter",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=7.5,
        alignment=1 # Center
    )
    
    disclaimer_style = ParagraphStyle(
        "DisclaimerText",
        parent=normal_style,
        fontSize=6.5,
        textColor=colors.HexColor("#334155"),
        leading=8.5
    )

    story = []
    
    # 1. Logo and Tagline
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    possible_logo_paths = [
        os.path.join(base_dir, "static", "img", "brand", "RGTlogo_only.jpeg"),
        os.path.join(base_dir, "static", "img", "brand", "RGTlogo.jpeg"),
        os.path.join(os.getcwd(), "static", "img", "brand", "RGTlogo_only.jpeg"),
        os.path.join(os.getcwd(), "static", "img", "brand", "RGTlogo.jpeg"),
        "static/img/brand/RGTlogo_only.jpeg",
        "static/img/brand/RGTlogo.jpeg",
    ]
    logo_path = None
    for p in possible_logo_paths:
        if os.path.exists(p):
            logo_path = p
            break
            
    if logo_path:
        try:
            from reportlab.lib.utils import ImageReader
            img_reader = ImageReader(logo_path)
            iw, ih = img_reader.getSize()
            aspect = ih / float(iw) if iw > 0 else 1.0
            target_width = 1.8 * cm  # ~0.7 inches
            target_height = target_width * aspect
            img = Image(logo_path, width=target_width, height=target_height)
            img.hAlign = 'CENTER'
            story.append(img)
            story.append(Spacer(1, 2))
        except Exception as e:
            logger.warning("Could not add logo to PDF: %s", e)
            
    tagline_style = ParagraphStyle(
        "BrandTagline",
        parent=normal_style,
        fontName="Helvetica",
        fontSize=8.5,
        leading=10,
        textColor=colors.HexColor("#64748b"),
        alignment=1,  # Center
        spaceAfter=4
    )
    story.append(Paragraph("<b>Reliable AI. Scalable Growth. Intelligent Technology</b>", tagline_style))
    story.append(Spacer(1, 4))
            
    # 2. Header Title Banner
    banner_content = [
        Paragraph("RGTVERTEX INTERNSHIP PROGRAMME – 2026", subtitle_style),
        Paragraph("OFFICIAL INTERNSHIP PERFORMANCE & EVALUATION REPORT", title_style)
    ]
    banner_table = Table([[banner_content]], colWidths=[545])
    banner_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#0f172a")),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 8))
    
    # 3. Data extraction
    if not intern_profile or not isinstance(intern_profile, dict):
        intern_profile = {}
    intern_id = safe_str(intern_profile.get("intern_id") or intern_profile.get("rgt_id") or f"RGTV-INT-{str(intern_profile.get('id', '0000'))[:4]}", "RGTV-INT-0000")
    name = safe_str(intern_profile.get("name"), "Unknown Intern")
    email = safe_str(intern_profile.get("email"), "N/A")
    
    try:
        from services.sheets_service import get_all_users
        all_users = get_all_users()
        sheet_user = next((u for u in all_users if u.get("id") == intern_profile.get("id")), {})
    except Exception:
        sheet_user = {}
        
    phone = safe_str(intern_profile.get("phone") or sheet_user.get("phone"), "N/A")
    duration = safe_int(intern_profile.get("internship_duration_months"), 3)
    if duration <= 0:
        duration = 3
        
    cycle_info = get_internship_cycle(intern_profile.get("joining_date"), duration, intern_profile.get("created_at"))
    joining_date = safe_str(cycle_info.get("joining_date") or sheet_user.get("joining_date"), "N/A")
    
    department = safe_str(intern_profile.get("department"), "General")
    college_name = safe_str(intern_profile.get("college_name") or sheet_user.get("college_name"), "N/A")
    status = safe_str(intern_profile.get("status"), "active").lower()
    deactivation_reason = safe_str(intern_profile.get("deactivation_reason"), "")
    
    # Section I: Intern Details Table
    sec1_header = Paragraph("<b>SECTION I: INTERN REGISTRATION & ENROLLMENT DETAILS</b>", section_title_style)
    sec1_data = [
        [sec1_header, "", "", ""],
        [Paragraph("RGT ID / Unique ID:", td_style), Paragraph(f"<b><font name='Courier-Bold'>{escape_xml(intern_id)}</font></b>", td_style),
         Paragraph("Enrollment:", td_style), Paragraph("<b>RGTvertex Internship Programme</b>", td_style)],
        [Paragraph("Intern Full Name:", td_style), Paragraph(f"<b>{escape_xml(name)}</b>", td_style),
         Paragraph("Domain / Department:", td_style), Paragraph(f"<b>{escape_xml(department)}</b>", td_style)],
        [Paragraph("Registered Email ID:", td_style), Paragraph(f"{escape_xml(email)}", td_style),
         Paragraph("Joining Date:", td_style), Paragraph(f"{escape_xml(joining_date)}", td_style)],
        [Paragraph("Mobile Number:", td_style), Paragraph(f"{escape_xml(phone)}", td_style),
         Paragraph("Internship Duration:", td_style), Paragraph(f"<b>{duration} Months</b>", td_style)],
        [Paragraph("University / College:", td_style), Paragraph(f"{escape_xml(college_name)}", td_style),
         Paragraph("Current Profile Status:", td_style), Paragraph(f"<b>{escape_xml(status.upper())}</b>", td_style)],
    ]
    sec1_table = Table(sec1_data, colWidths=[125, 147, 125, 148])
    sec1_table.setStyle(TableStyle([
        ('SPAN', (0,0), (3,0)),
        ('BACKGROUND', (0,0), (3,0), colors.HexColor("#1a1a1a")),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(sec1_table)
    story.append(Spacer(1, 10))
    
    # 4. Section II: Monthly Tasks & Professional Competency Evaluation
    sec2_header = Paragraph("<b>SECTION II: MONTHLY TASKS & PROFESSIONAL COMPETENCY EVALUATION</b>", section_title_style)
    
    all_cycles = cycle_info.get("all_cycles", [])
    
    # Force fresh performance data so PDF always reflects latest submitted reports
    from services.cache_service import global_cache
    global_cache.invalidate("Performance")
    perf_reports = ss.get_performance_reports_for_student(str(intern_profile.get("id", "")))
    # Sort chronological by period_start
    perf_reports = sorted(perf_reports, key=lambda x: str(x.get("period_start") or x.get("submitted_at") or ""))
    logger.info(
        "PDF: generating for intern_id=%r — found %d performance reports, %d cycles",
        intern_profile.get("id"), len(perf_reports), len(all_cycles)
    )
    
    # Build a lookup map: period_start[:7] (YYYY-MM) -> report, for date-based matching
    perf_by_month = {}
    for r in perf_reports:
        key = str(r.get("period_start", ""))[:7]  # YYYY-MM
        if key and key not in perf_by_month:
            perf_by_month[key] = r
    
    submissions = ss.get_submissions_for_student(str(intern_profile.get("id", "")))
    tasks = ss.get_tasks_for_student(str(intern_profile.get("id", "")), intern_profile.get("department", ""))
    
    sec2_headers = [
        Paragraph("Evaluation<br/>Period", th_style),
        Paragraph("Tasks<br/>Completed", th_style),
        Paragraph("Completion<br/>%", th_style),
        Paragraph("Initiative<br/>(Max 10)", th_style),
        Paragraph("Task Comp.<br/>(Max 10)", th_style),
        Paragraph("Behavioral<br/>Index (10)", th_style),
        Paragraph("Obtained<br/>Score", th_style),
        Paragraph("Milestone<br/>Standing", th_style),
    ]
    
    sec2_data = [
        [sec2_header, "", "", "", "", "", "", ""],
        sec2_headers
    ]
    
    valid_obtained_scores = []
    cyc_rows = []
    
    for idx, cyc in enumerate(all_cycles):
        cyc_month_key = str(cyc.get("start", ""))[:7]  # YYYY-MM
        rep = perf_by_month.get(cyc_month_key)
        
        if rep is None and idx < len(perf_reports):
            candidate = perf_reports[idx]
            if str(candidate.get("period_start", ""))[:7] == cyc_month_key:
                rep = candidate
        
        summary = get_performance_summary(rep)
        cyc_rows.append((idx, cyc, rep, summary))
        
    i = 0
    while i < len(cyc_rows):
        idx, cyc, rep, summary = cyc_rows[i]
        if summary.get("has_report"):
            m_num = cyc.get("month", idx + 1)
            m_start = safe_str(cyc.get("start"), "")[:7]
            m_label = f"Month {m_num}\n({m_start})"
            
            init_score = str(summary.get("initiative_grade", "0"))
            task_score = str(summary.get("task_completion", "0"))
            behav_score = str(summary.get("behavioral_index", "–"))
            obtained = str(summary.get("obtained_score", "–"))
            try:
                valid_obtained_scores.append(safe_float(summary.get("obtained_score"), 0.0))
            except Exception:
                pass
            
            standing = str(summary.get("milestone_standing", "–"))
            standing_color = "#15803d" if standing == "PASS" else "#b91c1c"
            standing_html = f"<b><font color='{standing_color}'>{standing}</font></b>"
            
            comp_str = f"{summary.get('tasks_completed', 0)} / {summary.get('tasks_assigned', 0)}"
            comp_pct = str(summary.get('completion_pct', '0%'))
            
            sec2_data.append([
                Paragraph(escape_xml(m_label).replace("\n", "<br/>"), td_center),
                Paragraph(escape_xml(comp_str), td_center),
                Paragraph(escape_xml(comp_pct), td_center),
                Paragraph(init_score, td_center),
                Paragraph(task_score, td_center),
                Paragraph(behav_score, td_center),
                Paragraph(f"<b>{obtained}</b>", td_bold_center),
                Paragraph(standing_html, td_center)
            ])
            i += 1
        else:
            j = i
            while j < len(cyc_rows) and not cyc_rows[j][3].get("has_report"):
                j += 1
            count = j - i
            if count == 1:
                m_num = cyc.get("month", idx + 1)
                m_start = safe_str(cyc.get("start"), "")[:7]
                m_label = f"Month {m_num}\n({m_start})"
            else:
                first_m = cyc_rows[i][1].get("month", i + 1)
                last_m = cyc_rows[j-1][1].get("month", j)
                first_start = safe_str(cyc_rows[i][1].get("start"), "")[:7]
                last_start = safe_str(cyc_rows[j-1][1].get("start"), "")[:7]
                if first_start and last_start and first_start != last_start:
                    m_label = f"Months {first_m}–{last_m}\n({first_start} to {last_start})"
                else:
                    m_label = f"Months {first_m}–{last_m}\n(In Progress)"
            
            sec2_data.append([
                Paragraph(escape_xml(m_label).replace("\n", "<br/>"), td_center),
                Paragraph("0 / 0", td_center),
                Paragraph("0%", td_center),
                Paragraph("–", td_center),
                Paragraph("–", td_center),
                Paragraph("–", td_center),
                Paragraph("–", td_center),
                Paragraph("<b><font color='#b45309'>IN PROGRESS</font></b>", td_center)
            ])
            i = j
            
    sec2_table = Table(sec2_data, colWidths=[84, 56, 56, 56, 60, 60, 56, 117])
    
    t_styles = [
        ('SPAN', (0,0), (-1,0)),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1a1a1a")),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#333333")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,1), 2.5),
        ('TOPPADDING', (0,2), (-1,-1), 1.5),
        ('BOTTOMPADDING', (0,2), (-1,-1), 1.5),
        ('LEFTPADDING', (0,0), (-1,-1), 2.5),
        ('RIGHTPADDING', (0,0), (-1,-1), 2.5),
    ]
    # Zebra striping
    for r in range(2, len(sec2_data)):
        bg = colors.HexColor("#f8fafc") if r % 2 == 0 else colors.white
        t_styles.append(('BACKGROUND', (0, r), (-1, r), bg))
        
    sec2_table.setStyle(TableStyle(t_styles))
    story.append(sec2_table)
    story.append(Spacer(1, 10))
    
    # 5. Section III: Attendance Summary
    sec3_header = Paragraph("<b>SECTION III: ATTENDANCE SUMMARY</b>", section_title_style)
    
    attendance_records = ss.get_all_attendance()
    intern_att = [a for a in attendance_records if str(a.get("intern_id")) == str(intern_profile.get("id", ""))]
    
    holidays_data = ss.get_all_holidays()
    holiday_dates = {h.get("date", "")[:10] for h in holidays_data}
    
    def _is_working_day(d_str):
        try:
            d_obj = datetime.strptime(str(d_str)[:10], "%Y-%m-%d").date()
            return d_obj.weekday() < 5 and str(d_str)[:10] not in holiday_dates
        except Exception:
            return False
            
    total_attended = sum(1 for a in intern_att if str(a.get("status")).lower() in ("present", "present_late") and _is_working_day(a.get("date", "")))
    
    total_scheduled = compute_working_days(cycle_info.get("joining_date", ""), cycle_info.get("expected_completion_date", ""))
    
    leaves = ss.get_leaves_for_student(str(intern_profile.get("id", "")))
    leaves_availed = sum(safe_int(l.get("days_requested"), 1) for l in leaves if str(l.get("status")).lower() == "approved")
    
    min_att_limit = safe_float(getattr(config, "MIN_ATTENDANCE_PASSING_LIMIT", 75.0), 75.0)
    att_rate = round((total_attended / total_scheduled) * 100, 1) if total_scheduled > 0 else (100.0 if total_attended > 0 else 0.0)
    
    att_color = "#15803d" if att_rate >= min_att_limit else "#b91c1c"
    
    sec3_data = [
        [sec3_header, ""],
        [Paragraph("Total Working Days:", td_style), Paragraph(f"{total_scheduled} Days", td_style)],
        [Paragraph("Total Days Present:", td_style), Paragraph(f"{total_attended} Days", td_style)],
        [Paragraph("Leaves Availed (Approved Absence):", td_style), Paragraph(f"{leaves_availed} Days", td_style)],
        [Paragraph("Minimum Attendance Passing Limit:", td_style), Paragraph(f"{min_att_limit}%", td_style)],
        [Paragraph("Attendance Rate Obtained:", td_style), Paragraph(f"<b><font color='{att_color}'>{att_rate}%</font></b>", td_style)],
    ]
    sec3_table = Table(sec3_data, colWidths=[295, 250])
    sec3_table.setStyle(TableStyle([
        ('SPAN', (0,0), (1,0)),
        ('BACKGROUND', (0,0), (1,0), colors.HexColor("#1a1a1a")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(sec3_table)
    story.append(Spacer(1, 10))
    
    # 6. Section IV: Cumulative Performance & Graduation Verdict
    sec4_header = Paragraph("<b>SECTION IV: CUMULATIVE PERFORMANCE & GRADUATION VERDICT</b>", section_title_style)
    
    cum_score = round(sum(valid_obtained_scores) / len(valid_obtained_scores), 1) if valid_obtained_scores else 0.0
    
    if cum_score >= 9.0:
        grade_band = "Outstanding"
    elif cum_score >= 8.0:
        grade_band = "Excellent"
    elif cum_score >= 7.0:
        grade_band = "Good"
    elif cum_score >= 5.0:
        grade_band = "Satisfactory"
    elif valid_obtained_scores:
        grade_band = "Needs Improvement"
    else:
        grade_band = "N/A (Pending Evaluations)"
        
    min_perf = safe_float(getattr(config, "MIN_PERFORMANCE_PASSING_LIMIT", 5.0), 5.0)
    verdict, verdict_color = get_graduation_verdict(status, deactivation_reason, duration, valid_obtained_scores, cum_score, att_rate, min_att_limit, min_perf)
        
    sec4_data = [
        [sec4_header, ""],
        [Paragraph("Cumulative Performance Rating (Out of 10.0):", td_style), Paragraph(f"<b>{cum_score} / 10.0</b>", td_style)],
        [Paragraph("Overall Performance Grade:", td_style), Paragraph(f"<b>{escape_xml(grade_band)}</b>", td_style)],
        [Paragraph("Official Internship Graduation Verdict:", td_style), Paragraph(f"<b><font color='{verdict_color}'>{escape_xml(verdict)}</font></b>", td_style)],
    ]
    sec4_table = Table(sec4_data, colWidths=[295, 250])
    sec4_table.setStyle(TableStyle([
        ('SPAN', (0,0), (1,0)),
        ('BACKGROUND', (0,0), (1,0), colors.HexColor("#1a1a1a")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(sec4_table)
    story.append(Spacer(1, 10))
    
    # 7. Disclaimers & Approval block (keep together so it doesn't break across pages)
    disc_title_style = ParagraphStyle(
        "DiscTitle",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=7.5,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=3
    )
    full_disclaimer_style = ParagraphStyle(
        "FullDisclaimer",
        parent=normal_style,
        fontSize=6.5,
        textColor=colors.HexColor("#334155"),
        leading=8.5,
        wordWrap='CJK'
    )
    disc_title = Paragraph("<b>IMPORTANT REGISTRY VALIDATION &amp; DISCLAIMERS</b>", disc_title_style)
    disc1 = Paragraph("<b>1. Authenticity of Report:</b> This official performance report is generated directly from the RGTvertex Intern Portal and accurately reflects verifiable attendance and competency evaluations as of the timestamped verification date.", full_disclaimer_style)
    disc2 = Paragraph(f"<b>2. Passing Standards:</b> Certification and graduation from the RGTvertex Internship Programme require a minimum cumulative attendance rate of <b>{min_att_limit}%</b> and a cumulative performance evaluation score of at least <b>{getattr(config, 'MIN_PERFORMANCE_PASSING_LIMIT', 5.0)}/10.0</b>.", full_disclaimer_style)
    disc3 = Paragraph("<b>3. Data Accuracy:</b> Periodic performance records and evaluations are submitted and maintained by the intern's assigned reporting manager and verified by the RGTvertex administration team.", full_disclaimer_style)
    approved_by = Paragraph(
        "<i>Approved by: Administration Team &amp; Program Director</i>",
        ParagraphStyle("ApprovedBy", parent=normal_style, fontSize=7, leading=9, alignment=2, textColor=colors.HexColor("#475569"))
    )
    
    disc_table = Table(
        [[disc_title], [disc1], [disc2], [disc3], [approved_by]],
        colWidths=[545]
    )
    disc_table.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('ALIGN', (0,4), (0,4), 'RIGHT'),
    ]))
    
    story.append(KeepTogether([HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#cbd5e1"), spaceBefore=6, spaceAfter=6), disc_table]))
    
    # 8. Build Document using NumberedCanvas
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25,
        rightMargin=25,
        topMargin=22,
        bottomMargin=55
    )
    
    # Prepare canvas subclass with specific metadata
    class CustomReportCanvas(NumberedCanvas):
        pass
        
    CustomReportCanvas.intern_id = intern_id
    ist_tz = pytz.timezone('Asia/Kolkata')
    CustomReportCanvas.verification_date = datetime.now(ist_tz).strftime("%Y-%m-%d %H:%M:%S IST")
    CustomReportCanvas.verify_url = f"{host_url.rstrip('/')}/verify/{intern_id}" if host_url else f"/verify/{intern_id}"
    
    doc.build(story, canvasmaker=CustomReportCanvas)
    
    return buffer.getvalue()
