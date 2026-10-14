from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CaseRecord:
    """ผลการตรวจสอบเคส QA — ใช้ส่งออกไปยัง QA Recheck Sheet.

    Flow: Helpdesk (text) → iLoan/ITOS → ITEC → เขียนผล QA Recheck
    (ไม่เช็ครูป/OCR, ไม่เช็ค MDM อีกต่อไป)
    """

    case_id: str = ""

    # ── overall ──────────────────────────────────────────────────
    qa_status: str = ""     # PASS / FAIL / NEED_EVIDENCE
    reason: str = ""        # เหตุผลสรุป
    match: str = ""         # "Yes" / "No" — คอลัมน์ MATCH (yes/no)
    audit_time: str = ""    # เวลาที่ตรวจ (วัน/เวลา ที่รัน automation ตรวจเคสนี้)
    flow: str = ""          # "ITOS" / "K2" — เคสไหนมีเลขที่ใบคำขอรูปแบบ ITOS ใช้ iLoan,
                            # ไม่มีใช้ K2 ค้นด้วยชื่อลูกค้าแทน (K2 ไม่เช็ค match เชิงลึก)

    # ── QA Recheck Sheet columns (ตรงตามชีทจริง) ───────────────────
    customer_name: str = ""     # ชื่อลูกค้า
    branch_id_name: str = ""    # Branch ID+Name
    platform: str = ""          # Android / IOS
    loan_app_number: str = ""   # เลขที่ใบคำขอ
    model_name: str = ""        # ชื่อรุ่น
    storage: str = ""           # ความจุ
    color: str = ""             # สี
    price: str = ""             # ราคา
    down_payment_net: str = ""  # เงินดาวน์สุทธิ
    down_payment: str = ""      # เงินดาวน์ที่ลูกค้าจ่ายจริง
    trade_in: str = ""          # Trade-in (มูลค่าเครื่องเก่าแลกใหม่)
    discount_package: str = ""      # ส่วนลด package ที่ได้รับ (จาก ITOS)
    discount_down_payment: str = "" # ส่วนลดเงินดาวน์ (จาก ITOS)
    discount_other: str = ""        # ส่วนลดอื่นๆ (จาก ITOS)
    discount_device: str = ""       # ส่วนลดค่าเครื่อง (จาก ITOS)
    serial_number: str = ""     # Serial Number (ข้อมูล Series No ใหม่)
    serial_source: str = ""     # Check S/N — แหล่งที่มาของ Serial Number (DOA (iLoan) / Contract.pdf (OCR) / N/A)
    imei: str = ""              # IMEI (ข้อมูล Series No เดิม)
    imei_source: str = ""       # Check IMEI — แหล่งที่มาของ IMEI (DOA (iLoan) / Contract.pdf (OCR) / ITEC Comb. / Helpdesk)
    mdm_found: str = ""         # MDM (yes/no) — เจอจาก serial number โดยตรง
    macbook_mdm: str = ""       # MACBOOK MDM (yes/no) — เจอจาก serial หรือ fallback ผ่าน Branch ID
    mdm_case: str = ""          # MDM Case ID (เช่น "MDM-260726-0120") จาก Helpdesk
    remark: str = ""            # สรุปจุดที่ไม่ match (คอลัมน์ Remark:)
    failed_fields: list[str] = field(default_factory=list)  # ชื่อ field ที่ต้องไฮไลท์สีเหลือง

    # ── per-item pass/fail ภายใน (ใช้ตัดสิน match รวม, ไม่เขียนลงชีทตรงๆ) ──
    chk_branch: str = ""
    chk_customer_name: str = ""
    chk_loan_app_number: str = ""
    chk_serial_imei: str = ""
    chk_exec_time: str = ""
    chk_category: str = ""
    chk_brand: str = ""
    chk_model: str = ""
    chk_color: str = ""
    chk_storage: str = ""
    chk_price_main: str = ""
    chk_discount_pkg: str = ""
    chk_trade_in: str = ""
    chk_trade_in_value: str = ""
    chk_price_total: str = ""
    chk_down_net: str = ""
    chk_down_actual: str = ""
    chk_serial_new: str = ""
    chk_sbv_company: str = ""
    chk_siv_company: str = ""

    @classmethod
    def needs_evidence(cls, case_id: str, reason: str) -> "CaseRecord":
        return cls(
            case_id=case_id,
            qa_status="NEED_EVIDENCE",
            match="No",
            reason=reason,
        )
