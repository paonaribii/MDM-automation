"""sheet_reader.py — อ่านแท็บของ Google Sheet ผ่าน Sheets API ด้วย service account (ไฟล์เดียวกับ
sheet_writer.py) แทนการโหลด CSV export แบบไม่ login — ชีทต้นทางจึงตั้งเป็น "จำกัด" ได้ ไม่ต้องเปิด
"ทุกคนที่มีลิงก์" (แค่แชร์ให้ email ของ service account เป็นผู้มีสิทธิ์อ่าน)."""
from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

from qa_automation.config import env

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def read_tab_rows(sheet_url: str, gid: str | int) -> list[list[str]]:
    """คืนทุกแถวของแท็บ gid เป็นข้อความตามที่แสดงในชีท (เหมือนที่ CSV export ให้)."""
    creds_file = env("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not creds_file:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE is not set in .env")
    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    worksheet = gspread.authorize(creds).open_by_url(sheet_url).get_worksheet_by_id(int(gid))
    return worksheet.get_all_values()
