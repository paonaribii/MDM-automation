"""crm.py — ดึงข้อมูลเคสจาก CRM (Ufund) ผ่าน Playwright + ตอบกลับ/ปิดเคส (เฉพาะเคส match=Yes).

แทนที่ helpdesk.py ของระบบเดิม — เคสไม่ได้มาจาก Helpdesk อีกต่อไป แต่มาจาก CRM
(ซึ่งรับเคสต่อมาจาก 7Chat อีกที) ฟิลด์ที่ใช้ตรวจสอบ (เลขที่ใบคำขอ, ชื่อลูกค้า,
Branch, Serial/IMEI) เป็นฟิลด์โครงสร้างอยู่แล้วในหน้าเคส (ไม่ต้อง parse จาก
free-text message เหมือนสมัย Helpdesk).

get_case() เป็น read-only ล้วนๆ. start_processing()/submit_reply_and_close()
เป็นธุรกรรมจริงที่กระทบเคสจริง (กด "บันทึก" 2 ครั้ง, ปิดเคสจริง) — ยืนยันจากผู้ใช้
ให้ทำเฉพาะเคสที่ตรวจแล้ว match=Yes เท่านั้น ส่วน selector ของฟอร์มยังไม่เคยทดสอบ
รันจริงแบบ end-to-end (ต่างจาก get_case ที่ทดสอบแล้ว) — แนะนำให้ลองรันแบบ --headed
ทีละเคสก่อนเชื่อใจปล่อยเป็น loop ยาวๆ.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.sync_api import Page

from qa_automation.config import env

logger = logging.getLogger(__name__)

BASE_URL = env("CRM_BASE_URL")
LOGIN_URL = f"{BASE_URL}/login"

TARGET_CATEGORY = "Submit MDM"

# เลขที่ใบคำขอรูปแบบ ITOS เท่านั้น (XXXXXX-XXXXXX) ถึงจะเข้า flow เต็ม — เหมือนกฎเดิม
# สมัย Helpdesk (ยืนยันจากผู้ใช้ว่าใช้กฎเดิมไปก่อน)
_LOAN_APP_RE = re.compile(r"(\d{6}-\d{6})")

_STATUS_VALUES = ("รอดำเนินการ", "กำลังดำเนินการ", "ดำเนินการเสร็จสิ้น", "ปิดเคส")

# สคริปต์ 1.1 "เอกสารครบถ้วน" จากชีท Script CMC V.2 (Apr 2026) หัวข้อ MDM Submit — ใช้ตอบ
# กลับเฉพาะเคสที่ตรวจแล้ว match=Yes เท่านั้น ลายเซ็นใช้ "BOT" ตรงตัว (ยืนยันจากผู้ใช้ —
# ไม่ใช้ชื่อคนจริงเพราะเป็นการตอบกลับอัตโนมัติ)
REPLY_SCRIPT_TEMPLATE = (
    "เรียนแจ้งทางสาขาค่ะ\n\n"
    "จากการตรวจสอบของเจ้าหน้าที่ ข้อมูลถูกต้องครบถ้วนแล้ว ให้ดำเนินการต่อดังนี้\n\n"
    "1. ให้สาขาทำตามขั้นตอน Asset Deliver บนระบบ ITOS / K2 ให้เรียบร้อย\n"
    "2. หากเรียบร้อยแล้ว ให้ตรวจสอบสถานะสัญญาในระบบ ITOS / K2 ต้องเป็น \"ลูกหนี้ปกติ\" "
    "หรือ \"เสร็จสมบูรณ์\" เท่านั้น\n"
    "3. ถ้ายังไม่ขึ้นสถานะดังกล่าว ห้ามส่งมอบเครื่องให้กับลูกค้าทุกกรณี\n\n"
    "หากมีข้อสงสัยเพิ่มเติมแจ้งมาได้เลยนะคะ ขอบคุณค่ะ\n"
    "#BOT / #{loan_app_number}"
)

# "สาขาผู้สร้างเคส" ใน CRM มาในรูปแบบ "ID 282_Studio 7_Big C_Kamphaengphet" (เว้นวรรค
# ระหว่าง ID กับเลข, คั่นชื่อสาขาด้วย "_", ไม่มี ":") ต่างจาก branch string ของ iLoan/ITEC
# ที่เป็น "ID282 : ชื่อสาขา" (rules._branch_id() แกะรหัสสาขาจากก่อน " : " เท่านั้น) — ต้อง
# normalize ให้เป็นรูปแบบเดียวกันก่อน ไม่งั้น _branch_id() จะแกะรหัสไม่ออกเลย (ได้ค่าว่าง
# กลายเป็น "ไม่มีข้อมูลจาก CRM" ทั้งที่จริงๆ มีข้อมูลสาขาอยู่ — เจอบั๊กนี้จากการทดสอบเคสจริง
# ที่ทดสอบ)
_BRANCH_CODE_RE = re.compile(r"^\s*([A-Za-z]*)\s*(\d+)\s*[_\s]*(.*)$")


def _normalize_branch(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or ":" in raw:  # มีรูปแบบ "รหัส : ชื่อ" อยู่แล้ว — ไม่ต้องแตะ
        return raw
    m = _BRANCH_CODE_RE.match(raw)
    if not m:
        return raw
    prefix, num, rest = m.group(1), m.group(2), m.group(3).strip(" _")
    code = f"{prefix}{num}"
    return f"{code} : {rest}" if rest else code


@dataclass
class CRMCase:
    """ข้อมูลเคสที่ดึงมาจากหน้า CRM case detail — attribute ชื่อเดียวกับที่
    rules.evaluate_case() ต้องการ (customer_name/loan_app_number/branch/
    imei_serial/mdm_case_id) เพื่อใช้แทน HelpdeskCase เดิมได้ตรงๆ."""

    case_id: str = ""
    case_url: str = ""
    status: str = ""                 # รอดำเนินการ / กำลังดำเนินการ / ดำเนินการเสร็จสิ้น / ปิดเคส
    created_at: str = ""
    creator_name: str = ""           # ชื่อ-นามสกุล (ผู้สร้างเคส) — พนักงานสาขาที่แจ้ง ไม่ใช่ลูกค้า
    branch: str = ""                 # สาขาผู้สร้างเคส
    category: str = ""
    additional_detail: str = ""
    responsible_name: str = ""
    responsible_phone: str = ""
    responsible_branch: str = ""
    loan_app_number: str = ""        # เลขที่ใบคำขอ (ข้อมูลลูกค้า)
    customer_name: str = ""          # ชื่อ-นามสกุล (ข้อมูลลูกค้า) — ลูกค้าตัวจริง
    imei_serial: str = ""            # Serial Number/IMEI
    invoice_number: str = ""
    mdm_case_id: str = ""            # ไม่มีฟิลด์นี้ตรงๆ ใน CRM — เก็บว่างไว้ (CMC Summary หาแทน)


def _value_after_label(lines: list[str], label: str) -> str:
    """หาบรรทัดถัดจากบรรทัดที่ตรงกับ label เป๊ะ — โครงสร้างหน้า CRM เป็น label/value
    สลับกันบรรทัดต่อบรรทัดเสมอ (ยืนยันจากหน้าเคสจริงที่สำรวจมา)."""
    for i, line in enumerate(lines):
        if line.strip() == label:
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if nxt:
                    return nxt
            return ""
    return ""


class CRMClient:
    """Login + อ่านข้อมูลเคสจาก CRM (Ufund) — read-only เท่านั้นในตอนนี้."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._logged_in = False

    def login(self, username: str, password: str) -> bool:
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1000)

        if "/login" not in self.page.url:
            self._logged_in = True
            return True

        self.page.locator("input[placeholder='Input Username...']").fill(username)
        self.page.locator("input[placeholder='Input Password...']").fill(password)
        self.page.locator("button:has-text('Login')").click()

        try:
            self.page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            self._logged_in = True
            return True
        except Exception:
            return False

    def get_case(self, case_id: str) -> CRMCase:
        """เปิดหน้าเคส /case/{case_id} แล้วดึงฟิลด์ที่ต้องใช้ทั้งหมด — read-only
        ล้วนๆ (ไม่กดปุ่มใดๆ ในหน้า ไม่รับเคส ไม่ตอบกลับ ไม่ปิดเคส)."""
        case_url = f"{BASE_URL}/case/{case_id}"
        self.page.goto(case_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)
        self.page.locator("text=หมายเลขเคส").first.wait_for(timeout=10000)

        body_text = self.page.locator("body").inner_text()
        lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]

        case = CRMCase(case_id=case_id, case_url=case_url)

        header = next((ln for ln in lines if ln.startswith("หมายเลขเคส")), "")
        m = re.search(r"หมายเลขเคส\s*:\s*(\S+)", header)
        if m:
            case.case_id = m.group(1)

        if header:
            header_idx = lines.index(header)
            if header_idx + 1 < len(lines) and lines[header_idx + 1] in _STATUS_VALUES:
                case.status = lines[header_idx + 1]

        case.created_at         = _value_after_label(lines, "วันที่สร้างเคส:")
        case.creator_name       = _value_after_label(lines, "ชื่อ-นามสกุล (ผู้สร้างเคส):")
        case.branch             = _normalize_branch(_value_after_label(lines, "สาขาผู้สร้างเคส:"))
        case.category           = _value_after_label(lines, "หมวดหมู่:")
        case.responsible_name   = _value_after_label(lines, "ผู้รับผิดชอบเคส:")
        case.responsible_phone  = _value_after_label(lines, "เบอร์ผู้รับผิดชอบเคส:")
        case.responsible_branch = _value_after_label(lines, "สาขาผู้รับผิดชอบเคส:")
        case.loan_app_number    = _value_after_label(lines, "เลขที่ใบคำขอ:")
        case.customer_name      = _value_after_label(lines, "ชื่อ-นามสกุล:")
        case.imei_serial        = _value_after_label(lines, "Serial Number/IMEI:")
        case.invoice_number     = _value_after_label(lines, "Invoice Number:")

        # เก็บเฉพาะรูปแบบ ITOS (XXXXXX-XXXXXX) — กันเผื่อฟิลด์มีค่าอื่นปนมา (เช่น "-"
        # ตอนไม่มีข้อมูล) ไม่งั้นจะเข้าใจผิดว่ามีเลขที่ใบคำขอทั้งที่จริงไม่มี
        loan_m = _LOAN_APP_RE.search(case.loan_app_number)
        case.loan_app_number = loan_m.group(1) if loan_m else ""

        return case

    def _find_field_by_label(self, label: str, timeout: int = 15000):
        """หา input/textarea ด้วย placeholder ที่ขึ้นต้นด้วย label — ใช้ CSS attribute
        substring selector (`*=`) เป็นหลัก (ทนกว่า get_by_placeholder เพราะไม่สนใจ role,
        กันปัญหา "..." สามจุด กับ "…" อักขระเดียวไม่ตรงกันด้วย substring match เหมือนกัน)
        ต่อท้ายด้วย `:visible` เสมอ — หน้านี้มี element ซ้ำ 2 ชุดซ่อนไว้ด้วย responsive layout
        (เจอบั๊กเดียวกันมาแล้วใน 7Chat: `:visible` filter จำเป็นเพื่อไม่ให้ไปเจอ/รอ element
        ที่ซ่อนอยู่แทนตัวที่ผู้ใช้เห็นจริงบนจอ). รอนานถึง `timeout` เพราะบางฟิลด์ (เช่น
        "รายละเอียดหลังดำเนินการ") โผล่มาใน DOM ช้ากว่าที่คิด หลังกด "บันทึก" ครั้งแรกไปแล้ว
        (เจอจริงจากการทดสอบ — รอแค่ 1.5-3 วินาทีไม่พอ) — ถ้ายังหาไม่เจอ fallback ไปหา label
        เป็น sibling ของ field จริง."""
        selector = f"textarea[placeholder*='{label}']:visible, input[placeholder*='{label}']:visible"
        field = self.page.locator(selector).first
        try:
            field.wait_for(state="visible", timeout=timeout)
            return field
        except Exception:
            pass
        el = self.page.get_by_text(label, exact=False).first
        return el.locator("xpath=following-sibling::*[self::textarea or self::input][1]")

    def submit_reply_and_close(self, loan_app_number: str) -> bool:
        """ตอบกลับ+ปิดเคส — เรียกทันทีหลัง get_case() ตอนยังอยู่ในหน้าเคสนั้น เฉพาะเคส
        ที่ตรวจแล้ว match=Yes เท่านั้น (ยืนยันจากผู้ใช้).

        ขั้นตอน (ยืนยันจากผู้ใช้):
          1. กรอก "รายละเอียดก่อนดำเนินการ" = "กำลังดำเนินการ", "ชื่อ-นามสกุลผู้ดำเนินการ" = ".",
             "เบอร์ผู้ดำเนินการ" = "." (ต้องกรอก 2 ช่องหลังด้วย ไม่งั้นระบบฟ้อง "กรอกข้อมูล
             ไม่ครบถ้วน" แล้วไม่ไปต่อ — เจอจริงจากการทดสอบ ไม่ต้องเป็นชื่อ/เบอร์จริง ใส่ "."
             พอ)
          2. กด "บันทึก" (ครั้งที่ 1) → มี popup "แจ้งเตือน: ยืนยันการบันทึกข้อมูล?" โผล่มา
             ทุกครั้งที่กด "บันทึก" — ต้องกด "OK" ยืนยันอีกทีถึงจะเซฟจริง (เจอจริงจากการทดสอบ
             — ไม่กด OK เคสจะไม่ขยับสถานะเลย) จากนั้นฟอร์ม "รายละเอียดหลังดำเนินการ" จะโผล่มา
          3. กรอกสคริปต์ 1.1 (REPLY_SCRIPT_TEMPLATE) ลงช่องนั้น
          4. กด "บันทึก" (ครั้งที่ 2) → กด "OK" ยืนยัน popup อีกครั้ง — เคสจะถูกปิด. หาปุ่ม
             "บันทึก" นี้โดยไต่ขึ้นไปหา container (ancestor) ที่มีทั้ง textarea นี้และปุ่ม
             "บันทึก" อยู่ด้วยกันก่อนเสมอ (กันกดผิดปุ่มถ้ามีปุ่ม "บันทึก" มากกว่า 1 ปุ่มพร้อมกัน
             บนหน้า ณ ตอนนั้น)

        คืน True ถ้าทำครบทุกขั้นตอนไม่มี error — คืน False ถ้าทำไม่สำเร็จ (caller ควรถือว่า
        ยังไม่เสร็จ รอรันรอบถัดไปมาลองใหม่)."""
        try:
            self._find_field_by_label("รายละเอียดก่อนดำเนินการ").fill("กำลังดำเนินการ", timeout=5000)
            # ต้องกรอกชื่อ-นามสกุล/เบอร์ผู้ดำเนินการด้วย ไม่งั้นระบบฟ้อง "กรอกข้อมูลไม่ครบถ้วน"
            # แล้วไม่ยอมไปต่อ (ฟอร์ม "รายละเอียดหลังดำเนินการ" ไม่โผล่มาเลย) — ยืนยันจากผู้ใช้
            # ว่าใส่ "." (จุดเฉยๆ) พอ ไม่ต้องเป็นชื่อ/เบอร์จริง
            self._find_field_by_label("ชื่อ-นามสกุลผู้ดำเนินการ").fill(".", timeout=5000)
            self._find_field_by_label("เบอร์ผู้ดำเนินการ").fill(".", timeout=5000)
            self.page.get_by_role("button", name="บันทึก", exact=True).first.click(timeout=10000)
            self._confirm_save_popup()
            self.page.wait_for_timeout(1500)

            # ฟิลด์นี้ใช้เวลาโผล่มาไม่แน่นอนมาก (เจอจริงว่าบางเคส 3-5 วิ บางเคสเกิน 15 วิ
            # ยังไม่โผล่ — ขึ้นกับขนาด "ประวัติการโต้ตอบ" ที่ต้องโหลดก่อนด้วย) รอให้นานขึ้น
            # อีกเป็น 25 วิ กันเคสที่โหลดช้าโดนตัดจบเร็วเกินไป
            after_box = self._find_field_by_label("รายละเอียดหลังดำเนินการ", timeout=25000)
            message = REPLY_SCRIPT_TEMPLATE.format(loan_app_number=loan_app_number)
            after_box.fill(message, timeout=15000)

            # กดปุ่ม "บันทึก" ที่อยู่ใน container เดียวกับ textarea นี้เท่านั้น — ไม่ใช่ตัวแรก
            # ที่เจอบนหน้าเฉยๆ (อาจมีปุ่ม "บันทึก" ของฟอร์มอื่นค้างอยู่พร้อมกัน)
            try:
                container = after_box.locator(
                    "xpath=ancestor::*[.//button[normalize-space(text())='บันทึก']][1]"
                )
                container.get_by_role("button", name="บันทึก", exact=True).first.click(timeout=10000)
            except Exception:
                self.page.get_by_role("button", name="บันทึก", exact=True).first.click(timeout=10000)
            self._confirm_save_popup()
            self.page.wait_for_timeout(1500)
            return True
        except Exception as exc:
            logger.exception(
                "submit_reply_and_close ล้มเหลว (loan_app_number=%s): %s", loan_app_number, exc
            )
            return False

    def _confirm_save_popup(self) -> None:
        """กดปุ่ม "OK" บน popup "แจ้งเตือน: ยืนยันการบันทึกข้อมูล?" ที่โผล่มาทุกครั้งหลังกด
        "บันทึก" — ไม่กด popup นี้เคสจะไม่ถูกบันทึกจริงเลย (เจอจริงจากการทดสอบ). รอไม่เกิน 5
        วินาที เผื่อบาง flow ไม่มี popup ก็ไม่ error"""
        try:
            self.page.get_by_role("button", name="OK", exact=True).first.click(timeout=5000)
        except Exception:
            logger.warning("ไม่เจอ popup ยืนยัน 'OK' หลังกด บันทึก (อาจไม่มี popup ในเคสนี้)")
