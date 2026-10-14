"""itec_desktop.py — ดึงข้อมูล ITEC สดจากโปรแกรม desktop "ITEC2007_Search"
(ITECStock2007.exe) แทนการอ่าน Google Sheet ที่ sync มา.

โปรแกรมนี้ควบคุมผ่าน pywinauto (UI Automation) ไม่ใช่ Playwright เพราะเป็น
Windows Forms desktop app ไม่ใช่เว็บ.

โครงสร้างที่ยืนยันจากการทดสอบจริง (login ด้วย username 31905):
- Path: C:\\Program Files (x86)\\Softbox\\ITEC2007_Search\\ITECStock2007.exe
- แต่ละรายงาน (Sell Bill View / Sale Inv View) ต้องเปิดโปรแกรมแยก instance กัน
  (login แยกกันคนละครั้ง) — เปิด instance เดียวแล้วสลับรายงานไม่ได้ (คลิกรายงาน
  ที่ 2 จะแทนที่หน้าต่าง Main Form เดิม ไม่ได้เปิดเพิ่ม)
- Login: หน้าต่าง "Get Password" (auto_id="GetPassword") มี Edit
  auto_id="tbUserName" / "tbPassword" — ไม่มีปุ่ม OK ให้เห็นใน UIA ต้องกด Enter
  ที่ช่อง password แทน
- หลัง login จะเจอ "ITECStock 2007 Main Form" (เมนูแบบ ribbon) ต้องคลิกแท็บ
  "Sell" แล้วคลิกปุ่มรายงานที่ต้องการ:
    - "Sell Bill View"  -> เปิดหน้าต่างชื่อ "...  (Sell Bill View)"
    - "Sale Inv View"   -> เปิดหน้าต่างชื่อ "... (Full Sale Invoice View)"
  ห้ามกดปุ่ม "Full Sale Inv" — ปุ่มนั้นเป็นหน้าจอ "ออกใบกำกับภาษีขายเต็มจากใบย่อ"
  (สร้างเอกสารใหม่ ไม่ใช่หน้าดูข้อมูล).
- ทั้งสองหน้าต่างรายงาน ค้นหาด้วยช่อง Comment (auto_id="tbComment") + ปุ่ม
  "Search" — Sell Bill View มีช่องนี้อยู่แท็บแรกเลย ส่วน Full Sale Invoice View
  ต้องคลิกแท็บ "เงื่อนไขเพิ่มเติม" ก่อนถึงจะเห็นช่อง Comment.
- ค่าเริ่มต้นของฟอร์ม (สำคัญมาก ไม่งั้นค้นหาไม่เจออะไรเลย แม้จะพิมพ์ถูก):
    - "Branch" (auto_id="tbBranch") default เป็นสาขาของ user ที่ login (เช่น
      "55" = Head Office) ไม่ใช่ "Not Specify Branch" — ต้องเคลียร์เป็น "0" ก่อน
    - ช่วงวันที่ "Start (CrTime)"/"Start (DocDate)" (Pane auto_id="dtStartDate"/
      "dtStartDocdate") บาง instance default แคบแค่วันเดียว (วันนี้) — ต้องขยาย
      ปีเริ่มต้นถอยหลัง 1 ปีก่อนค้นหา (คลิกที่ตัวเลขปีในตัวควบคุมแล้วกด Down)
- อ่านค่าจากตารางผลลัพธ์ (Table auto_id="dtG01"): แต่ละเซลล์เป็น DataItem ชื่อ
  f"Row{n}_{ColumnName}" — ค่าจริงที่แสดงอยู่ใน cell.legacy_properties()['Value']
  (ไม่ใช่ .name/.texts() ซึ่งเป็นแค่ accessible name).
- ตาราง Sell Bill View ไม่มีคอลัมน์ "เงินดาวน์ที่ลูกค้าจ่ายจริง"/"เงินดาวน์สุทธิ"
  ตรงๆ (ต่างจาก Google Sheet เดิมที่มีคอลัมน์คำนวณเพิ่มไว้) แต่ทั้ง 2 ค่านี้หาได้จาก
  2 แหล่งคนละที่กันในตารางจริง (ยืนยันจากเคสจริง ไม่ใช่ค่าเดียวกันเสมอไป — เจอเคส
  ที่ Comment บอกเงินดาวน์ 7,000 แต่ทุกคอลัมน์ชำระเงิน = 0.00 และ Finish="ยังรับเงิน
  ไม่ครบ" คือ ตกลงจะจ่าย 7,000 แต่ยังไม่ได้จ่ายจริงสักบาท ไม่ใช่ข้อมูลผิด ตรงกับที่
  ITOS เองก็แยก 2 ฟิลด์นี้จากคนละแถวใน DOA table เหมือนกัน):
    - เงินดาวน์สุทธิ (ยอดตกลง/สัญญา) = ตัวเลขท้าย Comment "เงินดาวน์ (X.XX)"
    - เงินดาวน์ที่จ่ายจริง (ยอดรับเข้าจริงแล้ว ณ ตอนนี้) = รวมทุกคอลัมน์วิธีชำระเงิน
      (เงินสด/บัตรเครดิต/เช็ค/โอนเงิน ฯลฯ) — ห้ามใช้คอลัมน์ "คูปอง (Coupon)" เป็น
      แหล่งเงินดาวน์เด็ดขาด (เจอเคสจริงที่พนักงานบันทึกเงินดาวน์ไว้ในคอลัมน์นี้แทน
      ทำให้เข้าใจผิดว่าเป็น field เงินดาวน์ได้ — คอลัมน์นี้จริงๆ ใช้เทียบ Trade-in).
- ตาราง dtG01 (แท็บแรก "เงื่อนไขเบื้องต้น") เป็นข้อมูลระดับ "บิล" เท่านั้น ไม่มี
  Product Name/Serial — ต้องคลิกแท็บ "เงื่อนไขเกี่ยวกับสินค้า (Product conditions)"
  ก่อนถึงจะเห็นตาราง dtG02 (ข้อมูลระดับ "รายการสินค้า") ที่มีคอลัมน์ "Product (Name)"
  และ "Serial" — ยืนยันจากข้อมูลจริงว่าค่าที่แสดงในคอลัมน์ "Serial" ของ ITEC คือ
  IMEI ของเครื่อง (ไม่ใช่ serial number) ตรงกับ imei_old ของ ITOS เป๊ะในทุกเคสที่
  ทดสอบ — ใช้ 2 คอลัมน์นี้แทนแท็บ "Comb." เดิมที่โปรแกรม desktop ไม่มี (เทียบ
  Product Name กับรุ่นสินค้าใน ITOS + ใช้ Serial เป็น IMEI fallback).
- เคยลอง minimize หน้าต่างระหว่างที่ไม่ได้ใช้ (กัน automation บังหน้าจอ/แย่ง focus
  จากงานอื่นของผู้ใช้) ทั้งแบบเรียกผ่าน pywinauto WindowSpecification ตรงๆ (พังทันที
  เพราะทุก attribute access ของมัน re-search หน้าต่างจาก title ทุกครั้ง และหน้าต่างที่
  minimize อยู่ถูกกรองออกจากผลค้นหาแบบ visible-window ของ UIA) และแบบใช้
  win32gui.ShowWindow() กับ HWND ตรงๆ (ผ่านได้ 8-9 รอบแรกแต่หน้าต่างเริ่มค้าง/เสียหลัง
  minimize-restore สลับไปมาซ้ำๆ หลายรอบ — เจอจริงระหว่างรันจริงกับเคสจำนวนมาก) —
  ทั้งสองวิธีเสี่ยงทำให้ข้อมูลเสียหาย จึงตัดออก ปล่อยให้หน้าต่างเปิดค้างไว้ปกติตลอด
  การรันแทน (แลกความสะดวกด้าน UI กับความน่าเชื่อถือของข้อมูล).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from pywinauto import Application

_EXE_PATH = r"C:\Program Files (x86)\Softbox\ITEC2007_Search\ITECStock2007.exe"

# Comment: "UFUND Personal/202607-074973/วราภรณ์ นนท์ยะโส/ เงินดาวน์ (0.00)"
_COMMENT_NAME_RE = re.compile(r"/\d{6}-\d{6}/([^/]+)/")
# บาง comment แนบเบอร์โทรลูกค้าต่อท้ายชื่อในช่องเดียวกัน (ไม่มี "/" คั่น) เช่น
# "สมชาย ใจดี 0812345678" — ต้องตัดเบอร์ทิ้งก่อนเทียบกับ Helpdesk/ITOS
# ที่ไม่มีเบอร์ติดมา (เบอร์มือถือไทย 9-10 หลัก ขึ้นต้นด้วย 0 เป็นปกติ) — เจอบั๊กนี้ครั้งแรก
# ตอนใช้ itec_sheet.py (ชีท) แล้วแก้ไปแล้ว แต่ลืม port มาที่ itec_desktop.py ตอนย้ายมาใช้
# ดึงสดจากโปรแกรม เจอเคสจริงซ้ำอีกจึงต้องแก้ที่นี่ด้วย
_TRAILING_PHONE_RE = re.compile(r"\s+0?\d{9,10}\s*$")

# ใช้เลือกแถวสินค้าจริงใน dtG02 (บิลเดียวอาจมีหลายแถว เช่น เครื่อง + เคส/ประกัน) — แถว
# ที่เป็นตัวเครื่องจริงจะมีความจุ (GB/TB) ระบุอยู่ใน Product (Name) เสมอ
_STORAGE_RE = re.compile(r"\d+\s*(?:GB|TB)", re.IGNORECASE)

# ค่าเงินดาวน์ตัวเลขจริงอยู่ท้าย Comment เสมอ "เงินดาวน์ (X.XX)" — ยืนยันจากเคสจริงว่า
# แม่นกว่าการรวมคอลัมน์วิธีชำระเงิน (เงินสด/บัตรเครดิต/เช็ค/โอนเงิน ฯลฯ) มาก เพราะพนักงาน
# บันทึกเงินดาวน์ผ่านช่องทางที่ไม่แน่นอน (เจอเคสจริงที่เงินดาวน์ถูกบันทึกไว้ในคอลัมน์
# "คูปอง (Coupon)" แทน ทำให้ผลรวมคอลัมน์ชำระเงินได้ 0.00 ทั้งที่ ITOS มีเงินดาวน์จริง)
# ใช้ค่านี้เป็นหลักเสมอ ถ้า Comment ไม่มี pattern นี้ค่อย fallback ไปรวมคอลัมน์ชำระเงินแทน
_DOWN_PAYMENT_RE = re.compile(r"เงินดาวน์\s*\(\s*([\d,]+\.?\d*)\s*\)")


def _extract_down_payment(comment: str) -> str:
    m = _DOWN_PAYMENT_RE.search(comment or "")
    if not m:
        return ""
    try:
        return f"{float(m.group(1).replace(',', '')):,.2f}"
    except ValueError:
        return ""


@dataclass
class ITECRecord:
    loan_app_number: str = ""

    # ── ITEC (Sell Bill View) ──────────────────────────────────────
    sbv_found: bool = False
    sbv_customer_name: str = ""
    sbv_branch_name: str = ""
    sbv_price_total: str = ""        # Total Price
    sbv_down_payment_net: str = ""       # เงินดาวน์สุทธิ (ยอดตกลง — จาก Comment)
    sbv_down_payment_actual: str = ""    # เงินดาวน์ที่จ่ายจริง (รวมคอลัมน์ชำระเงินจริง)
    sbv_coupon: str = ""              # คูปอง (Coupon)
    sbv_company_name: str = ""       # Customer (Name) — ต้องเป็นชื่อบริษัท
    sbv_product_name: str = ""       # Product (Name) — จากแท็บ "เงื่อนไขเกี่ยวกับสินค้า"
                                       # เทียบกับรุ่นสินค้าใน ITOS (แทน "Comb." ที่ตัดออก)
    sbv_serial: str = ""              # "Serial" — ค่าจริงคือ IMEI ของเครื่อง (ยืนยันจาก
                                       # ข้อมูลจริง ตรงกับ imei_old ของ ITOS เป๊ะ) ใช้เป็น
                                       # IMEI fallback แทน "Comb." IMEI ที่ตัดออก

    # ── ITEC (Sale Inv View) ────────────────────────────────────────
    siv_found: bool = False
    siv_customer_name: str = ""
    siv_branch_name: str = ""
    siv_price_total: str = ""        # Total Price After Discount
    siv_company_name: str = ""       # Customer(Name) (มักถูก mask ด้วย *****)
    siv_down_payment_net: str = ""   # เงินดาวน์สุทธิ — SIV Comment มี "เงินดาวน์ (X.XX)"
                                       # เหมือน SBV เช่นกัน (ยืนยันจากผู้ใช้) ใช้เทียบ
                                       # แบบ 3 แหล่ง (ITOS/SBV/SIV) เหมือนชื่อ-ราคา

    # ── Comb. ─────────────────────────────────────────────────────
    # ไม่มีในโปรแกรม desktop นี้ (ผู้ใช้ยืนยันตัดออก) — เก็บไว้ให้ rules.py ที่มี
    # logic "N/A ถ้ายังไม่มีข้อมูล Comb." ทำงานได้เหมือนเดิมโดยไม่ต้องแก้โค้ดที่นั่น
    comb_found: bool = False
    comb_product_name: str = ""
    comb_imei: str = ""


_PAYMENT_FIELD_KEYS = (
    "เงินสด  (Cash)",
    "เงินสุทธิบัตรเครดิต  (Net credit)",
    "เช็ค  (Cheque)",
    "เงินโอน (Remittance)",
    "ใบรับเงิน (Receipt)",
    "ใบรับมัดจำ (Deposit)",
    "คูปองเงินสด (Cash Coupon)",
    "สินเชื่อทรู",
    "สินเชื่อPayNext",
)


def _sum_fields(values: dict[str, str], keys: tuple[str, ...]) -> str:
    total = 0.0
    any_value = False
    for key in keys:
        raw = values.get(key, "")
        if not raw:
            continue
        try:
            total += float(raw.replace(",", ""))
            any_value = True
        except ValueError:
            continue
    return f"{total:,.2f}" if any_value else ""


def _sum_payment_fields(values: dict[str, str]) -> str:
    """เงินดาวน์ที่จ่ายจริง — รวมเฉพาะช่องทางที่เป็นเงินสด/เครดิตที่รับเข้าจริง ไม่รวม
    "คูปอง (Coupon)" (ผู้ใช้ยืนยัน — คูปองเป็นส่วนลด/เครดิต ไม่ใช่เงินที่ลูกค้าจ่ายจริง)."""
    return _sum_fields(values, _PAYMENT_FIELD_KEYS)


def _sum_payment_fields_with_coupon(values: dict[str, str]) -> str:
    """เงินดาวน์สุทธิ — รวมทุกช่องทางเหมือน _sum_payment_fields บวก "คูปอง (Coupon)"
    เข้าไปด้วย (ผู้ใช้ยืนยัน — เงินดาวน์สุทธิคือยอดรวมมูลค่าที่ตัดเข้าเงินดาวน์ทั้งหมด
    รวมเครดิต/คูปองด้วย ต่างจากเงินดาวน์ที่จ่ายจริงซึ่งนับเฉพาะเงินสด/เครดิตจริง) ค่านี้
    ควรตรงกับตัวเลขท้าย Comment "เงินดาวน์ (X.XX)" อยู่แล้ว (ยืนยันจากเคสจริง)."""
    return _sum_fields(values, _PAYMENT_FIELD_KEYS + ("คูปอง (Coupon)",))


class ITECWindow:
    """1 instance ของ ITECStock2007.exe ที่ login + เปิดรายงานเดียวไว้แล้ว.

    product_tab (ถ้าตั้งไว้ — ใช้เฉพาะ Sell Bill View): หลัง Search แล้วจะคลิกแท็บนี้
    เพิ่มเพื่ออ่านตาราง product_table_id (dtG02) ที่มี Product (Name)/Serial ระดับ
    รายการสินค้า (ตาราง dtG01 มีแค่ข้อมูลระดับบิล ไม่มี 2 คอลัมน์นี้)."""

    def __init__(
        self,
        window,
        comment_tab: str | None,
        product_tab: str | None = None,
        product_table_id: str = "dtG02",
    ) -> None:
        self.window = window
        self._comment_tab = comment_tab
        self._product_tab = product_tab
        self._product_table_id = product_table_id

    def _read_all_rows(self, auto_id: str) -> dict[int, dict[str, str]]:
        table = self.window.child_window(auto_id=auto_id, control_type="Table")
        rows: dict[int, dict[str, str]] = {}
        for cell in table.descendants():
            name = cell.element_info.name
            m = re.match(r"Row(\d+)_(.+)", name)
            if not m:
                continue
            row_idx = int(m.group(1))
            col = m.group(2)
            try:
                value = cell.legacy_properties().get("Value", "") or ""
            except Exception:
                value = ""
            rows.setdefault(row_idx, {})[col] = value
        return rows

    def _read_product_row(self, auto_id: str) -> dict[str, str]:
        """อ่านตารางรายการสินค้า (dtG02) — บิลเดียวอาจมีหลายแถว (เช่น เครื่อง + เคส/
        ประกัน) ต้องเลือกเฉพาะแถวที่เป็นตัวเครื่องจริง (มีความจุระบุใน Product (Name))
        ไม่ใช่ Row0 เสมอไป — ยืนยันจากที่ผู้ใช้ระบุว่าให้ "เอาเฉพาะแถวที่มี ชื่อรุ่น
        ความจุ สี สินค้า" ไม่ใช่แถวแรกเฉยๆ."""
        rows = self._read_all_rows(auto_id)
        if not rows:
            return {}

        for idx in sorted(rows):
            product_name = rows[idx].get("Product (Name)", "")
            if product_name and _STORAGE_RE.search(product_name):
                return rows[idx]
        # ไม่มีแถวไหนมีความจุระบุ — fallback ไปแถวแรกที่มีชื่อสินค้าอย่างน้อย
        for idx in sorted(rows):
            if rows[idx].get("Product (Name)"):
                return rows[idx]
        return rows[sorted(rows)[0]]

    def _read_bill_row(self, auto_id: str) -> dict[str, str]:
        """อ่านตารางระดับบิล (dtG01) — ค้นด้วยเลขที่ใบคำขอบางครั้งเจอมากกว่า 1 บิล
        เพราะ Comment มีเลขที่ใบคำขอซ้ำกันได้ (เช่น บิลเครื่องหลัก + บิลอุปกรณ์เสริม/
        ค่าธรรมเนียมแยกต่างหากที่ผูก "phy id" ของบิลหลักไว้ในคอมเมนต์) ต้องเลือกเฉพาะ
        บิลที่เป็นรายการซื้อเครื่องหลักจริง (Customer(Name) ต้องมีคำว่า "ธันเดอร" ซึ่ง
        เป็นชื่อบริษัทลูกค้าเงินผ่อน ไม่ใช่ลูกค้าปลีกทั่วไป) ไม่ใช่แถวแรกเสมอไป — เจอเคส
        จริงที่แถวแรกเป็นบิลอุปกรณ์เสริมแยกต่างหาก ราคา/ชื่อลูกค้าไม่ตรงกับ ITOS เลย."""
        rows = self._read_all_rows(auto_id)
        if not rows:
            return {}

        for idx in sorted(rows):
            if any("ธันเดอร" in v for v in rows[idx].values()):
                return rows[idx]
        return rows[sorted(rows)[0]]

    def search(self, loan_app_number: str) -> dict[str, str]:
        """ค้นหาด้วยเลขที่ใบคำขอ คืน dict {ColumnName: value} ของแถวแรกที่เจอ
        (หรือ dict ว่างถ้าไม่เจอ).

        เคยลอง minimize หน้าต่างไว้นอกช่วงค้นหาแล้ว restore กลับเป็นช่วงๆ (กันบัง/แย่ง
        focus จากงานอื่นของผู้ใช้) แต่เจอจริงระหว่างรันจริงว่าทำให้หน้าต่างเสีย/ค้างหลัง
        minimize-restore สลับไปมาซ้ำๆ หลายรอบ (ประมาณเคสที่ 8-9 ขึ้นไปเริ่มพัง) —
        ความเสี่ยงต่อความถูกต้องของข้อมูลสำคัญกว่าความสะดวกด้าน UI จึงตัดออก ให้หน้าต่าง
        เปิดค้างไว้ปกติตลอดการรันแทน"""
        win = self.window
        win.set_focus()
        if self._comment_tab:
            try:
                win.child_window(title=self._comment_tab, control_type="TabItem").click_input()
                time.sleep(0.5)
            except Exception:
                pass

        comment = win.child_window(auto_id="tbComment", control_type="Edit")
        comment.set_edit_text(loan_app_number)
        win.child_window(title="Search", control_type="Button").click_input()
        time.sleep(2.5)

        result = self._read_bill_row("dtG01")

        # กันตารางยังไม่ทันรีเฟรชผลค้นหาใหม่ (โปรแกรมช้าลงหลังใช้งานต่อเนื่องนานๆ —
        # เจอจริงระหว่างรันจริงว่า Comment ที่อ่านได้เป็นของ "เคสก่อนหน้า" ที่ค้นไปแล้ว
        # ไม่ใช่เคสปัจจุบัน ทั้งที่ table.descendants() อ่านได้ปกติไม่ error เลย เงียบๆ
        # ได้ข้อมูลผิดคนไปโดยไม่รู้ตัว) — ตรวจว่า Comment มีเลขที่ใบคำขอที่พึ่งค้นจริงๆ
        # ถ้าไม่มี (แปลว่าตารางยังไม่รีเฟรช) รออีกแล้วอ่านซ้ำ — เจอเคสจริงว่ารอบ 2 ครั้ง
        # (2 วิ x 2) ยังไม่พอเวลาโปรแกรมช้าลงมากๆ จึงเพิ่มเป็น 4 ครั้ง (2.5 วิ x 4)
        for _ in range(4):
            if not result.get("Comment") or loan_app_number in result["Comment"]:
                break
            time.sleep(2.5)
            result = self._read_bill_row("dtG01")

        if self._product_tab:
            try:
                win.child_window(title=self._product_tab, control_type="TabItem").click_input()
                time.sleep(0.5)
                result.update(self._read_product_row(self._product_table_id))
            except Exception:
                pass

        return result


def _login_instance(username: str, password: str) -> Application | None:
    """เปิด ITECStock2007.exe instance ใหม่ + login คืน Application (ที่หน้า Main Form)
    หรือ None ถ้า login ไม่สำเร็จ."""
    app = Application(backend="uia").start(_EXE_PATH)

    get_password = None
    for _ in range(20):
        time.sleep(1)
        try:
            get_password = app.window(title="Get Password")
            if get_password.exists():
                break
        except Exception:
            pass
    if get_password is not None and get_password.exists():
        get_password.set_focus()
        get_password.child_window(auto_id="tbUserName", control_type="Edit").set_edit_text(username)
        pf = get_password.child_window(auto_id="tbPassword", control_type="Edit")
        pf.set_edit_text(password)
        time.sleep(0.5)
        pf.type_keys("{ENTER}")

    main_form = None
    for _ in range(20):
        time.sleep(1)
        try:
            mf = app.window(title="ITECStock 2007 Main Form")
            if mf.exists():
                main_form = mf
                break
        except Exception:
            pass
    if main_form is None:
        return None
    return app


def _open_report(app: Application, button_title: str, window_title_contains: str):
    """คลิกแท็บ Sell -> ปุ่มรายงาน แล้วรอหน้าต่างรายงานเปิด คืน window wrapper.

    ribbon เมนูของแอปนี้ตอบสนองช้าไม่คงที่ (เจอ ElementNotFoundError เป็นครั้งคราว
    ถ้าคลิกปุ่มรายงานเร็วเกินไปหลังคลิกแท็บ Sell) จึง retry ทั้ง 2 ขั้นตอน."""
    main_form = app.window(title="ITECStock 2007 Main Form")
    main_form.wait("ready", timeout=15)
    main_form.set_focus()

    for attempt in range(3):
        try:
            main_form.child_window(title="Sell", control_type="TabItem").click_input()
            time.sleep(1.5)
            main_form.child_window(title=button_title, control_type="Button").click_input()
            time.sleep(2)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)

    for _ in range(10):
        for w in app.windows():
            if window_title_contains in w.window_text():
                # .windows() คืน UIAWrapper ดิบ (ไม่มี .child_window()) — ต้องแปลงเป็น
                # WindowSpecification ผ่าน app.window(title=...) เพื่อใช้ child_window ได้
                return app.window(title=w.window_text())
        time.sleep(1)
    return None


def _reset_filters(win) -> None:
    """เคลียร์ Branch เป็น 0 (ไม่ระบุสาขา) และขยายช่วงวันที่เริ่มต้นถอยหลัง 1 ปี —
    ค่า default ของฟอร์ม (สาขา user ที่ login + ช่วงวันที่แคบแค่วันนี้) ทำให้ค้นหา
    ไม่เจอข้อมูลเลยถ้าไม่ล้างก่อน (ยืนยันจากการทดสอบจริง)."""
    win.set_focus()
    try:
        branch_field = win.child_window(auto_id="tbBranch", control_type="Edit")
        branch_field.set_edit_text("0")
        win.type_keys("{TAB}")
        time.sleep(0.3)
    except Exception:
        pass

    win_rect = win.rectangle()
    for pane_id in ("dtStartDate", "dtStartDocdate"):
        try:
            pane = win.child_window(auto_id=pane_id, control_type="Pane")
            r = pane.rectangle()
            x = (r.left - win_rect.left) + max(r.width() - 40, 10)
            y = (r.top - win_rect.top) + r.height() // 2
            win.click_input(coords=(x, y))
            time.sleep(0.2)
            win.type_keys("{DOWN}")
            time.sleep(0.3)
        except Exception:
            pass


class ITECDesktopClient:
    """Client สำหรับโปรแกรม ITEC desktop — เปิด 2 instance (Sell Bill View / Sale
    Inv View) แยกกันตอน login แล้ว search() ทั้งคู่ทุกเคส.

    เก็บ Application object ทั้ง 2 ตัวไว้เพื่อปิดทิ้งให้ถูกต้องตอนจบงาน (close()) — ถ้า
    ไม่ปิด process ITECStock2007.exe จะค้างอยู่เต็มเครื่องทุกครั้งที่รัน จนล็อกอินไม่ผ่าน
    เพราะเกินจำนวน session/instance ที่โปรแกรมรองรับพร้อมกัน (เจอจริงระหว่างพัฒนา — สะสม
    ทดสอบไว้ 24 instance แล้วล็อกอินรอบถัดไปล้มเหลวทันที)."""

    def __init__(self) -> None:
        self._sbv: ITECWindow | None = None
        self._siv: ITECWindow | None = None
        self._sbv_app: Application | None = None
        self._siv_app: Application | None = None
        self._username = ""
        self._password = ""

    def login(self, username: str, password: str) -> bool:
        self._username = username
        self._password = password

        sbv_app = _login_instance(username, password)
        if sbv_app is None:
            return False
        sbv_win = _open_report(sbv_app, "Sell Bill View", "Sell Bill View")
        if sbv_win is None:
            return False
        _reset_filters(sbv_win)
        self._sbv_app = sbv_app
        self._sbv = ITECWindow(
            sbv_win, comment_tab=None,
            product_tab="เงื่อนไขเกี่ยวกับสินค้า (Product conditions)",
        )

        siv_app = _login_instance(username, password)
        if siv_app is None:
            return False
        siv_win = _open_report(siv_app, "Sale Inv View", "Full Sale Invoice View")
        if siv_win is None:
            return False
        _reset_filters(siv_win)
        self._siv_app = siv_app
        self._siv = ITECWindow(siv_win, comment_tab="เงื่อนไขเพิ่มเติม")

        return True

    def _relaunch_sbv(self) -> bool:
        """เปิด instance ใหม่แทนตัวที่พัง — เจอจริงระหว่างรันจริงว่าหน้าต่าง Sell Bill
        View บางครั้ง (ไม่ทุกครั้ง นับไม่ได้ว่ากี่เคสถึงจะพัง) หยุดตอบสนองจน pywinauto
        หาไม่เจอ (ElementNotFoundError) หลังใช้งานต่อเนื่องนานๆ — เหมือนปัญหา MDM session
        degradation ที่เคยเจอมาก่อน แก้ด้วยวิธีเดียวกัน: kill แล้วเปิดใหม่แทนที่จะพยายาม
        ซ่อมหน้าต่างเดิม."""
        if self._sbv_app is not None:
            try:
                self._sbv_app.kill()
            except Exception:
                pass
        sbv_app = _login_instance(self._username, self._password)
        if sbv_app is None:
            return False
        sbv_win = _open_report(sbv_app, "Sell Bill View", "Sell Bill View")
        if sbv_win is None:
            return False
        _reset_filters(sbv_win)
        self._sbv_app = sbv_app
        self._sbv = ITECWindow(
            sbv_win, comment_tab=None,
            product_tab="เงื่อนไขเกี่ยวกับสินค้า (Product conditions)",
        )
        return True

    def _relaunch_siv(self) -> bool:
        if self._siv_app is not None:
            try:
                self._siv_app.kill()
            except Exception:
                pass
        siv_app = _login_instance(self._username, self._password)
        if siv_app is None:
            return False
        siv_win = _open_report(siv_app, "Sale Inv View", "Full Sale Invoice View")
        if siv_win is None:
            return False
        _reset_filters(siv_win)
        self._siv_app = siv_app
        self._siv = ITECWindow(siv_win, comment_tab="เงื่อนไขเพิ่มเติม")
        return True

    def _search_with_recovery(self, loan_app_number: str, which: str) -> dict[str, str]:
        """ค้นหา 1 รายงาน (SBV หรือ SIV) พร้อม auto-recovery ถ้าหน้าต่างค้าง — retry
        สูงสุด 2 ครั้ง (เปิด instance ใหม่ทุกครั้งที่ retry) เพราะเจอจริงระหว่างพัฒนาว่า
        บางครั้ง relaunch ครั้งแรกแล้ว search ทันทีก็ยังเจอหน้าต่างค้างซ้ำได้อีก (แค่
        retry เดียวไม่พอ)."""
        window = self._sbv if which == "sbv" else self._siv
        relaunch = self._relaunch_sbv if which == "sbv" else self._relaunch_siv
        label = "Sell Bill View" if which == "sbv" else "Sale Inv View"

        if window is None:
            return {}

        for attempt in range(3):
            try:
                return window.search(loan_app_number)
            except Exception as exc:
                if attempt == 2:
                    logging.warning("[ITEC] %s ค้างซ้ำเกิน 2 ครั้ง ยอมแพ้ (%s)", label, exc)
                    return {}
                logging.warning("[ITEC] %s window ค้าง (%s) — เปิด instance ใหม่แล้วลองซ้ำ (รอบ %d)",
                                 label, exc, attempt + 1)
                if not relaunch():
                    return {}
                window = self._sbv if which == "sbv" else self._siv
        return {}

    def close(self) -> None:
        """ปิดทั้ง 2 instance ให้ถูกต้อง — ต้องเรียกเสมอตอนจบงาน (เช่นใน finally ของ
        main.py) กัน process ค้างสะสมจนล็อกอินรอบถัดไปล้มเหลว."""
        for app in (self._sbv_app, self._siv_app):
            if app is None:
                continue
            try:
                app.kill()
            except Exception:
                pass
        self._sbv = None
        self._siv = None
        self._sbv_app = None
        self._siv_app = None

    def search(self, loan_app_number: str) -> ITECRecord:
        rec = ITECRecord(loan_app_number=loan_app_number)

        if self._sbv is not None:
            row = self._search_with_recovery(loan_app_number, "sbv")
            if row.get("Comment"):
                rec.sbv_found = True
                m = _COMMENT_NAME_RE.search(row["Comment"])
                sbv_name = m.group(1).strip() if m else ""
                rec.sbv_customer_name = _TRAILING_PHONE_RE.sub("", sbv_name).strip()
                rec.sbv_branch_name = row.get("Branch (Name)", "")
                rec.sbv_price_total = row.get("Total Price", "")
                rec.sbv_coupon = row.get("คูปอง (Coupon)", "")
                rec.sbv_company_name = row.get("Customer (Name)", "")
                # เงินดาวน์สุทธิ = รวมทุกคอลัมน์วิธีชำระเงิน + คูปอง (Coupon) — ควรตรงกับ
                # ตัวเลขท้าย Comment "เงินดาวน์ (X.XX)" อยู่แล้ว ใช้ผลรวมเป็นหลัก (โครงสร้าง
                # ข้อมูลเชื่อถือได้กว่า regex parse ข้อความอิสระ) แล้ว fallback ไป Comment
                # เฉพาะกรณีผลรวมว่างเปล่า (ไม่มีคอลัมน์ไหนมีค่าเลย)
                # เงินดาวน์ที่จ่ายจริง = รวมเหมือนกันแต่ไม่รวมคูปอง (คูปองเป็นเครดิต/ส่วนลด
                # ไม่ใช่เงินที่ลูกค้าจ่ายจริง) — ยืนยันจากเคสจริงว่าเป็นคนละค่ากันได้จริง
                # (เคสที่คูปอง=7,000 ทุกคอลัมน์ชำระเงินอื่น=0.00 และ Finish="ยังรับเงิน
                # ไม่ครบ" หมายถึงตกลงจะจ่าย 7,000 แต่ยังไม่ได้จ่ายจริงสักบาท ไม่ใช่ข้อมูล
                # ผิด ตรงกับที่ ITOS เองก็แยก 2 ฟิลด์นี้จากคนละแถวใน DOA table เหมือนกัน)
                sum_with_coupon = _sum_payment_fields_with_coupon(row)
                rec.sbv_down_payment_net = sum_with_coupon or _extract_down_payment(row["Comment"])
                rec.sbv_down_payment_actual = _sum_payment_fields(row)
                rec.sbv_product_name = row.get("Product (Name)", "")
                rec.sbv_serial = row.get("Serial", "")

        if self._siv is not None:
            row = self._search_with_recovery(loan_app_number, "siv")
            if row.get("Comment"):
                rec.siv_found = True
                m = _COMMENT_NAME_RE.search(row["Comment"])
                siv_name = m.group(1).strip() if m else ""
                rec.siv_customer_name = _TRAILING_PHONE_RE.sub("", siv_name).strip()
                rec.siv_branch_name = row.get("Branch(Name)", "")
                rec.siv_price_total = row.get("Total Price After Discount", "")
                rec.siv_company_name = row.get("Customer(Name)", "")
                rec.siv_down_payment_net = _extract_down_payment(row["Comment"])

        return rec
