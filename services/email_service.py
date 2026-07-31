"""
services/email_service.py
─────────────────────────
Handles SMTP email notifications.
"""
import os
import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime

logger = logging.getLogger(__name__)

NO_REPLY_SENDER = "RGTvertex Intern Portal (No-Reply)"

import threading

def _send_email_sync(to_email: str, subject: str, content: str, html_content: str = None, from_name: str = None):
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    
    if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
        logger.warning("SMTP configuration is missing. Email '%s' not sent.", subject)
        return False
        
    try:
        msg = EmailMessage()
        msg.set_content(content)
        
        if html_content:
            msg.add_alternative(html_content, subtype='html')
            
            # Embed logo inline if cid is used
            if 'cid:rgtlogo' in html_content:
                try:
                    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    logo_path = os.path.join(base_dir, "static", "img", "brand", "RGTlogo_only.jpeg")
                    with open(logo_path, 'rb') as img:
                        img_data = img.read()
                    msg.get_payload()[1].add_related(img_data, 'image', 'jpeg', cid='<rgtlogo>')
                except Exception as e:
                    logger.error("Could not attach inline logo: %s", e)
            
        msg["Subject"] = subject
        if from_name:
            msg["From"] = f"{from_name} <{smtp_username}>"
        else:
            msg["From"] = smtp_username
        msg["To"] = to_email

        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
            
        logger.info("Sent email to %s: %s", to_email, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False

def _send_email(to_email: str, subject: str, content: str, html_content: str = None, from_name: str = None):
    thread = threading.Thread(
        target=_send_email_sync,
        args=(to_email, subject, content, html_content, from_name)
    )
    thread.daemon = True
    thread.start()
    return True

def _render_branded_email(title: str, body_html: str) -> str:
    # Use CID embedding for inline logo
    logo_url = "cid:rgtlogo"
    
    return f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
      </head>
      <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f7f6; color: #333333; margin: 0; padding: 0; line-height: 1.6;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f7f6; padding: 20px 0;">
          <tr>
            <td align="center">
              <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                <!-- Header -->
                <tr>
                  <td align="center" style="padding: 30px 20px; background-color: #ffffff; border-bottom: 2px solid #f0f0f0;">
                    <img src="{logo_url}" alt="RGTvertex Logo" style="height: 80px; width: auto; max-width: 100%; display: inline-block; margin-bottom: 15px;">
                    <p style="margin: 0; font-size: 13px; color: #6b7280; font-weight: 500; letter-spacing: 0.5px; text-transform: uppercase;">Reliable AI. Scalable Growth. Intelligent Technology</p>
                  </td>
                </tr>
                <!-- Body -->
                <tr>
                  <td style="padding: 40px 30px;">
                    {body_html}
                  </td>
                </tr>
                <!-- Footer -->
                <tr>
                  <td align="center" style="padding: 20px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; font-size: 13px; color: #9ca3af;">
                    <p style="margin: 0;">&copy; {datetime.now().year} RGTvertex. All rights reserved.</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """

def send_leave_request_notification(manager_email: str, student_name: str, leave_id: str, start_date: str, end_date: str, reason: str, host_url: str):
    subject = f"Leave Request from {student_name}"
    content = f"Hello,\n\n{student_name} has requested a leave from {start_date} to {end_date}.\n\nReason:\n{reason}\n\nYou can review and approve or reject this request by clicking the link below:\n{host_url}/manager/leaves/{leave_id}\n\nBest,\nRGTvertex Intern Portal\n"
    
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Leave Request</h2>
        <p style="font-size: 16px;">Dear Manager,</p>
        <p style="font-size: 16px;"><strong>{student_name}</strong> has requested a leave.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>From:</strong> {start_date}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>To:</strong> {end_date}</td>
          </tr>
          <tr>
            <td><strong>Reason:</strong><br>{reason}</td>
          </tr>
        </table>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/manager/leaves/{leave_id}" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">Review Request</a>
        </div>
        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Team</p>
    """)
    return _send_email(manager_email, subject, content, html_content, from_name=NO_REPLY_SENDER)


def send_leave_decision_notification(target_email: str, student_name: str, status: str, start_date: str, end_date: str, remarks: str, manager_name: str, host_url: str):
    subject = f"Leave Request {'Approved' if status.lower() == 'approved' else 'Rejected'} – {start_date} to {end_date}"
    
    status_color = "#10b981" if status.lower() == "approved" else "#ef4444"
    
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Leave Request Decision</h2>
        <p style="font-size: 16px;">Dear {student_name},</p>
        <p style="font-size: 16px;">Your leave request for <strong>{start_date}</strong> to <strong>{end_date}</strong> has been <span style="color: {status_color}; font-weight: bold;">{status.upper()}</span>.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Status:</strong> <span style="color: {status_color}; font-weight: bold;">{status.upper()}</span></td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Manager:</strong> {manager_name}</td>
          </tr>
          <tr>
            <td><strong>Remarks:</strong><br>{remarks if remarks else 'None provided.'}</td>
          </tr>
        </table>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/intern/leave" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Leave Details</a>
        </div>

        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Team</p>
    """)
    
    content = f"Your leave request has been {status}.\nManager: {manager_name}\nRemarks: {remarks}\n\nView details here: {host_url}/intern/leave"
    return _send_email(target_email, subject, content, html_content, from_name=NO_REPLY_SENDER)

def send_attendance_notification(target_email: str, student_name: str, date: str, status: str, manager_name: str, department: str, host_url: str):
    # From name logic for attendance emails
    from_name = f"{manager_name} at RGTvertex"
    
    from datetime import datetime, timezone
    time_marked = datetime.now(timezone.utc).strftime("%H:%M UTC")
    
    details = f"""
--
Attendance Details:
Status: {status.title()}
Date: {date}
Time Marked: {time_marked}
Manager: {manager_name}
Department: {department}
--"""

    if status == "present":
        subject = f"Attendance – Marked Present ({date})"
        content_header = f"Your attendance for {date} has been marked Present."
    elif status == "absent":
        subject = f"Attendance – Marked Absent ({date})"
        content_header = f"You have been marked Absent for {date} as no check-in was recorded.<br>If incorrect, please inform your manager within 24 hours."
    elif status == "on_leave":
        subject = f"Attendance – On Leave ({date})"
        content_header = f"Your leave for {date} has been approved by {manager_name}. Attendance marked as On Leave."
    else:
        return False
        
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Attendance Notification</h2>
        <p style="font-size: 16px;">Dear {student_name},</p>
        <p style="font-size: 16px;">{content_header}</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Status:</strong> {status.title()}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Date:</strong> {date}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Time Marked:</strong> {time_marked}</td>
          </tr>
          <tr>
            <td><strong>Manager:</strong> {manager_name} ({department})</td>
          </tr>
        </table>
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/intern/attendance" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Attendance</a>
        </div>
        
        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Attendance System</p>
    """)
    
    # Text fallback
    content = f"Hi {student_name},\n\n{content_header.replace('<br>', chr(10))}\n{details}\n\nView details here: {host_url}/intern/attendance\n\nRegards,\nRGTvertex Attendance System"
    return _send_email(target_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_manager_attendance_override_notification(target_email: str, manager_name: str, intern_name: str, date: str, status: str, marking_manager_name: str, department: str, host_url: str):
    subject = f"Attendance Update – {intern_name} ({date})"
    
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Attendance Marked by Another Manager</h2>
        <p style="font-size: 16px;">Dear {manager_name},</p>
        <p style="font-size: 16px;">This is to inform you that the attendance for <strong>{intern_name}</strong> on <strong>{date}</strong> has been marked as <strong>{status.title()}</strong> by {marking_manager_name}.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Intern:</strong> {intern_name}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Status:</strong> {status.title()}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Date:</strong> {date}</td>
          </tr>
          <tr>
            <td><strong>Action By:</strong> {marking_manager_name}</td>
          </tr>
        </table>
        
        <p style="font-size: 16px;">No further action is required from you.</p>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/manager/attendance" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Attendance</a>
        </div>

        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Team</p>
    """)
    
    content = f"Hi {manager_name},\n\nAttendance for {intern_name} on {date} was marked as {status.title()} by {marking_manager_name}.\n\nView details here: {host_url}/manager/attendance"
    return _send_email(target_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_manager_invite_email(target_email: str, token: str, department: str, host_url: str):
    subject = f"Invitation – Department Manager Role at RGTvertex"
    invite_url = f"{host_url}/manager/signup?token={token}"
    content = f"""Hi Admin,

We're pleased to invite you to take on the role of Department Manager for {department} at RGTvertex.

Responsibilities include:
• Approving/reviewing leave requests and monitoring team attendance
• Coordinating onboarding and daily team operations
• Reporting progress to the founding team

Let us know if you accept, and we'll schedule a call to align on next steps.

Please click the link below to create your account:
{invite_url}

Regards,
Admin
RGTvertex"""

    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Department Manager Invitation</h2>
        <p style="font-size: 16px;">Hi <strong>Admin</strong>,</p>
        <p style="font-size: 16px;">We're pleased to invite you to take on the role of Department Manager for <strong>{department}</strong> at RGTvertex.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Role:</strong> Department Manager</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Department:</strong> {department}</td>
          </tr>
          <tr>
            <td>
              <strong>Responsibilities:</strong><br>
              • Approving/reviewing leave requests and monitoring team attendance<br>
              • Coordinating onboarding and daily team operations<br>
              • Reporting progress to the founding team
            </td>
          </tr>
        </table>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{invite_url}" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">Accept Invitation & Setup Account</a>
        </div>
        
        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Admin</p>
    """)
    
    return _send_email(target_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_absence_warning_notification(target_email: str, student_name: str, department: str, days_absent: int, manager_name: str, host_url: str):
    """
    Sends a formal warning email for continuous unapproved absence.
    Also CC's the shared manager notification email.
    """
    subject = "Warning – Unapproved Absence from Internship Duties"
    
    content = f"""Hi {student_name},

This is to bring to your attention that you have been absent from your internship duties for the past {days_absent} days without prior notice or approval from your reporting manager.

As a {department} intern at RGTvertex, you are expected to maintain regular attendance and inform your reporting manager in advance in case of any unavoidable absence. Unapproved and unexplained absence is a serious concern and reflects poorly on your commitment to the internship.

Please treat this email as a formal warning. If such behavior continues or repeats in the future, we will have no option but to terminate your internship with immediate effect.

We expect you to report back to work immediately and maintain discipline going forward. If you are facing any genuine issue, please communicate it to us at the earliest so we can understand and assist accordingly.

Regards,
{manager_name}
Reporting Manager – {department}
RGTvertex"""
    
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #dc2626; margin-top: 0;">Formal Warning – Unapproved Absence</h2>
        <p style="font-size: 16px;">Dear {student_name},</p>
        <p style="font-size: 16px;">This is to bring to your attention that you have been absent from your internship duties for the past <strong>{days_absent} days</strong> without prior notice or approval from your reporting manager.</p>
        <p style="font-size: 16px;">As a {department} intern at RGTvertex, you are expected to maintain regular attendance and inform your reporting manager in advance in case of any unavoidable absence. Unapproved and unexplained absence is a serious concern and reflects poorly on your commitment to the internship.</p>
        <p style="font-size: 16px; font-weight: bold; color: #dc2626;">Please treat this email as a formal warning. If such behavior continues or repeats in the future, we will have no option but to terminate your internship with immediate effect.</p>
        <p style="font-size: 16px;">We expect you to report back to work immediately and maintain discipline going forward. If you are facing any genuine issue, please communicate it to us at the earliest so we can understand and assist accordingly.</p>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/intern/attendance" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Attendance</a>
        </div>

        <p style="font-size: 16px; margin-top: 30px;">Regards,<br><strong>{manager_name}</strong><br>Reporting Manager – {department}<br>RGTvertex</p>
    """)
    
    content = content + f"\n\nView details here: {host_url}/intern/attendance"
    
    cc_email = os.environ.get("MANAGER_NOTIFICATION_EMAIL", "rgtvertexintern@gmail.com")
    
    # We will send a single email with HTML content. 
    # To CC, we can format the SMTP call slightly differently, but for now we'll just send two emails.
    _send_email(target_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)
    return _send_email(cc_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_extension_request_notification(manager_email: str, manager_name: str, student_name: str, requested_months: str, reason: str, host_url: str):
    subject = f"Extension Request from {student_name}"
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Extension Request</h2>
        <p style="font-size: 16px;">Dear {manager_name},</p>
        <p style="font-size: 16px;"><strong>{student_name}</strong> has requested an internship extension.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Requested Duration:</strong> +{requested_months} Months</td>
          </tr>
          <tr>
            <td><strong>Reason:</strong><br>{reason}</td>
          </tr>
        </table>
        
        <p style="font-size: 16px;">Please review this request in the Manager Dashboard.</p>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/manager/extensions" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">Review Request</a>
        </div>

        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Team</p>
    """)
    content = f"Hi {manager_name},\n\n{student_name} has requested an extension of {requested_months} months.\nReason:\n{reason}\n\nReview this in your dashboard: {host_url}/manager/extensions\n\nRGTvertex Team"
    return _send_email(manager_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_extension_decision_notification(target_email: str, student_name: str, status: str, requested_months: str, remarks: str, host_url: str):
    subject = f"Extension Request {'Approved' if status.lower() == 'approved' else 'Rejected'}"
    status_color = "#10b981" if status.lower() == "approved" else "#ef4444"
    
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Extension Request Decision</h2>
        <p style="font-size: 16px;">Dear {student_name},</p>
        <p style="font-size: 16px;">Your request for a <strong>{requested_months}-month extension</strong> has been <span style="color: {status_color}; font-weight: bold;">{status.upper()}</span>.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Status:</strong> <span style="color: {status_color}; font-weight: bold;">{status.upper()}</span></td>
          </tr>
          <tr>
            <td><strong>Remarks:</strong><br>{remarks if remarks else 'None provided.'}</td>
          </tr>
        </table>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/intern/extension/request" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Extension Status</a>
        </div>

        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Team</p>
    """)
    content = f"Hi {student_name},\n\nYour request for an extension of {requested_months} months has been {status}.\nRemarks: {remarks}\n\nView details here: {host_url}/intern/extension/request\n\nRGTvertex Team"
    return _send_email(target_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)
    
    # Send to intern with CC to manager notification email
    # _send_email does not explicitly support CC, so we can send one email to the intern,
    # and a copy to the CC address. Or modify _send_email.
    # Given _send_email uses EmailMultiAlternatives which supports cc, let's just send separately 
    # to avoid changing _send_email signature if not needed, or better, we can modify the _send_email 
    # call to handle it. For now, sending a separate notification to the manager inbox is safest.
    
    # Send to intern
    _send_email(target_email, subject, content, from_name=NO_REPLY_SENDER)
    # Send copy to manager inbox
    _send_email(cc_email, f"[CC] {subject} - {student_name}", content, from_name=NO_REPLY_SENDER)
    
    return True

def send_password_reset_email(target_email: str, name: str, reset_link: str):
    subject = "Reset your RGTvertex Password"
    content = f"""Hi {name},

You requested a password reset for your RGTvertex Intern Portal account.

Please click the link below to reset your password. This link is valid for 1 hour.
{reset_link}

If you did not request this, please ignore this email.

Best,
RGTvertex Intern Portal
"""
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Password Reset Request</h2>
        <p style="font-size: 16px;">Hi <strong>{name}</strong>,</p>
        <p style="font-size: 16px;">You requested a password reset for your RGTvertex Intern Portal account.</p>
        <p style="font-size: 16px;">Please click the button below to set a new password. This link is valid for 1 hour.</p>
        <div style="text-align: center; margin-top: 30px;">
          <a href="{reset_link}" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">Reset Password</a>
        </div>
        <p style="font-size: 14px; color: #6b7280; margin-top: 30px;">If you did not request a password reset, you can safely ignore this email.</p>
    """)
    return _send_email(target_email, subject, content, html_content, from_name=NO_REPLY_SENDER)


def send_daily_report_reminder_email(target_email: str, name: str, department: str, date_str: str, report_url: str):
    subject = f"Action Required: Missing Daily Report for {date_str}"
    
    content = f"""Hi {name},

This is an automated reminder that your internship shift (5:00 PM to 9:00 PM) has ended, and the 10:30 PM deadline to submit your Daily Report has passed.

We noticed that your Daily Report for {date_str} is still pending.

Please submit it immediately using the link below:
{report_url}

Regards,
RGTvertex Attendance System
"""
    
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #dc2626; margin-top: 0;">Missing Daily Report</h2>
        <p style="font-size: 16px;">Hi <strong>{name}</strong>,</p>
        <p style="font-size: 16px;">This is an automated reminder that your internship shift (5:00 PM to 9:00 PM) has ended, and the <strong>10:30 PM deadline</strong> to submit your Daily Report has passed.</p>
        <p style="font-size: 16px;">We noticed that your Daily Report for <strong>{date_str}</strong> is still pending.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Intern:</strong> {name}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Department:</strong> {department}</td>
          </tr>
          <tr>
            <td><strong>Date:</strong> {date_str}</td>
          </tr>
        </table>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{report_url}" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">Submit Daily Report Now</a>
        </div>
        
        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Attendance System</p>
    """)
    
    return _send_email(target_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_missing_report_escalation(manager_email: str, manager_name: str, intern_name: str, department: str, date_str: str):
    subject = f"Missing Daily Report — {intern_name}"
    
    content = f"""Hi {manager_name},

{intern_name} ({department}) has not submitted their Daily Report for {date_str}, even after the 10:30 PM reminder was sent.

Please follow up with them directly.

Regards,
RGTvertex Attendance System
"""

    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #dc2626; margin-top: 0;">Missing Daily Report Escalation</h2>
        <p style="font-size: 16px;">Hi <strong>{manager_name}</strong>,</p>
        <p style="font-size: 16px;"><strong>{intern_name}</strong> ({department}) has not submitted their Daily Report for {date_str}, even after the 10:30 PM reminder was sent.</p>
        <p style="font-size: 16px;">Please follow up with them directly.</p>
        
        <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 6px; margin: 25px 0;">
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Intern:</strong> {intern_name}</td>
          </tr>
          <tr>
            <td style="border-bottom: 1px solid #e5e7eb;"><strong>Department:</strong> {department}</td>
          </tr>
          <tr>
            <td><strong>Date:</strong> {date_str}</td>
          </tr>
        </table>
        
        <p style="font-size: 16px; margin-top: 30px;">Regards,<br>RGTvertex Attendance System</p>
    """)
    
    cc_email = os.environ.get("MANAGER_NOTIFICATION_EMAIL", "rgtvertexintern@gmail.com")
    
    _send_email(manager_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)
    return _send_email(cc_email, subject, content, html_content=html_content, from_name=NO_REPLY_SENDER)

def send_manual_warning_notification(intern_email: str, intern_name: str, reason: str, issuer_name: str, host_url: str):
    subject = "Action Required: Formal Warning Issued"
    content = f"Hello {intern_name},\n\nA formal warning has been issued by {issuer_name}.\n\nReason: {reason}\n\nPlease log in to the portal for details: {host_url}\n"
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #ef4444; margin-top: 0;">Formal Warning Issued</h2>
        <p>Dear {intern_name},</p>
        <p>A formal warning has been issued on your profile by <strong>{issuer_name}</strong>.</p>
        <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 15px; margin: 20px 0; color: #991b1b;">
            <strong>Reason:</strong><br>{reason}
        </div>
        <p>Please log in to your portal to review and acknowledge this warning.</p>
        <div style="text-align: center; margin-top: 25px;">
          <a href="{host_url}/intern/dashboard" style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #fff; text-decoration: none; border-radius: 6px;">Go to Portal</a>
        </div>
    """)
    return _send_email(intern_email, subject, content, html_content)

def send_new_intern_manager_notification(manager_email: str, manager_name: str, intern_name: str, intern_id: str, host_url: str):
    subject = f"New Intern Assigned: {intern_name}"
    content = f"Hello {manager_name},\n\nA new intern, {intern_name} ({intern_id}), has been assigned to you."
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">New Intern Assigned</h2>
        <p>Dear {manager_name},</p>
        <p>A new intern has just signed up and been automatically assigned to your department.</p>
        <ul>
            <li><strong>Name:</strong> {intern_name}</li>
            <li><strong>ID:</strong> {intern_id}</li>
        </ul>
        <p>Please log in to the manager portal to view their details.</p>
        <div style="text-align: center; margin-top: 25px;">
          <a href="{host_url}/manager/dashboard" style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #fff; text-decoration: none; border-radius: 6px;">View Dashboard</a>
        </div>
    """)
    return _send_email(manager_email, subject, content, html_content)

def send_performance_report_issued_notification(intern_email: str, intern_name: str, manager_name: str, month_str: str, host_url: str):
    subject = f"Performance Report Available for {month_str}"
    content = f"Hello {intern_name},\n\nYour performance report for {month_str} has been submitted by {manager_name}."
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Performance Report Issued</h2>
        <p>Dear {intern_name},</p>
        <p>Your performance report for <strong>{month_str}</strong> has been completed by {manager_name}.</p>
        <p>Please log in to the portal to review your scores and acknowledge the report.</p>
        <div style="text-align: center; margin-top: 25px;">
          <a href="{host_url}/intern/performance" style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #fff; text-decoration: none; border-radius: 6px;">View Report</a>
        </div>
    """)
    return _send_email(intern_email, subject, content, html_content)

def send_account_deactivation_notification(intern_email: str, intern_name: str, reason: str):
    subject = "Notice: Account Deactivated"
    content = f"Hello {intern_name},\n\nYour RGTvertex Intern Portal account has been deactivated.\nReason: {reason}"
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Account Deactivated</h2>
        <p>Dear {intern_name},</p>
        <p>Your access to the RGTvertex Intern Portal has been deactivated.</p>
        <div style="background-color: #f9fafb; padding: 15px; margin: 20px 0;">
            <strong>Status Reason:</strong> {reason}
        </div>
        <p>If you believe this is an error or have questions, please contact the administration.</p>
    """)
    return _send_email(intern_email, subject, content, html_content)

def send_password_changed_notification(user_email: str, user_name: str, host_url: str):
    subject = "Security Alert: Password Changed"
    content = f"Hello {user_name},\n\nYour password was successfully changed."
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Password Changed Successfully</h2>
        <p>Dear {user_name},</p>
        <p>This is a confirmation that the password for your RGTvertex portal account has just been changed.</p>
        <p>If you did not perform this action, please contact your administrator immediately.</p>
        <div style="text-align: center; margin-top: 25px;">
          <a href="{host_url}/auth/login" style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #fff; text-decoration: none; border-radius: 6px;">Log In</a>
        </div>
    """)
    return _send_email(user_email, subject, content, html_content)

def send_internship_completion_reminder(target_email: str, target_name: str, role: str, completion_date: str, intern_name: str, host_url: str):
    days_left = (datetime.strptime(completion_date, "%Y-%m-%d").date() - datetime.today().date()).days
    subject = f"Reminder: Internship Completion in {days_left} Days"
    
    if role == "intern":
        greeting = f"Dear {target_name},"
        body = f"This is a friendly reminder that your internship with RGTvertex is scheduled to conclude on <strong>{completion_date}</strong> (in {days_left} days)."
    else:
        greeting = f"Dear {target_name},"
        body = f"This is an automated reminder that the internship for <strong>{intern_name}</strong> is scheduled to conclude on <strong>{completion_date}</strong> (in {days_left} days)."

    content = f"{greeting}\n\n{body}\n"
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Approaching Completion</h2>
        <p>{greeting}</p>
        <p>{body}</p>
        <p>Please ensure all final reports, evaluations, and exit formalities are prepared accordingly.</p>
        
        <div style="text-align: center; margin-top: 30px;">
          <a href="{host_url}/{'intern' if role == 'intern' else 'manager'}/dashboard" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Dashboard</a>
        </div>
    """)
    content = content + f"\nView dashboard here: {host_url}/{'intern' if role == 'intern' else 'manager'}/dashboard\n"
    return _send_email(target_email, subject, content, html_content)

def send_welcome_email(intern_email: str, intern_name: str, intern_id: str, host_url: str):
    subject = "Welcome to RGTvertex!"
    content = f"Hello {intern_name},\n\nWelcome to the RGTvertex Intern Portal! Your Intern ID is {intern_id}."
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Welcome to RGTvertex!</h2>
        <p>Dear {intern_name},</p>
        <p>Your account on the RGTvertex Intern Portal has been successfully created.</p>
        <div style="background-color: #f9fafb; padding: 15px; margin: 20px 0;">
            <strong>Your Intern ID:</strong> {intern_id}<br>
            <strong>Portal URL:</strong> <a href="{host_url}">{host_url}</a>
        </div>
        <p>Make sure to log in daily to mark your attendance and submit your daily reports!</p>
        <div style="text-align: center; margin-top: 25px;">
          <a href="{host_url}/auth/login" style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #fff; text-decoration: none; border-radius: 6px;">Log In Now</a>
        </div>
    """)
    return _send_email(intern_email, subject, content, html_content)

def send_performance_acknowledged_notification(manager_email: str, manager_name: str, intern_name: str, month_str: str, host_url: str):
    subject = f"Performance Report Acknowledged: {intern_name}"
    content = f"Hello {manager_name},\n\n{intern_name} has acknowledged their performance report for {month_str}."
    html_content = _render_branded_email(subject, f"""
        <h2 style="color: #111827; margin-top: 0;">Report Acknowledged</h2>
        <p>Dear {manager_name},</p>
        <p><strong>{intern_name}</strong> has reviewed and acknowledged their performance evaluation for <strong>{month_str}</strong>.</p>
        <div style="text-align: center; margin-top: 25px;">
          <a href="{host_url}/manager/performance" style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #fff; text-decoration: none; border-radius: 6px;">View Dashboard</a>
        </div>
    """)
    return _send_email(manager_email, subject, content, html_content)

def send_manager_reassignment_notifications(old_mgr_email: str, old_mgr_name: str, new_mgr_email: str, new_mgr_name: str, intern_email: str, intern_name: str, host_url: str):
    # Notify old manager
    if old_mgr_email:
        old_subj = f"Intern Reassigned: {intern_name}"
        old_html = _render_branded_email(old_subj, f"""
            <h2 style='margin-top:0;'>Intern Reassignment</h2>
            <p>Dear {old_mgr_name},</p>
            <p><strong>{intern_name}</strong> has been reassigned to a different manager by the administration.</p>
            <div style="text-align: center; margin-top: 30px;">
              <a href="{host_url}/manager/dashboard" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Dashboard</a>
            </div>
        """)
        _send_email(old_mgr_email, old_subj, f"Intern reassigned.\nView dashboard: {host_url}/manager/dashboard", old_html)

    # Notify new manager
    if new_mgr_email:
        new_subj = f"New Intern Assigned: {intern_name}"
        new_html = _render_branded_email(new_subj, f"""
            <h2 style='margin-top:0;'>New Intern Assigned</h2>
            <p>Dear {new_mgr_name},</p>
            <p><strong>{intern_name}</strong> has been reassigned to you by the administration.</p>
            <div style="text-align: center; margin-top: 30px;">
              <a href="{host_url}/manager/dashboard" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Dashboard</a>
            </div>
        """)
        _send_email(new_mgr_email, new_subj, f"Intern assigned.\nView dashboard: {host_url}/manager/dashboard", new_html)

    # Notify intern
    if intern_email:
        int_subj = "Notice: Manager Reassigned"
        int_html = _render_branded_email(int_subj, f"""
            <h2 style='margin-top:0;'>Manager Reassigned</h2>
            <p>Dear {intern_name},</p>
            <p>Your reporting manager has been updated to <strong>{new_mgr_name or 'Unassigned'}</strong> by the administration.</p>
            <div style="text-align: center; margin-top: 30px;">
              <a href="{host_url}/intern/profile" style="display: inline-block; padding: 12px 24px; background-color: #111827; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px;">View Profile</a>
            </div>
        """)
        _send_email(intern_email, int_subj, f"Manager reassigned.\nView profile: {host_url}/intern/profile", int_html)
