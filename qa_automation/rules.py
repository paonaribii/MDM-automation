"""rules.py — ตรวจสอบความถูกต้องของข้อมูลข้าม CRM / iLoan(ITOS) / ITEC.

MATCH (yes/no) ต้องตรงกันครบทุกแหล่งที่ระบุไว้ต่อหัวข้อ — แหล่งไหนไม่มีข้อมูล
หรือมีแต่ไม่ตรงกัน ถือว่าไม่ match ทันที (ไม่ผ่อนปรน):

  CRM ต้องมี: ชื่อ-สกุลลูกค้า, เลขที่ใบคำขอ, Branch ID+Name
  iLoan(ITOS) ต้องมี: ชื่อ-สกุลลูกค้า, เลขที่ใบคำขอ, Branch ID+Name,
                      ชื่อรุ่น/ความจุ/สี, ราคาสินค้ารวมทั้งสิ้น, เงินดาวน์สุทธิ
  ITEC Sell Bill View ต้องมี: ชื่อ-สกุลลูกค้า, เลขที่ใบคำขอ, Branch ID+Name,
                      ราคาสุทธิ(Total Price), เงินดาวน์สุทธิ,
                      Customer(Name)=="บริษัท ธันเดอร์ ฟิน ฟิน จำกัด"
  ITEC Sale Inv View ต้องมี: ชื่อ-สกุลลูกค้า, เลขที่ใบคำขอ, Branch ID+Name,
                      ราคาสุทธิ(Total Price After Discount),
                      Customer(Name) ขึ้นต้นด้วย "บริษัท ธันเดอร์..." (ถูก mask)
  ITEC Comb.: Product Name ต้องตรงกับชื่อรุ่นใน iLoan(ITOS)

ทุกฟิลด์ที่ชื่อ-สกุลลูกค้า/Branch/ราคา ต้องตรงกันข้าม "ทุก" แหล่งที่ระบุไว้ข้างต้น
(ไม่ใช่แค่ไม่ขัดแย้งกัน — ต้องมีครบและตรงกันจริง)
"""
from __future__ import annotations

import re

from qa_automation.models import CaseRecord
from qa_automation.crm import CRMCase
from qa_automation.iloan import ILoanCase
from qa_automation.itec_desktop import ITECRecord
from qa_automation.k2 import K2Case


# ---- helpers ----

_PASS = "pass"
_FAIL = "fail"
_NA = "N/A"

_EXPECTED_COMPANY_NAME = "บริษัทธันเดอร์ฟินฟินจำกัด"  # เทียบแบบตัดช่องว่างออก
_EXPECTED_COMPANY_PREFIX = "บริษัทธันเดอร"             # SIV ถูก mask ด้วย ***** เช็คแค่ prefix


def _pf(condition: bool) -> str:
    return _PASS if condition else _FAIL


def _normalize_price(s: str) -> str:
    """ลบ comma/บาท/฿/ช่องว่าง แล้วทำให้เป็น float string เพื่อเปรียบเทียบ."""
    s = re.sub(r"[,\s฿]", "", (s or "").replace("บาท", "").strip())
    try:
        return str(float(s))
    except ValueError:
        return s.lower()


def _parse_price_float(s: str) -> float | None:
    """แปลงข้อความราคา (เช่น '฿29,500.00' / '29,500.00 บาท') เป็น float — คืน None ถ้า parse ไม่ได้."""
    s = re.sub(r"[,\s฿]", "", (s or "").replace("บาท", "").strip())
    try:
        return float(s)
    except ValueError:
        return None


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _text_contains(needle: str, haystack: str) -> bool:
    if not needle or not haystack:
        return False
    return needle.strip().lower() in haystack.strip().lower()


def _with_baht(s: str) -> str:
    """เติมหน่วย 'บาท' ต่อท้ายถ้ายังไม่มี (เช่นค่าเงินดาวน์จาก iLoan ที่ไม่มีหน่วยติดมา)."""
    s = (s or "").strip()
    if not s or "บาท" in s:
        return s
    return f"{s} บาท"


# รูปแบบ branch string คือ "{รหัสสาขา} : {ชื่อสาขา}" เช่น "ID583 : BN-..." หรือ
# "FCB3077 : BN Franchise-..." (สาขาแฟรนไชส์ไม่ได้ขึ้นต้นด้วย "ID" เสมอไป) — เดิมโค้ด
# จับเฉพาะ "ID\d+" ทำให้รหัสสาขาแฟรนไชส์อ่านไม่ออกเลย (กลายเป็นค่าว่างทุกแหล่ง แล้วโดน
# ตัดสินว่า "ไม่มีข้อมูล" ทั้งที่จริงๆ ตรงกันหมด) จึงเปลี่ยนมาจับรหัสตัวแรกสุดก่อน " : " แทน
_BRANCH_ID_RE = re.compile(r"^\s*([A-Za-z0-9]+)\s*:")


def _branch_id(s: str) -> str:
    m = _BRANCH_ID_RE.search(s or "")
    return m.group(1).upper() if m else ""


_STORAGE_RE = re.compile(r"(\d+\s*(?:GB|TB))", re.IGNORECASE)


def _extract_storage(product_name: str) -> str:
    m = _STORAGE_RE.search(product_name or "")
    return m.group(1).replace(" ", "") if m else ""


def _extract_color(product_name: str) -> str:
    m = _STORAGE_RE.search(product_name or "")
    if not m:
        return ""
    return (product_name or "")[m.end():].strip()


# สีฝั่ง ITEC มักมีปีรุ่น/generation/สเปคอื่นปนมาด้วย (เช่น "- Space Grey 2026",
# "Silver 2025 (11th Gen)", "with Standard glass - Space Black") ที่ไม่มีฝั่ง
# ITOS เลย (แค่ "Space Gray"/"Silver"/"Space Black" เฉยๆ) ยืนยันจากผู้ใช้ว่าสี
# ตรงกันจริง แค่ต้องตัดส่วนที่ไม่ใช่สีออกก่อนเทียบ — ตัดวงเล็บท้าย, ปี (ตัวเลข
# 4 หลักขึ้นต้น 19/20), เอาเฉพาะส่วนหลัง " - " ตัวสุดท้ายถ้ามี (สีมักอยู่ท้ายสุด
# หลังคำอธิบายสเปค เช่น "with Standard glass - Space Black" -> "Space Black"),
# และรวม "Grey"/"Gray" เป็นคำเดียวกัน (สะกดต่างกันแต่เป็นสีเดียวกัน)
_COLOR_TRAILING_PAREN_RE = re.compile(r"\([^)]*\)\s*$")
_COLOR_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _normalize_color(s: str) -> str:
    s = (s or "").strip()
    s = _COLOR_TRAILING_PAREN_RE.sub("", s).strip(" -/")
    s = _COLOR_YEAR_RE.sub("", s).strip(" -/")
    if " - " in s:
        s = s.rsplit(" - ", 1)[-1]
    s = s.strip(" -/")
    s = re.sub(r"\bgrey\b", "gray", s, flags=re.IGNORECASE)
    return s


# ชื่อสินค้าใน ITOS สั้น (แค่ยี่ห้อ+รุ่นหลัก) แต่ใน ITEC Comb. ยาวกว่ามาก (มีคำว่า
# "Smartphone" คั่น, รุ่น/generation, ความจุ, สี, เครือข่ายต่อท้าย) เช่น
#   ITOS = "OPPO Reno16 (8+256) (5G)"           Comb.= "OPPO Smartphone Reno16 (8+256) Pop White (5G) F+"
#   ITOS = "IPAD Air 11 M4 6th GEN (2026) WI-FI" Comb.= "Apple iPad Air 11 : M4 Wi-Fi 256GB - Blue 2026"
# เช็คนี้เทียบแค่ "ยี่ห้อ+รุ่นหลัก" เท่านั้น (ความจุ/สี เช็คแยกที่อื่นอยู่แล้ว) จึงตัด
# ส่วนในวงเล็บ (สเปกความจุ/เครือข่าย), generation/Wi-Fi, และเครื่องหมายวรรคตอนออกก่อนเทียบ
# — ใช้กับทุก platform เหมือนกันหมด (ผู้ใช้ยืนยันแล้วว่า iOS ก็ควรผ่อนปรนแบบเดียวกัน)
_MODEL_PAREN_RE = re.compile(r"\([^)]*\)")
# "F+" ไม่มี \w ต่อท้ายให้ \b จับ (ตัวถัดไปมักเป็นช่องว่าง/จบข้อความ ซึ่งไม่ใช่ word-boundary
# เพราะ "+" ก็ไม่ใช่ word char อยู่แล้ว) จึงต้องเช็คแยกด้วย lookaround แทน \b
_MODEL_FILLER_RE = re.compile(
    r"\b(?:Smartphone|5G|4G|Wi-?Fi|\d+(?:st|nd|rd|th)\s*Gen(?:eration)?)\b|(?<!\S)F\+(?!\S)",
    re.IGNORECASE,
)
_MODEL_PUNCT_RE = re.compile(r"[:\-–,]")


def _core_model_name(product_name: str) -> str:
    s = _MODEL_PAREN_RE.sub(" ", product_name or "")
    s = _MODEL_FILLER_RE.sub(" ", s)
    s = _MODEL_PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _compare_field(itos_val: str, itec_val: str, exact: bool = False) -> tuple[str, str]:
    """เทียบค่าฝั่ง ITOS กับค่าที่ได้/extract จากฝั่ง ITEC (ชื่อรุ่น/ความจุ/สี).

    FAIL ทันทีถ้าฝั่งใดฝั่งหนึ่งไม่มีค่า (รวมถึง extract ความจุ/สีจาก ITEC ไม่ได้)
    — ไม่ผ่อนปรนเป็น N/A (ยืนยันจากผู้ใช้: ขาดฝั่งไหนก็ถือว่าไม่ match). ถ้ามีทั้งคู่
    — เทียบแบบ exact (ความจุ/สี) หรือ substring ทั้งสองทาง (ชื่อรุ่น, ผ่าน
    _core_model_name เหมือน Comb. check เดิม เพราะ ITEC มักมีคำเติม/ยี่ห้อ/สเปกเพิ่มมาด้วย).
    """
    if not itec_val or not itos_val:
        missing = [name for name, v in (("ITOS", itos_val), ("ITEC", itec_val)) if not v]
        return _FAIL, f"ไม่มีข้อมูลจาก {', '.join(missing)}"
    if exact:
        ok = _norm_text(itos_val) == _norm_text(itec_val)
    else:
        core_itos = _core_model_name(itos_val)
        core_itec = _core_model_name(itec_val)
        ok = _text_contains(core_itos, core_itec) or _text_contains(core_itec, core_itos)
    return _pf(ok), ("" if ok else f"ITOS={itos_val}, ITEC={itec_val}")


def _require_all(sources: dict[str, str], is_price: bool = False) -> tuple[str, str]:
    """ต้องมีค่าในทุกแหล่งที่ระบุ และตรงกันหมด — แหล่งไหนขาดหรือไม่ตรง = FAIL."""
    missing = [name for name, v in sources.items() if not v or not v.strip()]
    if missing:
        return _FAIL, f"ไม่มีข้อมูลจาก {', '.join(missing)}"

    norm = _normalize_price if is_price else _norm_text
    normed = {name: norm(v) for name, v in sources.items()}
    if len(set(normed.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sources.items())
        return _FAIL, f"ค่าไม่ตรงกัน ({detail})"
    return _PASS, ""


def _parse_exec_minutes(exec_time: str) -> float:
    """แปลงข้อความเวลา เช่น '12 นาที 30 วินาที' → float minutes.
    รองรับ non-breaking space (\xa0) และ ชั่วโมง (hours) ด้วย
    """
    import unicodedata
    t = unicodedata.normalize("NFC", exec_time)
    t = t.replace("\xa0", " ").replace(" ", " ")
    minutes = 0.0
    h = re.search(r"(\d+)\s*ชั่วโมง", t)
    m = re.search(r"(\d+)\s*นาที", t)
    s = re.search(r"(\d+)\s*วินาที", t)
    if h:
        minutes += float(h.group(1)) * 60.0
    if m:
        minutes += float(m.group(1))
    if s:
        minutes += float(s.group(1)) / 60.0
    return minutes


# ---- main evaluate function ----

def evaluate_case(
    case_id: str,
    crm_case: CRMCase,
    iloan: ILoanCase,
    itec: ITECRecord | None = None,
) -> CaseRecord:
    """ตรวจสอบครบทุกจุด และคืน CaseRecord พร้อมผล match."""

    rec = CaseRecord(case_id=case_id, flow="ITOS")
    itec = itec or ITECRecord()

    # ────────────────────────────────────────────────
    # 1. ชื่อ-สกุลลูกค้า — CRM / ITOS / ITEC-SBV / ITEC-SIV ต้องมีและตรงกันหมด
    # ────────────────────────────────────────────────
    chk_customer_name, detail_customer_name = _require_all({
        "CRM": crm_case.customer_name,
        "ITOS": iloan.customer_name,
        "ITEC-SBV": itec.sbv_customer_name,
        "ITEC-SIV": itec.siv_customer_name,
    })
    rec.chk_customer_name = chk_customer_name

    # ────────────────────────────────────────────────
    # 2. เลขที่ใบคำขอ — CRM / ITOS / ITEC-SBV(พบ) / ITEC-SIV(พบ)
    # ────────────────────────────────────────────────
    chk_loan_app_number, detail_loan = _require_all({
        "CRM": crm_case.loan_app_number,
        "ITOS": iloan.loan_app_number,
        "ITEC-SBV": crm_case.loan_app_number if itec.sbv_found else "",
        "ITEC-SIV": crm_case.loan_app_number if itec.siv_found else "",
    })
    rec.chk_loan_app_number = chk_loan_app_number

    # ────────────────────────────────────────────────
    # 3. Branch ID+Name — CRM / ITOS / ITEC-SBV / ITEC-SIV (เทียบด้วยเลข ID)
    # ────────────────────────────────────────────────
    chk_branch, detail_branch = _require_all({
        "CRM": _branch_id(crm_case.branch),
        "ITOS": _branch_id(iloan.branch),
        "ITEC-SBV": _branch_id(itec.sbv_branch_name),
        "ITEC-SIV": _branch_id(itec.siv_branch_name),
    })
    rec.chk_branch = chk_branch

    # ────────────────────────────────────────────────
    # 4. ชื่อรุ่น/ความจุ/สี — ITOS เทียบกับ ITEC Product Name (ใช้ Comb. ถ้ามี ไม่งั้น
    # ใช้ Sell Bill View -> Product (Name) แทน เพราะ Comb. report ไม่มีในโปรแกรม
    # ITEC desktop ตอนนี้ — Product (Name) รูปแบบ "Apple iPhone 17 Pro Max 256GB
    # Deep Blue" เทียบชื่อรุ่นแบบ substring เหมือน Comb. เดิม ส่วนความจุ/สี extract
    # จากรูปแบบ "<ความจุGB/TB> <สี>" ต่อท้ายชื่อรุ่นมาเทียบแบบ exact)
    # ────────────────────────────────────────────────
    itec_product_name = itec.comb_product_name if itec.comb_found else itec.sbv_product_name

    chk_model, detail_model = _compare_field(iloan.model, itec_product_name)
    chk_storage, detail_storage = _compare_field(
        iloan.storage, _extract_storage(itec_product_name), exact=True
    )
    chk_color, detail_color = _compare_field(
        _normalize_color(iloan.color), _normalize_color(_extract_color(itec_product_name)), exact=True
    )
    rec.chk_model = chk_model
    rec.chk_storage = chk_storage
    rec.chk_color = chk_color

    # ────────────────────────────────────────────────
    # 5. ราคาสินค้ารวมทั้งสิ้น (ราคาสุทธิ) — ITOS / ITEC-SBV / ITEC-SIV
    # ────────────────────────────────────────────────
    chk_price_total, detail_price = _require_all({
        "ITOS": iloan.price_total,
        "ITEC-SBV": itec.sbv_price_total,
        "ITEC-SIV": itec.siv_price_total,
    }, is_price=True)
    rec.chk_price_total = chk_price_total

    # ────────────────────────────────────────────────
    # 6. เงินดาวน์สุทธิ — ITOS / ITEC-SBV (Sale Inv View ไม่มีคอลัมน์นี้)
    # ────────────────────────────────────────────────
    chk_down_net, detail_down = _require_all({
        "ITOS": iloan.down_payment_net,
        "ITEC-SBV": itec.sbv_down_payment_net,
    }, is_price=True)
    rec.chk_down_net = chk_down_net

    # ────────────────────────────────────────────────
    # 6b. เงินดาวน์ที่ลูกค้าจ่ายจริง — ITOS / ITEC-SBV (คนละคอลัมน์กับเงินดาวน์สุทธิ)
    # ────────────────────────────────────────────────
    chk_down_actual, detail_down_actual = _require_all({
        "ITOS": iloan.down_payment,
        "ITEC-SBV": itec.sbv_down_payment_actual,
    }, is_price=True)
    rec.chk_down_actual = chk_down_actual

    # ────────────────────────────────────────────────
    # 6c. Trade-in — มูลค่าเครื่องเก่าแลกใหม่ (ITOS) เทียบกับ คูปอง (Coupon) (ITEC-SBV)
    # ────────────────────────────────────────────────
    chk_trade_in_value, detail_trade_in = _require_all({
        "ITOS": iloan.trade_in_value,
        "ITEC-SBV": itec.sbv_coupon,
    }, is_price=True)
    rec.chk_trade_in_value = chk_trade_in_value

    # ────────────────────────────────────────────────
    # 7. Sanity check: ITEC Customer(Name) ต้องเป็นชื่อบริษัท (ไม่ใช่ลูกค้าปลายทาง)
    # ────────────────────────────────────────────────
    sbv_company_ok = _norm_text(itec.sbv_company_name) == _EXPECTED_COMPANY_NAME if itec.sbv_found else False
    rec.chk_sbv_company = _pf(sbv_company_ok) if itec.sbv_found else _FAIL

    siv_company_ok = _norm_text(itec.siv_company_name).startswith(_EXPECTED_COMPANY_PREFIX) if itec.siv_found else False
    rec.chk_siv_company = _pf(siv_company_ok) if itec.siv_found else _FAIL

    # ────────────────────────────────────────────────
    # 7b. Sanity check: "ส่วนลดอื่นๆ" ต้องเป็น 0 เสมอ — ถ้าไม่ใช่ 0 ถือว่าผิด
    # ────────────────────────────────────────────────
    discount_other_val = _parse_price_float(iloan.discount_other)
    discount_other_ok = discount_other_val is not None and discount_other_val == 0
    chk_discount_other = _pf(discount_other_ok)
    detail_discount_other = "" if discount_other_ok else f"ส่วนลดอื่นๆ={iloan.discount_other or '(ไม่มีข้อมูล)'} (ต้องเป็น 0)"

    # ────────────────────────────────────────────────
    # Serial/IMEI — เก็บไว้เป็นข้อมูลอ้างอิง (ไม่ได้อยู่ใน requirement การ match ใหม่)
    # ────────────────────────────────────────────────
    rec.chk_serial_new = _pf(bool(iloan.serial_new)) if iloan.serial_new else _NA

    # ────────────────────────────────────────────────
    # สรุป QA Recheck fields — ถ้าแหล่งข้อมูลไม่ตรงกัน ให้ยึด ITOS (iLoan) เป็นหลักเสมอ
    # (แหล่งอื่นใช้เป็น fallback เฉพาะตอน ITOS ไม่มีข้อมูลเท่านั้น)
    # ────────────────────────────────────────────────
    rec.customer_name   = iloan.customer_name or crm_case.customer_name
    rec.branch_id_name   = iloan.branch or crm_case.branch or itec.sbv_branch_name
    rec.loan_app_number  = iloan.loan_app_number or crm_case.loan_app_number
    rec.model_name        = iloan.model or itec.comb_product_name
    rec.storage            = iloan.storage or _extract_storage(itec.comb_product_name)
    rec.color               = iloan.color or _extract_color(itec.comb_product_name)
    rec.price               = iloan.price_total or itec.sbv_price_total
    rec.down_payment_net  = _with_baht(iloan.down_payment_net or itec.sbv_down_payment_net)
    rec.down_payment      = _with_baht(iloan.down_payment or itec.sbv_down_payment_actual)
    rec.trade_in            = iloan.trade_in_value or itec.sbv_coupon
    rec.discount_package        = _with_baht(iloan.discount_package)
    rec.discount_down_payment   = _with_baht(iloan.discount_down_payment)
    rec.discount_other          = _with_baht(iloan.discount_other)
    rec.discount_device         = _with_baht(iloan.discount_device)
    rec.serial_number     = iloan.serial_new
    rec.serial_source     = iloan.serial_source if iloan.serial_new else ""
    # IMEI fallback chain: DOA table (iLoan) → _Contract.pdf (รวมอยู่ใน iloan.imei_old แล้ว)
    # → ITEC Comb. คอลัมน์ IMEI (AG) → ITEC Sell Bill View -> Serial (dtG02) → CRM
    if iloan.imei_old:
        rec.imei        = iloan.imei_old
        rec.imei_source = iloan.imei_source
    elif itec.comb_imei:
        rec.imei        = itec.comb_imei
        rec.imei_source = "ITEC Comb."
    elif itec.sbv_serial:
        rec.imei        = itec.sbv_serial
        rec.imei_source = "ITEC Sell Bill View"
    elif crm_case.imei_serial:
        rec.imei        = crm_case.imei_serial
        rec.imei_source = "CRM"
    else:
        rec.imei        = ""
        rec.imei_source = ""
    rec.mdm_case = crm_case.mdm_case_id

    # ────────────────────────────────────────────────
    # Overall MATCH (yes/no) — ต้องผ่านทุกข้อ ไม่มีข้อยกเว้น
    # ────────────────────────────────────────────────
    # แต่ละหัวข้อ map ไปยัง field ที่เขียนลงชีท (ใช้ระบุคอลัมน์ที่ต้องไฮไลท์สีเหลือง)
    checks = {
        "ชื่อ-สกุลลูกค้า": (rec.chk_customer_name, detail_customer_name, "customer_name"),
        "เลขที่ใบคำขอ": (rec.chk_loan_app_number, detail_loan, "loan_app_number"),
        "Branch ID+Name": (rec.chk_branch, detail_branch, "branch_id_name"),
        "ชื่อรุ่น": (chk_model, detail_model, "model_name"),
        "ความจุ": (chk_storage, detail_storage, "storage"),
        "สี": (chk_color, detail_color, "color"),
        "ราคาสุทธิ": (rec.chk_price_total, detail_price, "price"),
        "เงินดาวน์สุทธิ": (rec.chk_down_net, detail_down, "down_payment_net"),
        "เงินดาวน์ที่ลูกค้าจ่ายจริง": (rec.chk_down_actual, detail_down_actual, "down_payment"),
        "Trade-in": (rec.chk_trade_in_value, detail_trade_in, "trade_in"),
        "ITEC-SBV Customer(Name)": (
            rec.chk_sbv_company, "" if sbv_company_ok else f"ได้ '{itec.sbv_company_name}'", None
        ),
        "ITEC-SIV Customer(Name)": (
            rec.chk_siv_company, "" if siv_company_ok else f"ได้ '{itec.siv_company_name}'", None
        ),
        "ส่วนลดอื่นๆ": (chk_discount_other, detail_discount_other, "discount_other"),
    }
    failed = [f"{name}({detail})" if detail else name for name, (status, detail, _) in checks.items() if status == _FAIL]
    rec.failed_fields = [
        field for status, _, field in checks.values() if status == _FAIL and field
    ]

    if failed:
        rec.qa_status = "FAIL"
        rec.match = "No"
        rec.reason = f"ไม่ match: {'; '.join(failed)}"
        rec.remark = "\n".join(f"{i}. {item}" for i, item in enumerate(failed, start=1))
    else:
        rec.qa_status = "PASS"
        rec.match = "Yes"
        rec.reason = "ข้อมูลตรงกันครบทุกแหล่งทุกหัวข้อ"
        rec.remark = ""

    return rec


# ────────────────────────────────────────────────────────────────
# K2 flow — CRM ไม่มีเลขที่ใบคำขอรูปแบบ ITOS ให้ค้นหา K2 ด้วยชื่อลูกค้าแทน
# แค่ยืนยัน/ค้นชื่อผ่านเว็บ K2 เท่านั้น — ไม่เช็ค match เชิงลึก (ราคา/รุ่น/serial ฯลฯ)
# (ยังไม่ถูกเรียกใช้จาก main.py ตอนนี้ — เคสไม่มีเลขที่ใบคำขอ ITOS จะข้ามไปเลยตามกฎเดิม)
# ────────────────────────────────────────────────────────────────

def build_k2_record(case_id: str, crm_case: CRMCase, k2: K2Case | None) -> CaseRecord:
    """สร้าง CaseRecord แบบย่อสำหรับเคส K2 — เก็บแค่ case_id/ชื่อลูกค้า/สาขา ไม่เช็ค match."""
    rec = CaseRecord(case_id=case_id, flow="K2")
    rec.customer_name = (k2.customer_name if k2 else "") or crm_case.customer_name
    rec.branch_id_name = crm_case.branch or (k2.branch if k2 else "")
    return rec
