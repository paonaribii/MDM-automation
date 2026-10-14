"""sevenchat.py — รอจนกว่าจะมีเคสใหม่ในแท็บ "เคสใหม่" ของ 7Chat แล้วเปิดเคสนั้นเพื่อ
อ่านลิงก์ไปหน้า CRM case (ยืนยันจากผู้ใช้: หยิบจากแท็บ "เคสใหม่" เท่านั้น ห้ามแตะเคสในแท็บ
"เคสวันนี้" หรือเคสที่ Solve/Close แล้วเด็ดขาด).

ขั้นตอนนี้แค่ "เปิดดู" เคส (คลิกเข้าไปดู + อ่านลิงก์ไปหน้า CRM จาก DOM ตรงๆ ไม่ได้คลิกลิงก์
หรือ navigate ไปเอง) — ไม่ได้กด "บันทึก"/รับเคสใดๆ ใน CRM (ส่วนนั้นทำแยกต่างหากทีหลัง
ตามที่ยืนยันจากผู้ใช้ว่ายังไม่ให้แตะ)

Selector ทั้งหมดยืนยันจากการสำรวจ DOM จริงแล้ว (ผ่าน read_page + JS inspection):
  - แท็บ/ปุ่มนับจำนวนเคส เช่น "เคสใหม่ (N)" เป็น <button> ข้อความตรงๆ
  - แต่ละเคสในลิสต์เป็น <a href="/chat/{chatId}/{caseId}">
  - ไอคอน ⓘ เป็น <svg> อยู่ใน <h3> ของชื่อลูกค้า (มี class "xl:hidden" — ซ่อนที่หน้าจอ
    กว้าง >=1280px จึงต้อง set viewport ให้แคบกว่านั้นเสมอ กันไอคอนไม่โผล่)
  - แท็บ "ประวัติ" ใน panel เป็น <button> เหมือนกัน ไม่ใช่ลิงก์ — กดแล้วแสดงลิสต์ประวัติ
  - แต่ละรายการในประวัติมีลิงก์ CRM เป็น <a href="{CRM_BASE_URL}/case/..."> ตรงๆ
    อ่าน href ได้เลยไม่ต้องคลิก (ปลอดภัยกว่า กัน accidental navigation)
"""
from __future__ import annotations

import logging
import re

from playwright.sync_api import Page

from qa_automation.config import env

logger = logging.getLogger(__name__)

BASE_URL = env("SEVENCHAT_BASE_URL")
_CRM_BASE_URL = env("CRM_BASE_URL")
LOGIN_URL = f"{BASE_URL}/login"

_NEW_CASE_TAB_RE = re.compile(r"เคสใหม่\s*\(\s*(\d+)\s*\)")
_CRM_CASE_HREF_RE = re.compile(re.escape(_CRM_BASE_URL) + r"/case/(\d+)")

# ต้อง set viewport ให้แคบกว่า Tailwind breakpoint "xl" (1280px) เสมอ ไม่งั้นไอคอน ⓘ
# (class "xl:hidden") จะถูกซ่อนแล้วกดไม่ได้เลย
_VIEWPORT = {"width": 1024, "height": 800}


class SevenChatClient:
    """Login + รอ/เปิดเคสใหม่ใน 7Chat แล้วอ่านลิงก์ไปหน้า CRM case."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.page.set_viewport_size(_VIEWPORT)
        # จำ href ของเคสที่เคยหยิบไปประมวลผลแล้วในรันนี้ — กันหยิบเคสเดิมซ้ำ เพราะสคริปต์
        # ไม่เคยกด "รับเคส" ใน CRM เลย (ตามที่ยืนยันจากผู้ใช้) เคสจึงยังค้างเป็น "เคสใหม่" ต่อ
        # ใน 7Chat แม้จะประมวลผลไปแล้ว — ไม่กันไว้จะโดนหยิบซ้ำเรื่อยๆ ทุกรอบ poll (เจอบั๊กจริง
        # ตอนรัน --limit หลายเคส: เคสเดียวกันถูกบันทึกซ้ำ 3 รอบในชีท)
        self._seen_hrefs: set[str] = set()

    def login(self, username: str, password: str) -> bool:
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1000)
        if "/login" not in self.page.url:
            return True
        self.page.locator("input[placeholder='Input Username...']").fill(username)
        self.page.locator("input[placeholder='Input Password...']").fill(password)
        self.page.locator("button:has-text('Login')").click()
        try:
            self.page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
            return True
        except Exception:
            return False

    def _new_case_count(self) -> int:
        """อ่านตัวเลขในปุ่มแท็บ "เคสใหม่ (N)" — คืน 0 ถ้าอ่านไม่ได้ (กันรอค้างถ้า DOM เปลี่ยนไป)."""
        try:
            text = self.page.get_by_role("button", name=_NEW_CASE_TAB_RE).first.inner_text(timeout=3000)
        except Exception:
            return 0
        m = _NEW_CASE_TAB_RE.search(text)
        return int(m.group(1)) if m else 0

    def _try_open_first_new_case(self) -> str | None:
        """พยายามเปิดเคสแรกในแท็บ "เคสใหม่" แล้วอ่านลิงก์ CRM — คืน None (ไม่ raise) ถ้าล้ม
        เหลวเพราะแย่งกับ agent คนอื่นไม่ทัน (เคสถูกคนอื่นหยิบ/ปิดไปก่อนที่เราจะเปิดจนสำเร็จ —
        เจอจริงจากการทดสอบ: เคสหลุดจากแท็บ "เคสใหม่" ไปเป็น Resolve เพราะ agent อื่นปิดไปก่อน)
        ให้ caller (wait_for_new_case) วนกลับไป poll หาเคสใหม่ตัวถัดไปแทน."""
        self.page.get_by_role("button", name=_NEW_CASE_TAB_RE).first.click()
        self.page.wait_for_timeout(500)

        if self._new_case_count() == 0:
            logger.info("  [7Chat] เคสใหม่หายไปแล้ว (คนอื่นหยิบ/ปิดไปก่อน) — วนกลับไปรอเคสถัดไป")
            return None

        # รายการเคสแต่ละอันเป็น <a href="/chat/{chatId}/{caseId}"> อยู่ใน <main> — ต้อง scope
        # ด้วย "main " นำหน้าเสมอ ไม่งั้นจะไปจับ <a href="/chat/{chatId}"> ของโลโก้ในแถบเมนูบนสุด
        # (นอก <main>) มาก่อน เพราะมันมาก่อนในหน้าเว็บและ href ขึ้นต้นเหมือนกัน (เจอบั๊กจริง —
        # เป็นสาเหตุที่คลิกแล้วไม่เปิดเคส/ค้างมาตลอด ไม่ใช่ปัญหาเรื่อง element ถูกบังอย่างที่คิด)
        try:
            all_links = self.page.locator("main a[href^='/chat/']").all()
            hrefs = [lnk.get_attribute("href") or "" for lnk in all_links]
            # เลือกอันแรกที่ "ยังไม่เคยหยิบไปประมวลผลในรันนี้" เท่านั้น — ถ้าเคสใหม่ที่เห็นอยู่
            # ตอนนี้เป็นตัวเดิมที่เพิ่งทำไปแล้ว (ยังไม่มีใครกด "รับเคส" ใน CRM เลยยังค้างอยู่)
            # ให้ถือว่ายังไม่มีเคสใหม่จริงๆ แทนที่จะหยิบซ้ำ
            href = next((h for h in hrefs if h and h not in self._seen_hrefs), "")
            if not href:
                logger.info(
                    "  [7Chat] เคสที่เห็นในแท็บ 'เคสใหม่' ตอนนี้ประมวลผลไปแล้วทั้งหมดในรันนี้ "
                    "(ยังไม่มีใครกด 'รับเคส' ใน CRM เคสเลยยังค้างอยู่) — วนกลับไปรอเคสถัดไป"
                )
                return None
            self._seen_hrefs.add(href)
            self.page.goto(f"{BASE_URL}{href}", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)

            # เปิด panel รายละเอียดลูกค้า (ไอคอน ⓘ ในหัว h3 ชื่อลูกค้า) แล้วสลับไปแท็บ "ประวัติ"
            self.page.locator("h3 svg").first.click(timeout=15000)
            self.page.wait_for_timeout(500)
            self.page.get_by_role("button", name="ประวัติ", exact=True).click(timeout=10000)
            self.page.wait_for_timeout(500)

            # อ่าน href ของลิงก์ CRM รายการแรก (ล่าสุด) ตรงๆ จาก DOM — ไม่คลิก ไม่ navigate
            crm_link = self.page.locator(f"a[href*='{_CRM_BASE_URL}/case/']").first
            crm_link.wait_for(timeout=10000)
            href = crm_link.get_attribute("href") or ""
        except Exception as exc:
            logger.warning(
                "  [7Chat] เปิดเคสไม่สำเร็จ (%s) — อาจโดน agent คนอื่นหยิบไปพร้อมกัน วนกลับไปรอเคสถัดไป",
                exc,
            )
            return None

        m = _CRM_CASE_HREF_RE.search(href)
        if not m:
            logger.warning(
                "  [7Chat] เปิดเคสแล้วแต่ไม่เจอลิงก์ CRM ในประวัติเคส (href=%r) — วนกลับไปรอเคสถัดไป",
                href,
            )
            return None
        crm_case_id = m.group(1)
        logger.info("  [7Chat] -> CRM case id = %s", crm_case_id)
        return crm_case_id

    def wait_for_new_case(
        self, poll_interval_sec: float = 5.0, timeout_sec: float | None = None
    ) -> str:
        """รอจนกว่าแท็บ "เคสใหม่" จะมีเคสที่ **ยังไม่เคยหยิบไปในรันนี้** อย่างน้อย 1 เคส แล้ว
        เปิดเคสนั้น (ยังคง scope อยู่แค่แท็บ "เคสใหม่" เท่านั้น — ห้ามแตะเคสแท็บอื่นเด็ดขาด
        "เคสวันนี้"/"ค้างเก่า"/เคส Solve/Close แล้ว) → อ่านลิงก์ไปหน้า CRM case จาก "ประวัติ"
        panel → คืนเลขเคส CRM.

        ยังไม่กดปุ่ม "บันทึก"/รับเคสใดๆ ใน CRM และไม่คลิกลิงก์ CRM เลย — แค่อ่าน href ตรงๆ
        จาก DOM เพื่อความปลอดภัย (กัน accidental navigation)

        ระบบมี agent คนอื่นแย่งหยิบเคสพร้อมกันได้ตลอดเวลา (เจอจริงจากการทดสอบ) — ถ้าเปิดเคส
        ไม่ทัน (คนอื่นหยิบ/ปิดไปก่อน) จะไม่ throw error แต่วนกลับไป poll หาเคสถัดไปแทนโดยอัตโนมัติ."""
        self.page.goto(BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        elapsed = 0.0
        while True:
            if self._new_case_count() > 0:
                logger.info("  [7Chat] พบเคสใหม่แล้ว — ลองเปิดเคสแรกในแท็บ 'เคสใหม่'")
                crm_case_id = self._try_open_first_new_case()
                if crm_case_id is not None:
                    return crm_case_id
                # ไม่เจอเคสที่ยังไม่เคยทำ (ซ้ำของเดิม/แย่งไม่ทัน) — รอสักครู่ก่อน poll ใหม่
                # กัน busy-loop ยิงรัวๆ ไม่หยุดตอนเคสเดิมยังค้างอยู่
                self.page.wait_for_timeout(int(poll_interval_sec * 1000))
                self.page.goto(BASE_URL, wait_until="domcontentloaded")
                self.page.wait_for_timeout(1500)
                elapsed += poll_interval_sec
                if timeout_sec is not None and elapsed >= timeout_sec:
                    raise TimeoutError(f"ไม่มีเคสใหม่เข้ามาภายใน {timeout_sec}s")
                continue

            logger.info(
                "  [7Chat] ยังไม่มีเคสใหม่ (รอมาแล้ว %.0fs) — เช็คอีกครั้งใน %.0fs",
                elapsed, poll_interval_sec,
            )
            self.page.wait_for_timeout(int(poll_interval_sec * 1000))
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            elapsed += poll_interval_sec
            if timeout_sec is not None and elapsed >= timeout_sec:
                raise TimeoutError(f"ไม่มีเคสใหม่เข้ามาภายใน {timeout_sec}s")
