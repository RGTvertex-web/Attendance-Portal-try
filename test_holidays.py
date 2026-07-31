import os
import json
from dotenv import load_dotenv
load_dotenv()
from services.sheets_service import get_all_holidays
with open('holidays_output.txt', 'w') as f:
    f.write(json.dumps(get_all_holidays()))
