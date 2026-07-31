import os
from dotenv import load_dotenv
load_dotenv()
from services.sheets_service import add_holiday, get_all_holidays
print("Before adding:", get_all_holidays())
success = add_holiday("2026-12-25", "Christmas")
print("Add success?", success)
print("After adding:", get_all_holidays())
