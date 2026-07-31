# RGTvertex Intern Portal

© 2026 RGTvertex. Intern Management Portal v1.0

A full-stack Flask web application built to manage interns, track attendance, handle leave requests, and conduct weekly performance evaluations. The application uses a role-based access control (RBAC) system with three distinct roles: Admin, Department Manager, and Intern.

## Tech Stack
- **Backend**: Python, Flask
- **Database**: Supabase (PostgreSQL) for Profiles/Auth, Google Sheets for dynamic analytics and tabular data tracking (Attendance, Leaves, Reports).
- **Authentication**: Supabase Auth
- **Email Notifications**: Native `smtplib` connected to a Gmail account.
- **Exports**: `pandas`, `openpyxl`, `reportlab`
- **Deployment**: Configured for Vercel Serverless Functions (`vercel.json`).

## Key Features
1. **Role-Based Access Control (RBAC)**
   - **Admin**: Has global visibility. Can approve leaves, view organization-wide dashboards, manage users, handle roles, and export reports in CSV/Excel/PDF.
   - **Department Manager**: Restricted to viewing interns within their assigned department. Can mark daily attendance, manage tasks (create, assign, review), and submit monthly performance reports.
   - **Intern**: Has access to a dynamic attendance calendar, can submit and withdraw leave requests, submit and edit daily task reports, view notifications, and read their performance evaluations.

2. **Automated Attendance**
   - Managers mark attendance via the portal.
   - The system automatically fires an email to the intern on behalf of the manager notifying them if they were marked Present, Absent, or On Leave.

3. **Leave Management**
   - Interns submit leave requests detailing start/end dates and reasoning.
   - Emails are routed to a centralized admin inbox (`rgtvertexintern@gmail.com`).
   - Admins review and approve/reject leave requests from the Admin dashboard.

4. **Performance & Task Tracking**
   - Managers submit a monthly graded performance report for each intern based on 7 criteria, with auto-calculated scores (out of 100) and grade bands.
   - Managers assign tasks to interns, and interns submit daily progress reports (which they can edit on the same day).

5. **Analytics & Exports**
   - Admin dashboard displays active KPIs (Present/Absent counts, unread warnings, at-risk interns).
   - One-click exports of Attendance, Leave, and Performance data to **CSV**, **Excel (.xlsx)**, and **PDF**.

6. **Real-Time Notifications & Announcements**
   - In-app notification bell alerts managers of pending leaves and unreviewed reports.
   - Interns are alerted of recent attendance marks, new performance evaluations, leave approvals, and unacknowledged warnings.
   - Fault-tolerant context processing ensures the site stays up even if the notification engine faces temporary database issues.
   - Global Announcements widget displayed actively across Admin, Manager, and Intern dashboards.

7. **Security & Data Integrity**
   - Built-in "Forgot Password" flow using secure email reset tokens.
   - "My Profile" allows all roles to update their names and change passwords.
   - Verified PDF export routes with `/verify/<intern_id>` support using QR codes on official certificates.
   - Vercel-optimized caching strategy (stateless).
   - Robust Pagination on heavy tables (Users, Tasks) to ensure scalability.

---

## Local Setup Instructions

### 1. Prerequisites
- Python 3.9+
- A Supabase Project
- A Google Cloud Service Account (with Sheets/Drive API enabled)
- A Gmail account with an App Password (for SMTP)

### 2. Install Dependencies
In your terminal, navigate to the project root and install the required packages:

```bash
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory and populate it with your credentials:

```env
# Flask Settings
SECRET_KEY=your_secure_random_string_here

# Supabase Credentials
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-key-here

# Google Sheets Config
# Paste the entire JSON string on one line, or export it in your environment
GOOGLE_CREDENTIALS_JSON={"type": "service_account", "project_id": "...", ...}
SPREADSHEET_ID=your_google_sheet_id_here

# Email Configuration (for automated notifications)
SMTP_SERVER=smtp-mail.outlook.com
SMTP_PORT=587
SMTP_USERNAME=your-email@outlook.com
SMTP_PASSWORD=your_app_password
MANAGER_NOTIFICATION_EMAIL=your-manager-notification-email@example.com
```

### 4. Database Setup
1. **Supabase (PostgreSQL)**: Ensure your Supabase project is set up. You **must** run the SQL migration scripts located in the root directory via your Supabase SQL Editor in the following order:
   - `supabase_migration.sql` (Creates base `users` table and sync triggers)
   - `supabase_unblock_rls.sql` (Disables/configures RLS for application access)
   - `supabase_migration_manager_invites.sql` (Adds invite-related columns)
   - `supabase_migration_password_reset.sql` (Adds password reset token columns)
2. **Google Sheets**: Ensure your Google Sheet has the following tabs created exactly as named, with matching headers as defined in `services/sheets_service.py`:
   - `Users`, `Tasks`, `Submissions`, `Attendance`, `Warnings`, `Leaves`, `Reports`, `Performance`, `Invites`

### 5. Run the Application
Start the Flask development server:

```bash
python app.py
```

The app will be available at `http://127.0.0.1:5000/`.

---

## Deployment (GitHub & Vercel)

This project is configured for seamless deployment on **Vercel** using Serverless Functions. 

### 1. Push to GitHub
Before deploying, push your code to a GitHub repository. **CRITICAL:** Do NOT upload your `.env` file or your Google Service Account JSON file (e.g., `attendance-portal-*.json`). Your `.gitignore` is already configured to exclude these, but ensure you don't force-add them.

Files/Folders to include:
- `models/`, `routes/`, `scripts/`, `services/`, `static/`, `templates/`
- `app.py`, `config.py`, `extensions.py`, `requirements.txt`, `vercel.json`
- `README.md`, `SETUP.md`, `.gitignore`, `.env.example`

### 2. Deploy on Vercel
1. Go to [Vercel](https://vercel.com/) and click **Add New Project**.
2. Import your newly created GitHub repository.
3. Open the **Environment Variables** section before clicking deploy.
4. Add **all** variables from your `.env` file:
   - `SECRET_KEY`
   - `SUPABASE_URL` and `SUPABASE_KEY`
   - `SPREADSHEET_ID`
   - `GOOGLE_CREDENTIALS_JSON` (Paste the entire minified JSON string as the value)
   - `SMTP_*` variables
5. Click **Deploy**. Vercel will use `vercel.json` to properly map `app.py` to the Python Serverless runtime.
