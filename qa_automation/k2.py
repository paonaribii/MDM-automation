"""k2.py — client สำหรับเว็บ K2 (K2_FORM_URL ใน .env) — fallback เมื่อ Helpdesk ไม่มี
เลขที่ใบคำขอรูปแบบ ITOS (XXXXXX-XXXXXX) ให้ค้นหาด้วยชื่อลูกค้าแทนใน Business Support ->
Customer E-Credit File.

Login: HTTP Basic auth (Windows Integrated Auth ของเว็บนี้ยอมรับ Basic auth ผ่าน
BrowserContext(http_credentials=...) — ต้องสร้าง context แบบนี้ตั้งแต่ต้นใน main.py).

โครงสร้างหน้าเว็บ:
- เมนู "Business Support" -> "Customer E-Credit File" เปิดตารางค้นหาในแท็บ default
- ผลค้นหา/ฟอร์มรายละเอียด render อยู่ใน iframe (ไม่ใช่ top-level page)
- ค้นหาด้วยชื่อได้ผลลัพธ์เป็นสัญญาหลายสัญญาต่อ 1 ชื่อได้ (ประวัติเก่า+ปัจจุบัน) —
  ต้องเลือกสัญญาที่สถานะ "ลูกหนี้ปกติ" หรือ "เสร็จสมบูรณ์" เท่านั้น (ตามที่ระบุใน
  script ตอบกลับของเจ้าหน้าที่เอง) ถ้ามีหลายสัญญาที่ผ่านเกณฑ์ เลือกอันที่วันที่เริ่ม
  สัญญาล่าสุด
- หน้ารายละเอียดสัญญาโหลดช้ามาก (~30 วินาที) เป็น K2 SmartForm — แต่ละฟิลด์คือ
  <span class="SFC ... Label" title="ชื่อ label"> ตามด้วย <input> (อ่าน .value ได้ตรงๆ)
  หรือ dropdown widget แบบกำหนดเอง (ไม่มี .value ให้ใช้ td.innerText ตัด label ออก)
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from qa_automation.config import env

# สถานะสัญญาที่ถือว่า "ใช้งานอยู่จริง" — อิงตาม script มาตรฐานที่เจ้าหน้าที่ใช้ตอบกลับ:
# "ต้องตรวจสอบสถานะสัญญาในระบบ ITOS / K2 ต้องเป็น 'ลูกหนี้ปกติ' หรือ 'เสร็จสมบูรณ์' เท่านั้น"
_ACTIVE_STATUSES = {"ลูกหนี้ปกติ", "เสร็จสมบูรณ์"}

_GET_FIELD_JS = """
(label) => {
    const spans = Array.from(document.querySelectorAll('span.SFC'));
    const target = spans.find(el => (el.title || '').trim() === label.trim());
    if (!target) return null;
    const td = target.closest('td');
    if (!td) return null;
    const input = td.querySelector('input');
    if (input && input.type !== 'hidden') return input.value;
    // dropdown widget แบบกำหนดเอง — ไม่มี select ที่ selectedIndex ใช้งานได้จริง
    // ใช้ td.innerText ทั้งก้อนแล้วตัด label ออก ค่าที่เหลือคือค่าที่เลือกจริง
    const full = (td.innerText || '').trim();
    const labelText = (target.innerText || '').trim();
    let rest = full;
    if (labelText && full.startsWith(labelText)) {
        rest = full.slice(labelText.length).trim();
    }
    return rest || null;
}
"""


@dataclass
class K2Case:
    contract_number: str = ""
    contract_status: str = ""
    customer_name: str = ""
    branch: str = ""
    product_category: str = ""
    brand: str = ""
    model: str = ""
    storage: str = ""
    color: str = ""
    price_main: str = ""          # ราคาสินค้าหลัก (บาท)
    price_accessory: str = ""     # ราคาสินค้าเสริม (บาท)
    price_total_insured: str = "" # ราคาสินค้ารวมทั้งสิ้น (บาท) — รวมประกันแล้ว
    price_insurance: str = ""     # ราคาประกันคุ้มครองชีวิต (บาท)
    down_payment: str = ""        # จำนวนเงินดาวน์ (บาท)
    serial_number: str = ""


def _clean_field(v: str | None) -> str:
    if not v:
        return ""
    # ค่าจาก dropdown widget อาจมีขยะต่อท้าย (ไอคอนลูกศร ฯลฯ) ที่ตามมาเป็นบรรทัดใหม่
    return v.splitlines()[0].strip()


def _parse_thai_date(s: str) -> tuple[int, int, int]:
    """แปลง 'D/M/YYYY' (พ.ศ.) เป็น (ปี, เดือน, วัน) เพื่อเรียงลำดับ — คืน (0,0,0) ถ้า parse ไม่ได้."""
    try:
        d, m, y = s.strip().split("/")
        return int(y), int(m), int(d)
    except Exception:
        return 0, 0, 0


class K2Client:
    """Client สำหรับเว็บ K2 — ต้องสร้าง Page จาก BrowserContext ที่มี http_credentials แล้ว."""

    URL = env("K2_FORM_URL")

    def __init__(self, page: Page) -> None:
        self.page = page
        self._logged_in = False

    def login(self) -> bool:
        """เข้าเว็บ K2 (login ผ่าน http_credentials ของ context อยู่แล้ว) แล้วรอเมนูโหลดเสร็จ."""
        self.page.goto(self.URL, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(3000)
        try:
            self.page.locator("text=Loading Menu").wait_for(state="hidden", timeout=20000)
        except Exception:
            pass
        self.page.wait_for_timeout(1500)

        if not self._goto_list():
            return False

        self._logged_in = True
        return True

    def _goto_list(self) -> bool:
        """เปิดหน้ารายการ Customer E-Credit File ใหม่เสมอ — จำเป็นก่อนค้นหาทุกครั้ง เพราะ
        หลังเปิดดูรายละเอียดสัญญา 1 ครั้ง เฟรมเดิม (list) จะถูกแทนที่ด้วยฟอร์มรายละเอียด
        ไปแล้ว ค้นหาลูกค้าคนถัดไปจะเจอฟอร์มเก่าค้างอยู่แทนตารางค้นหาถ้าไม่ reset ก่อน."""
        try:
            # timeout ยาวขึ้น (เดิม 5000ms) — ตอนรันจริงมี context iLoan/MDM เปิดค้าง
            # พร้อมกันในเบราว์เซอร์เดียวกัน ทำให้เมนูโหลดช้ากว่าตอนทดสอบเดี่ยวๆ มาก
            self.page.get_by_text("Business Support", exact=False).first.click(timeout=20000)
            self.page.wait_for_timeout(1000)
            self.page.get_by_text("Customer E-Credit File", exact=False).first.click(timeout=20000)
            self.page.wait_for_timeout(2500)
            self.page.mouse.click(10, 400)  # ปิด dropdown เมนูที่ค้างอยู่
            self.page.wait_for_timeout(500)
            return True
        except Exception:
            return False

    def _list_frame(self):
        for f in self.page.frames:
            if "Summary" in f.url or "E-Customer" in f.url:
                return f
        return None

    def get_case(self, customer_name: str) -> K2Case | None:
        """ค้นหาด้วยชื่อลูกค้า เลือกสัญญาที่ active แล้วดึงข้อมูลรายละเอียด.

        คืน None ถ้าค้นหาไม่เจอ หรือไม่มีสัญญาที่สถานะ active เลย.
        """
        if not self._logged_in:
            return None

        if not self._goto_list():
            return None

        frame = self._list_frame()
        if frame is None:
            return None

        rows = self._search(frame, customer_name)
        if not rows:
            return None

        active = [r for r in rows if r["status"] in _ACTIVE_STATUSES]
        if not active:
            return None
        active.sort(key=lambda r: _parse_thai_date(r["start_date"]), reverse=True)
        chosen = active[0]

        return self._open_contract(frame, chosen["contract_number"], chosen["status"])

    def _search(self, frame, name: str) -> list[dict]:
        """ค้นหาในช่อง Quick Search แล้ว parse ผลลัพธ์เป็น list of dict.

        ช่อง search ไม่มี selector ที่เสถียร (custom widget) — ใช้ตำแหน่งคลิกคงที่
        (ยืนยันแล้วจากการสำรวจหน้าเว็บจริง) retry ได้ถ้า AJAX ยังไม่ทันอัพเดต.
        """
        for attempt in range(3):
            self.page.mouse.click(795, 299)
            self.page.wait_for_timeout(300)
            self.page.keyboard.press("Control+a")
            self.page.keyboard.type(name)
            self.page.wait_for_timeout(500)
            self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(1500)
            try:
                frame.wait_for_function(
                    """() => {
                        const el = document.querySelector('body');
                        return el && !el.innerText.includes('of 14308');
                    }""",
                    timeout=12000,
                )
            except Exception:
                pass
            self.page.wait_for_timeout(2500)

            rows_loc = frame.locator("tr").all()
            results = []
            for r in rows_loc:
                tds = [t.strip() for t in r.locator("td").all_inner_texts() if t.strip()]
                if len(tds) == 5 and tds[0].isdigit():
                    results.append({
                        "contract_number": tds[0],
                        "customer_name": tds[1],
                        "start_date": tds[2],
                        "end_date": tds[3],
                        "status": tds[4],
                    })
            if results:
                return results
            # ยังเป็นรายการ default (unfiltered) อยู่ — ลองใหม่
        return []

    def _open_contract(self, frame, contract_number: str, status: str) -> K2Case | None:
        try:
            frame.get_by_text(contract_number, exact=True).first.dblclick(timeout=5000)
        except Exception:
            return None
        # ฟอร์มรายละเอียดโหลดช้ามาก (K2 SmartForm) — ยืนยันจากการทดสอบจริงว่าต้องรอ ~30 วิ
        self.page.wait_for_timeout(30000)

        detail_frame = None
        for fr in self.page.frames:
            if "Electronic+Customer" in fr.url:
                detail_frame = fr
                break
        if detail_frame is None:
            return None

        result = K2Case(contract_number=contract_number, contract_status=status)

        # แท็บ "ข้อมูลผู้เช่าซื้อ" (default หลังเปิด) — ชื่อ-นามสกุลลูกค้า
        first_name = _clean_field(self._field(detail_frame, "ชื่อ (ภาษาไทย)"))
        last_name = _clean_field(self._field(detail_frame, "นามสกุล (ภาษาไทย)"))
        result.customer_name = f"{first_name} {last_name}".strip()

        # สลับไปแท็บ "ข้อมูลเงื่อนไขเช่าซื้อ" (ตำแหน่งที่ 4 ในแถบแท็บ — ยืนยันจากการสำรวจ)
        try:
            self.page.mouse.click(376, 204)
            self.page.wait_for_timeout(4000)
        except Exception:
            pass

        result.branch           = _clean_field(self._field(detail_frame, "เลือกรับที่สาขา"))
        result.product_category = _clean_field(self._field(detail_frame, "หมวดสินค้า"))
        result.brand              = _clean_field(self._field(detail_frame, "ยี่ห้อสินค้า"))
        result.model              = _clean_field(self._field(detail_frame, "รุ่นสินค้า"))
        result.storage             = _clean_field(self._field(detail_frame, "ความจุ"))
        result.color                = _clean_field(self._field(detail_frame, "สี"))
        result.price_main          = _clean_field(self._field(detail_frame, "ราคาสินค้าหลัก (บาท)"))
        result.price_accessory    = _clean_field(self._field(detail_frame, "ราคาสินค้าเสริม (บาท)"))
        result.price_insurance    = _clean_field(self._field(detail_frame, "ราคาประกันคุ้มครองชีวิต  (บาท)"))
        result.price_total_insured = _clean_field(self._field(detail_frame, "ราคาสินค้ารวมทั้งสิ้น (บาท)"))
        result.down_payment        = _clean_field(self._field(detail_frame, "จำนวนเงินดาวน์ (บาท)"))
        result.serial_number       = _clean_field(self._field(detail_frame, "Serial Number"))

        return result

    def _field(self, frame, label: str) -> str | None:
        try:
            return frame.evaluate(_GET_FIELD_JS, label)
        except Exception:
            return None
