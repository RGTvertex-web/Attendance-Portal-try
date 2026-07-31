import sys
from services import sheets_service as ss
from services.pdf_service import generate_internship_report_pdf

print("Starting PDF generation test...", flush=True)
try:
    students = ss.get_all_students()
    print(f"Found {len(students)} students.", flush=True)
    success = 0
    for s in students:
        try:
            pdf_bytes = generate_internship_report_pdf(s, host_url="http://localhost:5000")
            print(f"SUCCESS: {s.get('name')} ({s.get('id')}) -> {len(pdf_bytes)} bytes", flush=True)
            success += 1
        except Exception as e:
            print(f"FAILED: {s.get('name')} ({s.get('id')}) -> {e}", flush=True)
            import traceback
            traceback.print_exc()
    print(f"Test complete: {success}/{len(students)} succeeded.", flush=True)
except Exception as e:
    print(f"Global failure: {e}", flush=True)
    import traceback
    traceback.print_exc()
