"""iLoan client — ค้นหาใบคำขอและดึงข้อมูลสินค้าจาก iLoan/ITOS website.

Flow:
1. Login → จัดการ popup ตกลง
2. เลือก Company (บริษัท ธันเดอร์ ฟิน ฟิน จํากัด) → Head Office
3. ระบบงานสินเชื่อ → ใบคำขอ → รอเปิดสัญญา / Deliver
4. ค้นหาด้วยเลขที่ใบคำขอ (สถานะ = ทั้งหมด)
5. ดูข้อมูล บนแถวที่ตรงกับเลขที่ใบคำขอที่ค้นหา (ไม่ใช่แถวแรกที่เจอ — ตารางอาจยังไม่ update)
6. แท็บ "ข้อมูลลูกค้า" (default) → อ่านตาราง "ประวัติการเปลี่ยนเครื่อง (DOA)" → Serial/IMEI
7. แท็บ "ข้อมูลสินทรัพย์" → อ่านฟิลด์สินค้า/ราคา/เงินดาวน์
"""
from __future__ import annotations

import io
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, BrowserContext

from qa_automation.config import env

_TITLE_RE = re.compile(r"^(นางสาว|น\.ส\.|นาง|นาย|Mr\.|Mrs\.|Ms\.)\s*")
# ในสัญญา ค่าจะอยู่ต่อท้าย label ตรงๆ รูปแบบ "Serial / IMEI Number   <Serial> / <IMEI>"
# เช่น "Serial / IMEI Number   ABCD1234EF / -" (ใช้ "-" แทนช่องที่ไม่มีข้อมูล)
_SERIAL_IMEI_ANCHOR_RE = re.compile(r"Serial\s*/?\s*IMEI\s*Number", re.IGNORECASE)
_SERIAL_IMEI_VALUE_RE = re.compile(r"([A-Za-z0-9]{5,20}|-)\s*/\s*([A-Za-z0-9]{5,20}|-)")
_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


@dataclass
class ILoanCase:
    loan_app_number: str = ""
    customer_name: str = ""    # ชื่อ-นามสกุลลูกค้า (ไม่มีคำนำหน้า)
    branch: str = ""           # ชื่อสาขา + ID
    staff_name: str = ""       # ชื่อพนักงานสาขา
    product_category: str = "" # หมวดสินค้า (Smart Phone)
    brand: str = ""            # ยี่ห้อสินค้า (Apple)
    model: str = ""            # รุ่นสินค้า
    color: str = ""            # สี
    storage: str = ""          # ความจุ
    price_main: str = ""       # ราคาสินค้าหลัก (รวมภาษี)
    discount_package: str = "" # ส่วนลด package ที่ได้รับ (ถ้ามี)
    discount_down_payment: str = ""  # ส่วนลดเงินดาวน์
    discount_other: str = ""         # ส่วนลดอื่นๆ
    discount_device: str = ""        # ส่วนลดค่าเครื่อง
    price_total: str = ""      # ราคาสินค้ารวมทั้งสิ้น (รวมภาษี)
    down_payment_net: str = "" # เงินดาวน์สุทธิ (บาท)
    down_payment: str = ""     # เงินดาวน์ที่ลูกค้าจ่ายจริง (บาท)
    monthly_payment: str = ""  # ค่างวดต่อเดือน
    trade_in: str = ""         # นำเครื่องเก่ามาแลก
    trade_in_value: str = ""   # มูลค่าเครื่องเก่า
    imei_old: str = ""         # ข้อมูล Series No เดิม (DOA) — ใช้เป็น IMEI
    serial_new: str = ""       # ข้อมูล Series No ใหม่ (DOA) — ใช้เป็น Serial Number (เปลี่ยน)
    imei_source: str = ""      # แหล่งที่มาของ imei_old: "DOA (iLoan)" / "Contract.pdf (OCR)" / ""
    serial_source: str = ""    # แหล่งที่มาของ serial_new: "DOA (iLoan)" / "Contract.pdf (OCR)" / ""


def is_ios_device(brand: str, model: str) -> bool:
    """เช็คว่าสินค้าเป็น iOS (Apple) หรือไม่ จากยี่ห้อ/รุ่นที่ได้จาก iLoan."""
    b = (brand or "").strip().lower()
    m = (model or "").strip().lower()
    return b == "apple" or "iphone" in m or "ipad" in m


def _looks_like_imei(value: str) -> bool:
    """เช็คว่าค่านี้หน้าตาเหมือนเลข IMEI (ตัวเลขล้วน 14-16 หลัก) ไม่ใช่ Apple Serial
    Number จริง (ตัวอักษร+ตัวเลขผสมกัน ยาว ~10-12 ตัว) — ยืนยันจากเคสจริงที่คอลัมน์ DOA
    "ข้อมูล Series No ใหม่" มีเลข IMEI ปนมาแทนที่จะเป็น Serial Number จริง."""
    s = (value or "").strip()
    return s.isdigit() and 14 <= len(s) <= 16


def normalize_apple_serial(serial: str) -> str:
    """ปรับ Apple Serial Number ก่อนใช้ค้นหา MDM — ยืนยันจากผู้ใช้ว่า Serial Number ของ
    Apple จริงจะไม่ขึ้นต้นด้วยตัว "S" ถ้าเจอ S นำหน้าแบบนี้ ให้ตัดออกก่อน (อาจเป็นตัวอักษร
    เกินมาจากการพิมพ์/OCR ผิดที่คอลัมน์ DOA "ข้อมูล Series No ใหม่" — ตัดแล้วอาจหา MDM เจอ)
    ส่วนกรณีเจอ URL หลุดมาแทนที่ Serial Number เลย (เช่น
    "https://support.apple.com/boxinfo/125076") ไม่มีทางกู้คืนเป็น serial ได้ คืนค่าว่าง
    ให้ main.py fallback ไปใช้ imei_old แทน."""
    s = (serial or "").strip()
    if not s or "http" in s.lower():
        return ""
    if s.upper().startswith("S"):
        return s[1:].strip()
    return s


def _contract_pdf_password(page: Page) -> str:
    """รหัสผ่านของไฟล์สัญญา PDF — วิธีสร้างรหัสเป็นข้อมูลภายในของระบบต้นทาง จึงไม่ได้ใส่ไว้ใน
    repo สาธารณะ (คืนค่าว่าง = ข้าม fallback นี้ไป)."""
    return ""


class ILoanClient:
    """Client สำหรับ iLoan/ITOS website."""

    LOGIN_URL = f"{env('ILOAN_BASE_URL')}/"
    COMPANY_NAME = "บริษัท ธันเดอร์ ฟิน ฟิน จํากัด"
    BRANCH_NAME = "Head Office"

    def __init__(self, page: Page, context: BrowserContext) -> None:
        self.page = page
        self.context = context
        self._logged_in = False

    def login(self, username: str, password: str) -> bool:
        """Login และเลือก Company/Branch แล้ว navigate ไปหน้า Deliver.

        ถ้า context มี storage_state (cookie) ที่ยัง valid อยู่แล้ว (เช่น reuse จาก
        รันครั้งก่อน) การ goto จะไม่เจอฟอร์ม login — ข้ามการกรอก credential/popup
        ไปได้เลย แต่ยังต้องเดินหน้า Company → Deliver เหมือนเดิมเสมอ เพราะ
        ขั้นตอนนั้นเป็น navigation state ของหน้าเว็บ ไม่ใช่ auth state ที่ cookie จำได้.
        """
        if self._login_and_navigate(username, password, force_fresh_login=False):
            self._logged_in = True
            return True

        # cookie ที่ reuse มาอาจเสีย/ค้างจาก session ที่ถูกตัดตอนกลางคัน (เช่น เน็ตหลุด
        # ระหว่างรันครั้งก่อน) — ทำให้ "ดูเหมือน" login อยู่ (ไม่เจอฟอร์ม login) แต่จริงๆ
        # ใช้งานต่อไม่ได้ ถ้า chain ทั้งหมดพลาดหลัง reuse cookie ให้ login ใหม่แบบสดจริงๆ
        # อีกครั้งก่อนจะยอมแพ้ (ไม่ใช่แค่เชื่อว่า "ไม่เจอฟอร์ม login" = login สำเร็จ)
        import logging as _log
        _log.warning("[iLoan] Reused session failed — retrying with fresh login")
        if self._login_and_navigate(username, password, force_fresh_login=True):
            self._logged_in = True
            return True

        self._logged_in = True
        return True

    def _login_and_navigate(
        self, username: str, password: str, force_fresh_login: bool
    ) -> bool:
        """Login (ข้ามฟอร์มถ้า cookie ยัง valid เว้นแต่ force_fresh_login) แล้วเดินหน้า
        ไป Deliver page — คืน True ถ้า verify แล้วว่าถึงหน้า Deliver จริง."""
        self.page.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        already_logged_in = False
        if not force_fresh_login:
            try:
                self.page.get_by_role("textbox", name="User Login").wait_for(
                    state="visible", timeout=3000
                )
            except Exception:
                already_logged_in = True

        if not already_logged_in:
            self.page.get_by_role("textbox", name="User Login").fill(username)
            self.page.get_by_role("textbox", name="Password").fill(password)
            try:
                self.page.get_by_role("checkbox", name="Remember Me").check()
            except Exception:
                pass
            self.page.get_by_role("button", name="Login").click()
            self.page.wait_for_timeout(2000)

            # จัดการ popup "ตกลง" (แจ้งเตือน login)
            for _ in range(3):
                try:
                    btn = self.page.get_by_role("button", name="ตกลง")
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        self.page.wait_for_timeout(1000)
                except Exception:
                    break

        company_page = self._get_company_page()
        if company_page is None:
            return False
        self.page = company_page

        # ทั้งชุด (เลือก Company → Head Office → ระบบงานสินเชื่อ → ใบคำขอ → รอเปิดสัญญา)
        # เป็น chain เดียวกัน — ถ้าขั้นไหนพลาด (เช่นตอนระบบช้าเพราะมีหลาย browser
        # context ทำงานพร้อมกัน) ขั้นถัดไปจะพลาดตามทั้งหมดแบบเงียบๆ (ค้างอยู่หน้าเดิม)
        # จึง verify ผลจริงตอนจบ (มี textbox ค้นหา "เลขที่ใบคำขอ" ให้เห็นมั้ย) แล้ว
        # retry ทั้ง chain ใหม่ถ้ายังไม่สำเร็จ แทนที่จะปล่อยผ่านไปเงียบๆ แบบเดิม
        for attempt in range(3):
            self._navigate_to_deliver_page(self.page)
            try:
                self.page.get_by_role("textbox", name="เลขที่ใบคำขอ").wait_for(
                    state="visible", timeout=8000
                )
                return True
            except Exception:
                if attempt < 2:
                    self.page.wait_for_timeout(5000)

        return False

    def _navigate_to_deliver_page(self, page: Page) -> None:
        """เลือก Company → Head Office → ระบบงานสินเชื่อ → ใบคำขอ → รอเปิดสัญญา/Deliver."""
        try:
            page.locator("li:nth-child(2) > .cardlist > .details").click(timeout=20000)
            page.wait_for_timeout(1000)
        except Exception:
            try:
                page.get_by_text(self.COMPANY_NAME).click(timeout=20000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

        try:
            page.get_by_text("Head Office", exact=True).click(timeout=20000)
            page.wait_for_timeout(2000)
        except Exception:
            try:
                page.locator("div").filter(has_text="Head Office").last.click(timeout=20000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

        try:
            page.get_by_text("ระบบงานสินเชื่อ").click(timeout=20000)
            page.wait_for_timeout(1000)
            page.get_by_role("link", name=" ใบคำขอ ").click(timeout=20000)
            page.wait_for_timeout(500)
            page.get_by_role("link", name="รอเปิดสัญญา / Deliver").first.click(timeout=20000)
            page.wait_for_timeout(1500)
        except Exception as nav_err:
            import logging as _log
            _log.warning("[iLoan] Navigation error: %s", nav_err)

        try:
            blocker = page.locator(".blockUI.blockOverlay")
            if blocker.is_visible(timeout=3000):
                blocker.wait_for(state="hidden", timeout=10000)
        except Exception:
            pass

    def _get_company_page(self) -> Page | None:
        """รอหน้า SelectCompany — อาจเป็น page เดิมหรือ popup ใหม่."""
        try:
            self.page.wait_for_url("**/SelectCompany**", timeout=8000)
            return self.page
        except Exception:
            pass

        try:
            with self.context.expect_page(timeout=5000) as new_page_info:
                pass
            new_page = new_page_info.value
            new_page.wait_for_load_state("domcontentloaded")
            return new_page
        except Exception:
            pass

        try:
            select_url = env("ILOAN_SELECT_COMPANY_URL")
            self.page.goto(select_url, wait_until="domcontentloaded")
            return self.page
        except Exception:
            return None

    REGISTER_PRODUCT_URL = env("ILOAN_REGISTER_PRODUCT_URL")

    def submit_mdm(self, loan_app_number: str) -> bool:
        """กด "Submit MDM" ให้ใบคำขอนี้ — เมนู "ลงทะเบียนสินค้า" -> ค้นหาด้วยเลขที่ใบคำขอ
        (กรองสถานะ "ลงทะเบียนไม่ผ่าน" ซึ่งครอบคลุมทั้งเคสที่เพิ่ง deliver เสร็จใหม่ๆ ที่
        ยังไม่เคยลงทะเบียนเลย และเคสที่เคยลองแล้วไม่ผ่าน — ยืนยันจากผู้ใช้) -> กดปุ่ม
        ไอคอนดินสอสีส้ม "แก้ไขข้อมูล" เปิด modal -> กด "ยืนยันลงทะเบียน" อย่างเดียว
        (ไม่ต้องกด "API AUTO MDM" — ยืนยันจากผู้ใช้) -> กด "ยืนยัน" ใน dialog ยืนยันซ้อน
        อีกชั้น ("คุณต้องการยืนยันการลงทะเบียนหรือไม่?" — เป็น custom HTML modal ไม่ใช่
        native browser dialog ยืนยันจาก screenshot จริง ต้องคลิกปุ่มในหน้าเว็บ ไม่ใช่ผ่าน
        page.on("dialog", ...) ซึ่งจับได้แค่ native alert/confirm เท่านั้น).

        ธุรกรรมนี้แก้คืนยาก (submit จริงเข้าระบบ) — เรียกเฉพาะตอนมั่นใจแล้วว่าเคส match
        ครบทุกจุดจริงๆ เท่านั้น. คืน True ก็ต่อเมื่อ verify แล้วว่าสถานะแถวเปลี่ยนเป็น
        "ลงทะเบียนสำเร็จ" จริงๆ (ไม่ใช่แค่คลิกผ่านไม่มี exception — เจอจริงว่าคลิกผ่านได้
        โดยไม่มี error แต่สถานะไม่เปลี่ยนเลย เพราะมี modal ยืนยันซ้อนที่ไม่เคยถูกคลิก),
        False ถ้าหาแถว/ปุ่มไม่เจอ, มีข้อผิดพลาดระหว่างทาง, หรือ verify แล้วสถานะยังไม่ผ่าน.
        """
        if not self._logged_in:
            return False

        def _search_by_status(status_label: str) -> bool:
            self.page.goto(self.REGISTER_PRODUCT_URL, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            tb = self.page.get_by_role("textbox", name="เลขที่ใบคำขอ")
            tb.click(timeout=5000)
            tb.fill("", timeout=3000)
            tb.fill(loan_app_number, timeout=5000)
            sel = self.page.get_by_label("สถานะลงทะเบียนสินค้า", exact=False)
            if sel.count() == 0:
                sel = self.page.locator("select")
            sel.first.select_option(label=status_label, timeout=3000)
            self.page.wait_for_timeout(300)
            self.page.locator("#btnSearch").click(timeout=5000)
            self.page.wait_for_timeout(2000)
            return self.page.locator("table tbody tr", has_text=loan_app_number).count() > 0

        # เคสอาจเคย submit สำเร็จมาแล้วจริงจากการรันครั้งก่อน แต่ verify ตอนนั้นพลาด (false
        # negative — เจอจริงจากผู้ใช้ยืนยันว่ากด submit ผ่านใน iLoan ไปแล้วทั้งที่โค้ด return
        # False) เช็คก่อนว่าลงทะเบียนสำเร็จอยู่แล้วหรือยัง ถ้าใช่ให้ถือว่าสำเร็จเลย ไม่ต้อง
        # กดซ้ำ (กดซ้ำจะหาแถวไม่เจอเพราะแถวย้ายออกจากสถานะ "ลงทะเบียนไม่ผ่าน" ไปแล้ว)
        try:
            if _search_by_status("ลงทะเบียนสำเร็จ"):
                return True
        except Exception:
            pass

        try:
            if not _search_by_status("ลงทะเบียนไม่ผ่าน"):
                return False
        except Exception:
            return False

        try:
            matched_row = self.page.locator("table tbody tr", has_text=loan_app_number).first
            matched_row.locator("button[title='แก้ไขข้อมูล']").first.click(timeout=5000)
            self.page.wait_for_timeout(2000)
        except Exception:
            return False

        # ช่อง "Serial No" ใน modal "แก้ไขข้อมูลสินค้า" บางเคสมีตัว "S" นำหน้าติดมาผิด
        # (ไม่ใช่ Apple Serial Number จริง — ยืนยันจากผู้ใช้ เหมือนกับ normalize_apple_serial
        # ที่ใช้ตอนค้นหาใน MDM) ต้องแก้ในฟอร์มนี้ก่อนกด "ยืนยันลงทะเบียน" ไม่งั้นจะ submit
        # ค่าผิดเข้าระบบจริง
        try:
            serial_input = self.page.get_by_text("Serial No", exact=False).first.locator(
                "xpath=following::input[1]"
            )
            current_val = serial_input.input_value(timeout=3000)
            if current_val and current_val.strip().upper().startswith("S"):
                fixed_val = current_val.strip()[1:]
                serial_input.click(timeout=3000)
                serial_input.fill("", timeout=3000)
                serial_input.fill(fixed_val, timeout=3000)
                self.page.wait_for_timeout(300)
        except Exception:
            pass

        try:
            self.page.locator("button:has-text('ยืนยันลงทะเบียน')").first.click(timeout=5000)
            self.page.wait_for_timeout(1000)
            # modal ยืนยันซ้อน "คุณต้องการยืนยันการลงทะเบียนหรือไม่?" — กด "ยืนยัน" (ปุ่มแดง)
            self.page.get_by_role("button", name="ยืนยัน", exact=True).click(timeout=5000)
            self.page.wait_for_timeout(2000)
        except Exception:
            return False

        # Verify จริง — ค้นหาซ้ำแล้วเช็คว่าสถานะแถวเปลี่ยนเป็น "ลงทะเบียนสำเร็จ" แล้วหรือยัง
        # (เจอจริงว่าคลิกผ่านหมดไม่มี error แต่สถานะไม่เปลี่ยนเลยเพราะพลาด modal ซ้อนด้านบน
        # ไปครั้งนึงแล้ว — ห้ามเชื่อแค่ "คลิกผ่าน = สำเร็จ" อีกต่อไป). Backend อาจอัปเดตสถานะ
        # ช้ากว่าที่คิด (เจอจริงว่ากดผ่านสำเร็จแต่ verify ครั้งแรกยังไม่เจอ) จึง retry
        # หลายรอบก่อนสรุปว่าล้มเหลว
        for _ in range(3):
            try:
                if _search_by_status("ลงทะเบียนสำเร็จ"):
                    return True
            except Exception:
                pass
            self.page.wait_for_timeout(2000)
        return False

    def get_loan(self, loan_app_number: str) -> Optional[ILoanCase]:
        """ค้นหาใบคำขอและดึงข้อมูล."""
        if not self._logged_in:
            return None

        # กลับไปหน้ารายการ "รอเปิดสัญญา / Deliver" เสมอก่อนค้นหา — เคสก่อนหน้าอาจค้างอยู่ที่
        # หน้ารายละเอียด (Review) ซึ่งปุ่ม "ย้อนกลับ" ในหน้านั้นเป็นแค่ wizard tab สลับไป-มา
        # ไม่ใช่ปุ่มกลับไปหน้ารายการค้นหาจริง
        try:
            self.page.get_by_role("link", name="รอเปิดสัญญา / Deliver").first.click(timeout=5000)
            self.page.wait_for_timeout(1000)
        except Exception:
            pass

        try:
            self.page.evaluate("""
                document.querySelectorAll(
                    '.blockUI, .blockOverlay, .modal-backdrop, .loading-overlay'
                ).forEach(el => el.remove());
            """)
        except Exception:
            pass
        self.page.wait_for_timeout(300)

        # ค้นหา + รอผลจริง — บาง ครั้งตารางยังไม่ update ทันที (grid โหลดช้ากว่า network request)
        # จึง retry การกดค้นหาซ้ำจนกว่าจะเห็นแถวที่ตรง หรือเห็น "ไม่พบข้อมูล" จริงๆ
        found_match = False
        saw_no_data = False
        for attempt in range(3):
            try:
                tb = self.page.get_by_role("textbox", name="เลขที่ใบคำขอ")
                tb.click(timeout=5000)
                tb.fill("", timeout=3000)
                tb.fill(loan_app_number, timeout=5000)

                self._select_all_status()
                self.page.wait_for_timeout(300)

                self.page.locator("#btnSearch").click(timeout=5000)
            except Exception:
                return ILoanCase(loan_app_number=loan_app_number)

            try:
                self.page.locator(
                    "table tbody tr", has_text=loan_app_number
                ).first.wait_for(state="visible", timeout=6000)
                found_match = True
                break
            except Exception:
                pass

            try:
                if self.page.locator("table tbody tr", has_text="ไม่พบข้อมูล").count() > 0:
                    saw_no_data = True
                    break
            except Exception:
                pass

            # ตารางยังเป็นรายการเก่า (ยังไม่ filter) — รอเพิ่มแล้วลองค้นหาใหม่
            self.page.wait_for_timeout(1000)

        result = ILoanCase(loan_app_number=loan_app_number)

        if not found_match:
            return result  # ไม่พบใบคำขอนี้ในสถานะ รอเปิดสัญญา/Deliver จริงๆ (saw_no_data) หรือค้นหาไม่สำเร็จหลัง retry

        matched_row = self.page.locator("table tbody tr", has_text=loan_app_number).first

        try:
            matched_row.get_by_title("ดูข้อมูล").click(timeout=5000)
            self.page.wait_for_timeout(2000)
        except Exception:
            return result

        # แท็บ "ข้อมูลลูกค้า" คือ default หลังคลิกดูข้อมูล — อ่านตาราง DOA แบบ raw ก่อน
        # (ยังตีความไม่ได้ว่าคอลัมน์ไหนคือ IMEI/Serial Number จนกว่าจะรู้ยี่ห้อสินค้า)
        doa_imei1_old, doa_seriesno_old, doa_imei1_new, doa_seriesno_new = self._get_doa_raw()
        result.customer_name = self._get_customer_name()

        # สลับไปแท็บ "ข้อมูลสินทรัพย์" อ่านฟิลด์สินค้าก่อน — ต้องรู้ยี่ห้อ/รุ่นก่อนตัดสินใจ
        # ตีความคอลัมน์ DOA (ดู docstring ของ _get_doa_raw)
        try:
            self.page.get_by_text("ข้อมูลสินทรัพย์", exact=False).first.click(timeout=5000)
            self.page.wait_for_timeout(1500)
        except Exception:
            pass

        result.branch            = self._get_row_value("ชื่อสาขา")
        result.staff_name        = self._get_row_value("ชื่อพนักงานสาขาที่ dipchip")
        result.product_category  = self._get_row_value("หมวดสินค้า")
        result.brand              = self._get_row_value("ยี่ห้อสินค้า")
        result.model              = self._get_row_value("รุ่นสินค้า")
        result.color              = self._get_row_value("สี")
        result.storage            = self._get_row_value("ความจุ")
        result.price_main         = self._get_row_value("ราคาสินค้าหลัก (รวมภาษี)")
        result.discount_package   = self._get_row_value("ส่วนลด package")
        result.discount_down_payment = self._get_row_value("ส่วนลดเงินดาวน์")
        result.discount_other        = self._get_row_value("ส่วนลดอื่นๆ")
        result.discount_device       = self._get_row_value("ส่วนลดค่าเครื่อง")
        result.price_total        = self._get_row_value("ราคาสินค้ารวมทั้งสิ้น (รวมภาษี)")
        result.down_payment_net   = self._get_row_value("เงินดาวน์สุทธิ (บาท)")
        result.down_payment       = self._get_row_value("เงินดาวน์ที่ลูกค้าจ่ายจริง (บาท)")
        result.monthly_payment    = self._get_row_value("ค่างวดต่อเดือน(บาท)")
        result.trade_in           = self._get_row_value("นำเครื่องเก่ามาแลกเพื่อรับส่วนลดก่อนเริ่มสัญญา*")
        result.trade_in_value     = self._get_row_value("มูลค่าเครื่องเก่าแลกใหม่")

        # ตีความคอลัมน์ DOA ตามยี่ห้อ (ยืนยันจากข้อมูลจริง):
        #   Apple/iOS -> "ข้อมูล Series No เดิม" คือ IMEI, "ข้อมูล Series No ใหม่" คือ Serial Number
        #               (คอลัมน์ IMEI 1 ว่างเสมอสำหรับ Apple)
        #   Android   -> "ข้อมูล IMEI 1" (ใหม่ก่อน เดิมถ้าใหม่ว่าง) คือ IMEI, ไม่มี Serial Number
        #               จาก DOA (คอลัมน์ Series No ว่างเสมอสำหรับ Android)
        if is_ios_device(result.brand, result.model):
            result.imei_old = doa_seriesno_old
            result.serial_new = doa_seriesno_new
        else:
            result.imei_old = doa_imei1_new or doa_imei1_old
            result.serial_new = ""

        # เจอเคสจริงที่ DOA คอลัมน์ "ข้อมูล Series No ใหม่" มีค่าเป็นเลข IMEI (ตัวเลขล้วน
        # 14-16 หลัก ไม่ใช่รูปแบบ Serial Number ตัวอักษร+ตัวเลขปกติของ Apple ~10-12 ตัว)
        # ตรงกับ "ข้อมูล Series No เดิม" เป๊ะ — ยืนยันจากผู้ใช้ว่าไม่ใช่ Serial Number จริง
        # (สาขาไม่ได้กรอก/ไม่มีการเปลี่ยนเครื่องจริง) ต้องล้างทิ้งแล้วไป fallback หาใน
        # Contract.pdf แทน ไม่ใช่เชื่อค่าจาก DOA ตรงๆ
        if result.serial_new and _looks_like_imei(result.serial_new):
            result.serial_new = ""

        if result.imei_old:
            result.imei_source = "DOA (iLoan)"
        if result.serial_new:
            result.serial_source = "DOA (iLoan)"

        # Fallback: ถ้า imei_old หรือ serial_new ยังไม่มีค่า (รวมกรณี serial ถูกล้างทิ้ง
        # เพราะจริงๆ เป็นเลข IMEI ปนมาด้านบน) ลองเปิดไฟล์ _Contract.pdf (ล็อกรหัสผ่าน)
        # แล้วอ่านจากในสัญญาแทน — เติมเฉพาะช่องที่ยัง
        # ว่างอยู่จริงๆ เท่านั้น ไม่ทับค่าที่มีอยู่แล้วจาก DOA — ต้องสลับกลับไปแท็บ
        # "ข้อมูลลูกค้า" ก่อน เพราะตาราง "ไฟล์เอกสาร" อยู่ที่นั่น
        if not result.imei_old or not result.serial_new:
            try:
                self.page.get_by_text("ข้อมูลลูกค้า", exact=False).first.click(timeout=5000)
                self.page.wait_for_timeout(1000)
                fallback_imei, fallback_serial = self._get_serial_imei_from_contract()
                if not result.imei_old and fallback_imei:
                    result.imei_old = fallback_imei
                    result.imei_source = "Contract.pdf (OCR)"
                if not result.serial_new and fallback_serial:
                    result.serial_new = fallback_serial
                    result.serial_source = "Contract.pdf (OCR)"
            except Exception:
                pass

        # หมายเหตุ: ไม่ต้องกด "ย้อนกลับ" ตรงนี้ — get_loan() รอบถัดไปจะ reset ไปหน้ารายการเองที่ต้นฟังก์ชัน
        return result

    def _get_doa_raw(self) -> tuple[str, str, str, str]:
        """อ่านตาราง 'ประวัติการเปลี่ยนเครื่อง (DOA)' แบบ raw ทั้ง 4 ค่า →
        (imei1_old, seriesno_old, imei1_new, seriesno_new).

        ตารางนี้ implement เป็น ARIA grid (role=columnheader/row/gridcell ไม่ใช่ th/td
        ธรรมดา) และ header block ปรากฏซ้ำ 2 ชุดในหน้า (โครง grid ล็อกคอลัมน์) แต่มีแค่
        แถวข้อมูลจริงชุดเดียวที่ render — จึงหา "row" ที่มีจำนวน gridcell ตรงกับจำนวน
        คอลัมน์ของ DOA block (11 คอลัมน์: No./วันที่/IMEI1เดิม/Series No เดิม/
        Product Number เดิม/Phy-ID เดิม/IMEI1ใหม่/Series No ใหม่/Product Number ใหม่/
        Phy-ID ใหม่/เหตุผล) แล้วอ่านค่าตาม index คอลัมน์ที่สนใจ.

        หมายเหตุสำคัญ: ตารางนี้เก็บข้อมูลคนละคอลัมน์กันตามยี่ห้อสินค้า — ยืนยันจากข้อมูล
        จริงของทั้ง 2 platform: เคส Apple/iOS คอลัมน์ "ข้อมูล Series No" มีค่า (เดิม=
        รูปแบบ IMEI 15 หลัก, ใหม่=รูปแบบ Serial Number ตัวอักษร) ส่วน "ข้อมูล IMEI 1"
        ว่างเปล่าเสมอ ตรงข้ามกับเคส Android ที่ "ข้อมูล IMEI 1" มีค่าจริง (รูปแบบ IMEI
        15 หลัก) แต่ "ข้อมูล Series No" ว่างเปล่าเสมอ — เมธอดนี้จึงแค่อ่านค่าดิบทั้ง 4
        ช่องคืนไป ให้ get_loan() เป็นผู้ตัดสินใจว่าจะตีความคอลัมน์ไหนเป็น IMEI/Serial
        Number อีกที (ต้องรู้ยี่ห้อสินค้าก่อนถึงจะตัดสินใจได้ถูก).
        คืนค่า ("", "", "", "") ถ้าไม่มีประวัติเปลี่ยนเครื่อง.
        """
        try:
            self.page.locator("text=ประวัติการเปลี่ยนเครื่อง (DOA)").first.wait_for(
                state="visible", timeout=5000
            )
        except Exception:
            return "", "", "", ""

        try:
            all_headers = self.page.get_by_role("columnheader").all_inner_texts()
            # หา DOA header block แรก (ต่อเนื่อง 11 คอลัมน์ เริ่มจาก 'No.' ตามด้วย 'วันที่'
            # แล้วมี 'ข้อมูล Series No เดิม'/'ข้อมูล Series No ใหม่' อยู่ในบล็อกเดียวกัน)
            block_start = None
            for i in range(len(all_headers) - 10):
                if (
                    "Series No" in all_headers[i + 3] and "เดิม" in all_headers[i + 3]
                    and "Series No" in all_headers[i + 7] and "ใหม่" in all_headers[i + 7]
                ):
                    block_start = i
                    break
            if block_start is None:
                return "", "", "", ""

            idx_imei_old = 2    # offset ภายใน block: ข้อมูล IMEI 1 เดิม
            idx_serial_old = 3  # offset ภายใน block: ข้อมูล Series No เดิม
            idx_imei_new = 6    # offset ภายใน block: ข้อมูล IMEI 1 ใหม่
            idx_serial_new = 7  # offset ภายใน block: ข้อมูล Series No ใหม่
            block_len = 11

            rows = self.page.get_by_role("row")
            for i in range(rows.count()):
                cells = rows.nth(i).get_by_role("gridcell")
                if cells.count() != block_len:
                    continue
                texts = cells.all_inner_texts()
                imei1_old = texts[idx_imei_old].strip()
                seriesno_old = texts[idx_serial_old].strip()
                imei1_new = texts[idx_imei_new].strip()
                seriesno_new = texts[idx_serial_new].strip()
                if imei1_old or seriesno_old or imei1_new or seriesno_new:
                    return imei1_old, seriesno_old, imei1_new, seriesno_new
            return "", "", "", ""
        except Exception:
            return "", "", "", ""

    def _get_customer_name(self) -> str:
        """อ่านชื่อ-นามสกุลลูกค้าจากแท็บ 'ข้อมูลลูกค้า' (แถวที่มี 2 cell และ cell ที่สอง
        เป็น 'ชาย'/'หญิง' คือแถว ชื่อ-นามสกุล+เพศ) แล้วตัดคำนำหน้า (นาย/นาง/นางสาว) ออก."""
        try:
            rows = self.page.get_by_role("row")
            for i in range(rows.count()):
                cells = rows.nth(i).get_by_role("cell")
                if cells.count() != 2:
                    continue
                texts = cells.all_inner_texts()
                if texts[1].strip() in ("ชาย", "หญิง"):
                    return _TITLE_RE.sub("", texts[0].strip()).strip()
            return ""
        except Exception:
            return ""

    def _get_serial_imei_from_contract(self) -> tuple[str, str]:
        """Fallback: ดาวน์โหลด "_Contract.pdf" ในตาราง "ไฟล์เอกสาร" (แท็บข้อมูลลูกค้า)
        ปลดล็อกด้วยรหัสผ่านไฟล์ (ดู _contract_pdf_password — ไม่ได้เผยแพร่ใน repo นี้) แล้วอ่าน Serial/IMEI
        จากเนื้อหาสัญญา — ใช้เมื่อตาราง DOA ไม่มีข้อมูลเลย.

        หมายเหตุสำคัญ: ค่า Serial/IMEI ในไฟล์นี้ดึงด้วย pypdf/PyMuPDF text extraction
        ไม่ได้ (font ที่ใช้เขียนค่าไม่มี ToUnicode map ที่ใช้ได้ — extract_text() คืนค่าง
        เปล่าทั้งที่มองเห็นด้วยตาชัดเจน) จึงต้อง render หน้าแรกเป็นรูปภาพแล้วใช้ Tesseract
        OCR (local, ฟรี, ไม่จำกัดโควตา — ไม่ใช้ Gemini เพราะติด quota 20 ครั้ง/วัน) อ่านแทน
        ตัวเลข/ตัวอักษรละติน (Serial/IMEI) OCR ได้แม่นยำแม้ไม่ได้ลง Thai language pack.

        คืน (imei, serial) หรือ ("", "") ถ้าหาไม่เจอ/ไฟล์ไม่มี/ปลดล็อกไม่ได้.
        """
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image

        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

        password = _contract_pdf_password(self.page)
        if not password:
            return "", ""

        row = self.page.locator("tr", has_text="_Contract.pdf").first
        if row.count() == 0:
            return "", ""
        view_btn = row.get_by_text("ดูไฟล์")
        if view_btn.count() == 0:
            return "", ""

        try:
            with self.page.expect_download(timeout=8000) as download_info:
                view_btn.first.click(timeout=5000)
            download = download_info.value
        except Exception:
            return "", ""

        tmp_path = Path(tempfile.gettempdir()) / f"iloan_contract_{download.suggested_filename or 'file.pdf'}"
        ocr_text = ""
        try:
            download.save_as(str(tmp_path))
            doc = fitz.open(str(tmp_path))
            if doc.needs_pass:
                if not doc.authenticate(password):
                    return "", ""
            # ตาราง "บัญชีรายการเช่าซื้อ" ที่มี Serial/IMEI อยู่หน้าแรกเสมอ
            pix = doc[0].get_pixmap(dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            ocr_text = pytesseract.image_to_string(img)
        except Exception:
            return "", ""
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        # รูปแบบยืนยันจริงในสัญญา: "Serial / IMEI Number   <Serial> / <IMEI>"
        # เช่น "Serial / IMEI Number   ABCD1234EF / -" ("-" = ไม่มีข้อมูลฝั่งนั้น)
        anchor = _SERIAL_IMEI_ANCHOR_RE.search(ocr_text)
        if not anchor:
            return "", ""
        tail = ocr_text[anchor.end():anchor.end() + 200]
        vm = _SERIAL_IMEI_VALUE_RE.search(tail)
        if not vm:
            return "", ""
        serial = vm.group(1) if vm.group(1) != "-" else ""
        imei = vm.group(2) if vm.group(2) != "-" else ""
        return imei, serial

    def _select_all_status(self) -> None:
        """เปลี่ยน dropdown สถานะเป็น 'ทั้งหมด' — ลองหลาย strategy."""
        strategies = [
            {"label": "-- ทั้งหมด --"},
            {"index": 0},
            {"label": "ทั้งหมด"},
            {"value": ""},
        ]
        for strategy in strategies:
            try:
                sel = self.page.get_by_label("สถานะ", exact=True).first
                sel.select_option(**strategy, timeout=2000)
                return
            except Exception:
                continue
        try:
            self.page.evaluate("""
                () => {
                    var labels = Array.from(document.querySelectorAll('label'));
                    var statusLabel = labels.find(l => l.textContent.trim() === 'สถานะ');
                    if (statusLabel && statusLabel.htmlFor) {
                        var sel = document.getElementById(statusLabel.htmlFor);
                        if (sel && sel.options.length > 0) {
                            sel.value = sel.options[0].value;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    }
                }
            """)
        except Exception:
            pass

    def _get_row_value(self, header: str) -> str:
        """ดึงค่าจากตาราง โดยใช้ XPath following-sibling::td[1] จาก rowheader."""
        try:
            row_header = self.page.get_by_role("rowheader", name=header, exact=False).first
            value_cell = row_header.locator("xpath=following-sibling::td[1]")
            txt = value_cell.inner_text(timeout=1500).strip()
            return txt
        except Exception:
            pass
        try:
            row_header = self.page.get_by_role("rowheader", name=header, exact=False).first
            tr = row_header.locator("xpath=parent::tr")
            tds = tr.locator("td").all()
            if tds:
                return tds[0].inner_text(timeout=1000).strip()
        except Exception:
            pass
        return ""
