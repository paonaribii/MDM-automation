"""CMC Summary sheet — บันทึกคำขออนุมัติ Submit MDM (ผ่านฟอร์ม LINE @ufundpartners เมื่อ
สาขา/สาขาใกล้เคียงไม่มี MacBook ใช้ทำ MDM) ใช้ตรวจสอบว่า MDM Case ID ที่สาขาแจ้งมาใน
Helpdesk มีบันทึกอยู่จริงหรือไม่ (แทนการเชื่อข้อความอย่างเดียว).

Sheet: CMC_SUMMARY_SHEET_URL ใน .env
Tab: "เคสที่ไม่มีเครื่องลงโปรแกรม"

คอลัมน์ที่ใช้ (ยืนยันจากข้อมูลจริง):
  col 6:  เลขที่ใบคำขอ  ← ใช้ค้นหา
  col 11: Case ID       ← เช่น "MDM-260724-0008"
"""
from __future__ import annotations

from qa_automation.sheet_reader import read_tab_rows

_GID = "2035186734"
_COL_LOAN = 6
_COL_CASE_ID = 11


def read_cmc_summary_sheet(sheet_url: str, gid: str = _GID) -> dict[str, str]:
    """อ่าน CMC Summary sheet คืน dict {เลขที่ใบคำขอ: MDM Case ID}."""
    try:
        rows = read_tab_rows(sheet_url, gid)
    except Exception as e:
        print(f"[CMC Summary] Cannot read sheet: {e}")
        return {}

    result: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) <= max(_COL_LOAN, _COL_CASE_ID):
            continue
        loan = row[_COL_LOAN].strip()
        case_id = row[_COL_CASE_ID].strip()
        if loan and case_id:
            result[loan] = case_id
    return result


def find_cmc_case_id(loan_app_number: str, cmc_data: dict[str, str]) -> str:
    """คืน MDM Case ID ถ้าเลขที่ใบคำขอนี้มีบันทึกใน CMC Summary — คืน "" ถ้าไม่เจอ."""
    if not loan_app_number:
        return ""
    return cmc_data.get(loan_app_number.strip(), "")
