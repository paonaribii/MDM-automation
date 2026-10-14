"""MDM client — ค้นหา device ด้วย Serial Number และดึงสถานะ MDM.

Flow (จาก Codegen):
1. Login ที่ {MDM_BASE_URL}/login
2. ไปที่ Devices
3. ค้นหาด้วย Serial Number (Apple format: ~10 ตัว เช่น ABCD1234EF)
4. คลิกเข้าไปดูรายละเอียด device
5. ดึงข้อมูล: Model, Status, Supervised, Enrolled, Profile Password
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Page

from qa_automation.config import env


@dataclass
class MDMDevice:
    serial_number: str = ""
    model: str = ""
    status: str = ""              # Normal / Lost / Stolen
    supervised: str = ""          # true / false (icon สีเขียว = true)
    enrolled: str = ""            # true / false (icon สีเขียว = true)
    profile_downloaded: str = ""  # true / false (badge "Profile download" ในตาราง list
                                    # ผลค้นหา — ไม่ใช่แถว "Profile Password" ในหน้า detail
                                    # ซึ่งเป็นช่องรหัสผ่านที่ซ่อนไว้ คนละอย่างกันเลย)
    found: bool = False           # พบ device ในระบบหรือไม่


class MDMClient:
    """Client สำหรับ MDM website."""

    LOGIN_URL = f"{env('MDM_BASE_URL')}/login"
    DEVICES_URL = f"{env('MDM_BASE_URL')}/devices"

    def __init__(self, page: Page) -> None:
        self.page = page
        self._logged_in = False

    def login(self, username: str, password: str) -> bool:
        """Login เข้า MDM.

        ถ้า context มี storage_state (cookie) ที่ยัง valid อยู่แล้ว การ goto จะ
        redirect ออกจากหน้า login ทันที — เช็คก่อนเพื่อข้าม login ซ้ำ.
        """
        self.page.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        if "/login" not in self.page.url:
            self._logged_in = True
            return True

        self.page.get_by_role("textbox", name="Username Username").fill(username)
        self.page.get_by_role("textbox", name="Password Password").fill(password)
        self.page.get_by_role("button", name="Login").click()

        try:
            self.page.wait_for_url(
                lambda url: "/login" not in url,
                timeout=10000
            )
            self._logged_in = True
            return True
        except Exception:
            return False

    def search_device(self, serial_number: str) -> MDMDevice:
        """ค้นหา device ด้วย Serial Number."""
        result = MDMDevice(serial_number=serial_number)

        self.page.goto(self.DEVICES_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        # ค้นหา + รอผลจริง — retry ถ้าตารางยัง filter ไม่ทัน (พบว่า wait คงที่ตัวเดียว
        # บางครั้งเร็วเกินไป ทำให้ได้ false negative เป็นครั้งคราว)
        # timeout ของ fill() ยาวขึ้น (เดิม default 30000ms) — เครื่องมีภาระเพิ่มขึ้นตอน
        # รันคู่กับ ITEC desktop automation (pywinauto) พร้อมกัน ทำให้บางครั้งหน้า
        # Devices โหลด/ตอบสนองช้ากว่าปกติ
        cell = None
        for attempt in range(3):
            search_box = self.page.get_by_role("textbox", name="Search by Serial Number or")
            search_box.fill("", timeout=60000)
            search_box.fill(serial_number, timeout=60000)
            try:
                candidate = self.page.get_by_role("cell", name=serial_number).first
                candidate.wait_for(state="visible", timeout=4000)
                cell = candidate
                break
            except Exception:
                self.page.wait_for_timeout(1000)
                continue

        if cell is None:
            result.found = False
            return result

        result.found = True
        row = cell.locator("xpath=ancestor::tr")

        # ดึง Status จากผลลัพธ์การค้นหา (อยู่ใน row เดียวกับ serial)
        try:
            status_cell = row.get_by_role("cell").filter(
                has_text=re.compile(r"Normal|Lost|Stolen|Retired", re.IGNORECASE)
            ).first
            result.status = status_cell.inner_text(timeout=1000).strip()
        except Exception:
            pass

        # ดึง model จากผลลัพธ์
        try:
            cells = row.get_by_role("cell").all()
            for c in cells:
                txt = c.inner_text(timeout=500).strip()
                if txt and txt != serial_number and any(
                    brand in txt for brand in ["iPhone", "iPad", "Apple", "Samsung", "OPPO", "Vivo"]
                ):
                    result.model = txt
                    break
        except Exception:
            pass

        # ดึง badge วงกลมสีเขียว/แดง "Profile download" / "Enrolled" / "Supervised" จาก
        # ตาราง list ผลค้นหาโดยตรง (column 6/7/8 ตามลำดับ ยืนยันจาก selector จริงที่
        # ผู้ใช้ส่งมา: #table-user > ... > td:nth-child(6/7/8)) — เดิมอ่าน Supervised/
        # Enrolled จากหน้า detail แทน (ใช้ได้ถูกต้องบังเอิญ) ส่วน Profile download เดิม
        # ไปอ่านผิดจุด (แถว "Profile Password" ในการ์ด General ของหน้า detail เป็นช่อง
        # รหัสผ่านที่ซ่อนไว้ ไม่ใช่ badge สถานะเลย) — ยืนยันจากเคสจริง
        # ตอนนี้อ่านทั้ง 3 ค่าจากแหล่งเดียวกัน (ตาราง list) ให้สอดคล้องและเชื่อถือได้แน่นอน
        result.profile_downloaded = self._read_list_badge(row, "profile")
        result.enrolled = self._read_list_badge(row, "enrolled")
        result.supervised = self._read_list_badge(row, "supervised")

        # คลิกเข้าไปดูรายละเอียด
        try:
            cell.click()
            self.page.wait_for_load_state("domcontentloaded")
            self.page.wait_for_timeout(1500)
            result = self._extract_device_detail(result)
        except Exception:
            try:
                detail_link = self.page.get_by_role("link").filter(has_text=serial_number).first
                detail_link.click()
                self.page.wait_for_load_state("domcontentloaded")
                self.page.wait_for_timeout(1500)
                result = self._extract_device_detail(result)
            except Exception:
                pass

        return result

    def _read_list_badge(self, row, header_keyword: str) -> str:
        """หา column ในตาราง list ผลค้นหาที่ header มีคำว่า header_keyword (case-insensitive)
        ปน แล้วเช็คสีไอคอนในเซลล์นั้นของแถวนี้ — คืน "true"/"false". ยืนยัน column จริง
        จาก selector ที่ผู้ใช้ส่งมา (#table-user, td:nth-child(6/7/8) = Profile
        download/Enrolled/Supervised)."""
        try:
            col_idx = self.page.evaluate(
                """(keyword) => {
                    const table = document.querySelector('#table-user') || document;
                    const headers = Array.from(table.querySelectorAll('th'));
                    return headers.findIndex(
                        h => h.textContent.trim().toLowerCase().includes(keyword)
                    );
                }""",
                header_keyword,
            )
            if col_idx is None or col_idx < 0:
                return ""
            cell = row.locator("td").nth(col_idx)
            is_green = cell.evaluate(
                """(cellEl) => {
                    const icon = cellEl.querySelector('svg, i');
                    if (!icon) return false;
                    const color = window.getComputedStyle(icon).color;
                    const m = color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                    if (m) {
                        const r = +m[1], g = +m[2], b = +m[3];
                        return g > 150 && r < 120 && b < 120;
                    }
                    return false;
                }"""
            )
            return "true" if is_green else "false"
        except Exception:
            return ""

    def _extract_device_detail(self, result: MDMDevice) -> MDMDevice:
        """ดึง Supervised/Enrolled/Profile icon จากหน้า device detail (Vuetify layout)."""
        self.page.wait_for_timeout(1000)

        # JavaScript — ตรวจสอบ icon color ด้วย computed RGB
        # สีเขียวจริง: rgb(86, 202, 0) → g=202 > 150, r=86 < 120, b=0 < 120
        JS = r"""() => {
            const out = {};

            function isGreen(el) {
                if (!el) return false;
                const color = window.getComputedStyle(el).color;
                const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                if (m) {
                    const r = +m[1], g = +m[2], b = +m[3];
                    if (g > 150 && r < 120 && b < 120) return true;
                }
                const cls = Array.from(el.classList || []).join(' ');
                if (cls.includes('success') || cls.includes('green') ||
                    cls.includes('mdi-check')) return true;
                return false;
            }

            document.querySelectorAll('.v-row').forEach(row => {
                const label_el = row.querySelector('.v-col-sm-3 span, .v-col-sm-3 div');
                if (!label_el) return;
                const label = label_el.textContent.trim().replace(/:$/, '');
                if (!label) return;

                const icon = row.querySelector('.v-col-sm-9 .v-icon, .v-col-sm-9 i');
                if (icon) {
                    out[label] = isGreen(icon) ? 'true' : 'false';
                } else {
                    const val_el = row.querySelector('.v-col-sm-9 p, .v-col-sm-9 span:not(.mdi)');
                    out[label] = val_el ? val_el.textContent.trim() : '';
                }
            });
            return out;
        }"""

        pairs: dict = self.page.evaluate(JS)

        if pairs:
            # supervised/enrolled/profile_downloaded อ่านจากตาราง list ผลค้นหาไปแล้วใน
            # search_device() (แหล่งเดียวที่ยืนยันแล้วว่าถูกต้องแน่นอน) — ใช้ผลจากหน้านี้
            # เป็น fallback เฉพาะตอนที่อ่านจาก list ไม่ได้เลยเท่านั้น (เช่น หา column ไม่เจอ)
            if not result.supervised:
                result.supervised = pairs.get("Supervised", "")
            if not result.enrolled:
                result.enrolled = pairs.get("Enrolled", "")
            if not result.status:
                result.status = pairs.get("Status", "")

        return result

    def is_device_compliant(self, device: MDMDevice, expected_model: str = "") -> tuple[bool, str]:
        """
        ตรวจสอบ MDM compliance:
        - พบใน MDM
        - Status == Normal
        - Supervised == true (icon สีเขียว)
        - Enrolled == true (icon สีเขียว)
        - Profile Password == true (icon สีเขียว) = 3rd checkmark
        - ชื่อรุ่นตรงกับ expected_model
        """
        if not device.found:
            return False, f"ไม่พบ Serial Number {device.serial_number} ในระบบ MDM"

        if device.status.strip().lower() not in ("normal", ""):
            return False, f"MDM Status ผิดปกติ: {device.status} (ต้องเป็น Normal)"

        # ตรวจ 3 icon สีเขียว
        checks = {
            "Supervised":       device.supervised,
            "Enrolled":         device.enrolled,
            "Profile Password": device.profile_downloaded,
        }
        failed = [k for k, v in checks.items() if v == "false"]
        if failed:
            return False, f"MDM icon ไม่เป็นสีเขียว: {', '.join(failed)}"

        # ตรวจชื่อรุ่น
        if expected_model and device.model:
            mdm_model = device.model.strip().lower()
            exp_model = expected_model.strip().lower()
            if exp_model not in mdm_model and mdm_model not in exp_model:
                return False, f"รุ่นไม่ตรง MDM='{device.model}' vs expected='{expected_model}'"

        return True, "MDM: device ปกติ (Normal, Supervised✓, Enrolled✓, Profile✓)"

    def search_device_by_branch(
        self,
        branch_name: str,
        sheet_data: list[dict],
        expected_model: str = "",
    ) -> MDMDevice:
        """
        Fallback: ถ้าไม่พบ Serial Number ใน MDM
        → ค้นหา Device ID จาก MDM Sheet ด้วยชื่อสาขา
        → เปิดหน้า /devices/{id} แล้วดึงข้อมูล

        คืน MDMDevice ของเครื่องแรกที่พบและมี model ตรง (หรือเครื่องแรกสุด)
        """
        from qa_automation.mdm_sheet import find_branch_device_ids

        device_ids = find_branch_device_ids(branch_name, sheet_data)
        if not device_ids:
            return MDMDevice(found=False)

        first_found: MDMDevice | None = None

        # ลองดูแต่ละ Device ID
        for dev_id in device_ids:
            url = f"{self.DEVICES_URL}/{dev_id}"
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                self.page.wait_for_timeout(1500)

                # ตรวจว่า page ไม่ redirect กลับ (404 / ไม่มี device)
                if "/devices" not in self.page.url or "404" in self.page.title():
                    continue

                result = MDMDevice(serial_number=f"[branch-id:{dev_id}]", found=True)
                result = self._extract_device_detail(result)

                # ดึง model name จากหน้า detail
                try:
                    model_name_el = self.page.get_by_text(
                        re.compile(r"iPhone|iPad|Samsung|OPPO", re.IGNORECASE),
                        exact=False
                    ).first
                    result.model = model_name_el.inner_text(timeout=1000).strip()
                except Exception:
                    pass

                if first_found is None:
                    first_found = result

                # ถ้า model ตรง — คืนเลย
                if expected_model and result.model:
                    if (expected_model.lower() in result.model.lower() or
                            result.model.lower() in expected_model.lower()):
                        return result

            except Exception:
                continue

        # ไม่มีรุ่นไหนตรงเป๊ะ — คืน device แรกที่เจอจริง (สาขามีเครื่องขึ้นทะเบียน MDM
        # อยู่แล้ว ต่อให้รุ่นไม่ตรงเป๊ะก็ยังถือว่า "มี MACBOOK MDM" — เดิมโค้ดทิ้งผลนี้ไป
        # เฉยๆ ทำให้ MACBOOK MDM ได้ No เกือบตลอดตอน fallback ทั้งที่จริงมีเครื่องอยู่)
        if first_found is not None:
            return first_found
        return MDMDevice(found=False)

