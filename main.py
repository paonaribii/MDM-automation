"""QA Automation (CRM) — ตรวจสอบเคส Submit MDM + บันทึกผลลงชีท + ลงทะเบียน ITOS + ตอบกลับ/ปิดเคส
(ถ้าเปิด --auto-reply ไว้).

Flow เต็ม (ทดสอบรันจริงแล้วว่าใช้งานได้ครบทุกขั้นตอน รวมลงทะเบียน ITOS + ตอบกลับ/ปิดเคส CRM):

  1. รอเคสใหม่จาก 7Chat (--wait-7chat) — เปิด 7Chat, poll แท็บ "เคสใหม่" จนกว่าจะมีเคสเข้า
     แล้วเปิดเคส**แรกในแท็บนั้นเท่านั้น** (ห้ามแตะเคสแท็บ "เคสวันนี้"/เคส Solve/Close แล้ว)
     ตามลิงก์ไปหน้า CRM case (หรือระบุ --case-id เองก็ได้ ไม่ต้องรอ 7Chat)
     ใช้ --limit N เพื่อวนทำซ้ำ N เคสในรันเดียว (ไม่ใส่ = ทำแค่ 1 เคสจบ)
  2. อ่านข้อมูลเคสจาก CRM (เลขที่ใบคำขอ, ชื่อลูกค้า, สาขา, Serial/IMEI) จากฟิลด์โครงสร้าง
     ในหน้าเคสตรงๆ (ไม่ต้อง parse จาก free-text message เหมือนสมัย Helpdesk)
  3. ถ้าไม่มีเลขที่ใบคำขอรูปแบบ ITOS (XXXXXX-XXXXXX) — ข้ามเคสนั้นไปเลย ไม่เขียนที่ไหน
     (ใช้กฎเดิมจากระบบ Helpdesk ไปก่อนตามที่ยืนยันจากผู้ใช้) แล้ววนไปรอเคสถัดไปต่อ
  4. มีเลขที่ใบคำขอ ITOS:
       a. iLoan/ITOS — ค้นหาใบคำขอ ดึงข้อมูลสินค้า + Serial/IMEI (DOA)
       b. ITEC      — ค้นหาสดจากโปรแกรม ITEC desktop (Sell Bill View / Sale Inv View)
       c. MDM/MacBook MDM check (เฉพาะ iOS) — มี branch fallback + CMC Summary fallback
       d. evaluate_case() เช็ค match แบบละเอียด — สรุปจุดที่ไม่ match ไว้ใน remark
  5. เขียนผลลัพธ์แยก 3 แท็บใหม่ในชีท "AI Submit MDM" เดิม ผ่าน Google Sheets API:
     - "CRM Submit"    — iOS ที่ match ครบทุกจุด
     - "CRM Android"   — Android ที่ match ครบทุกจุด
     - "CRM No Submit" — เคสที่ไม่ match ไม่ว่าแพลตฟอร์มไหน
  6. ถ้า match=Yes **และ** เปิด --auto-reply ไว้:
       a. iloan.submit_mdm() — กดยืนยันลงทะเบียนสินค้าใน ITOS (เมนู "ลงทะเบียนสินค้า")
       b. ลงทะเบียน ITOS สำเร็จเท่านั้นถึงจะตอบกลับสคริปต์ 1.1 + ปิดเคสใน CRM ต่อ — ถ้า
          ลงทะเบียนไม่สำเร็จ ห้ามตอบกลับ/ปิดเคส (รอรันรอบถัดไปลองใหม่)
     (ยืนยันจากผู้ใช้ — ธุรกรรมจริงที่แก้คืนยาก จึงปิดไว้เป็นค่าเริ่มต้น ต้องเปิดเองชัดเจน)
     ถ้า match=No — ไม่แตะ ITOS/CRM เลย ข้ามไปรอเคสใหม่ถัดไปทันที
  7. ถ้า --wait-7chat และยังไม่ครบ --limit — วนกลับไปข้อ 1 รอเคสถัดไป
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from qa_automation.config import _load_local_env
from qa_automation.cmc_summary import read_cmc_summary_sheet, find_cmc_case_id
from qa_automation.crm import CRMClient
from qa_automation.iloan import ILoanClient, is_ios_device, normalize_apple_serial
from qa_automation.itec_desktop import ITECDesktopClient
from qa_automation.mdm import MDMClient
from qa_automation.mdm_sheet import read_mdm_branch_sheet, find_branch_device_ids
from qa_automation.models import CaseRecord
from qa_automation.rules import evaluate_case
from qa_automation.sevenchat import SevenChatClient
from qa_automation.sheet_writer import (
    QA_SHEET_URL,
    SheetsWriter,
    write_local_xlsx_report,
)
from playwright.sync_api import sync_playwright


def _force_fail(rec: CaseRecord, note: str) -> None:
    """บังคับผล FAIL/No พร้อมต่อท้าย remark/reason ด้วยเหตุผลจาก MDM/CMC check."""
    existing_lines = [ln for ln in rec.remark.splitlines() if ln.strip()]
    existing_lines.append(f"{len(existing_lines) + 1}. {note}")
    rec.remark = "\n".join(existing_lines)
    rec.reason = rec.reason + f"; {note}" if rec.qa_status == "FAIL" else f"ไม่ match: {note}"
    rec.qa_status = "FAIL"
    rec.match = "No"


def _iter_case_ids(
    case_ids: list[str] | None,
    sevenchat_client: SevenChatClient | None,
    poll_interval_sec: float,
    wait_timeout_sec: float | None,
    limit: int | None,
) -> Iterator[str]:
    """คืนเลขเคสทีละตัว — จาก --case-id ที่ระบุไว้ตรงๆ, หรือถ้าใช้ --wait-7chat ก็รอเคส
    ใหม่จาก 7Chat ทีละเคส (วนซ้ำจนครบ limit — ไม่ใส่ limit ก็รอบเดียวจบเหมือนเดิม)."""
    if sevenchat_client is None:
        for cid in case_ids or []:
            yield cid
        return

    count = 0
    while limit is None or count < limit:
        logging.info(
            "Waiting for a new case in 7Chat 'เคสใหม่' tab... (%d/%s)",
            count + 1, limit if limit is not None else "?",
        )
        crm_case_id = sevenchat_client.wait_for_new_case(
            poll_interval_sec=poll_interval_sec, timeout_sec=wait_timeout_sec
        )
        yield crm_case_id
        count += 1


def run(
    case_ids: list[str] | None,
    output_file: Path,
    headless: bool,
    skip_sheets: bool,
    wait_7chat: bool = False,
    poll_interval_sec: float = 5.0,
    wait_timeout_sec: float | None = None,
    limit: int | None = None,
    auto_reply: bool = False,
) -> None:
    _load_local_env()

    # -- Load env vars ---------------------------------------------
    crm_user       = os.getenv("CRM_USERNAME", "").strip()
    crm_pass       = os.getenv("CRM_PASSWORD", "").strip()
    il_user        = os.getenv("ILOAN_USERNAME", "").strip()
    il_pass        = os.getenv("ILOAN_PASSWORD", "").strip()
    mdm_user       = os.getenv("MDM_USERNAME", "").strip()
    mdm_pass       = os.getenv("MDM_PASSWORD", "").strip()
    mdm_sheet_url  = os.getenv("MDM_SHEET_URL", "").strip()
    cmc_sheet_url  = os.getenv("CMC_SUMMARY_SHEET_URL", "").strip()
    itec_user      = os.getenv("ITEC_USERNAME", "").strip()
    itec_pass      = os.getenv("ITEC_PASSWORD", "").strip()
    sevenchat_user = os.getenv("SEVENCHAT_USERNAME", "").strip()
    sevenchat_pass = os.getenv("SEVENCHAT_PASSWORD", "").strip()
    google_creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

    # -- ขั้น 0: อ่าน static data (ไม่ต้อง browser) --------------
    logging.info("Reading MDM Branch Sheet...")
    mdm_sheet_data = read_mdm_branch_sheet(mdm_sheet_url) if mdm_sheet_url else []
    logging.info("  -> %d branch entries loaded", len(mdm_sheet_data))

    logging.info("Reading CMC Summary Sheet...")
    cmc_data = read_cmc_summary_sheet(cmc_sheet_url) if cmc_sheet_url else {}
    logging.info("  -> %d CMC Summary entries loaded", len(cmc_data))

    # เชื่อม Google Sheets ผ่าน API (service account) — ไม่ต้อง login ผ่านเบราว์เซอร์แล้ว
    sheets_writer: SheetsWriter | None = None
    if not skip_sheets and google_creds_file:
        try:
            sheets_writer = SheetsWriter(google_creds_file)
        except Exception as exc:
            logging.exception(
                "เชื่อม Google Sheets API ไม่สำเร็จ (จะข้ามการเขียนชีทไปเลย เขียนแค่ local xlsx): %s", exc
            )
            sheets_writer = None
    elif not google_creds_file and not skip_sheets:
        logging.warning("GOOGLE_SERVICE_ACCOUNT_FILE ไม่ได้ตั้งค่า — ข้ามการเขียนไปยัง Google Sheets")

    logging.info("Logging in to ITEC desktop app (Sell Bill View / Sale Inv View)...")
    itec_client = ITECDesktopClient()
    if not itec_client.login(itec_user, itec_pass):
        raise RuntimeError("ITEC login failed")

    # ต้องปิด instance ITEC ทุกครั้งที่จบงาน (ไม่ว่าจะสำเร็จหรือ error กลางทาง) ไม่งั้น
    # process ITECStock2007.exe ค้างสะสมจนล็อกอินรอบถัดไปล้มเหลว
    try:
        records: list[CaseRecord] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)

            # ใช้ context/page แยกกันระหว่าง iLoan / MDM / CRM / 7Chat — ห้ามใช้ page ร่วมกัน
            # เพราะแต่ละ client navigate page ไปหน้าของตัวเอง จะทำให้ client อื่นเสีย
            # state ถ้าใช้ page เดียวกัน (ยืนยันจากปัญหาจริงสมัยระบบ Helpdesk)
            state_dir = Path("state")
            state_dir.mkdir(exist_ok=True)
            mdm_state_path = state_dir / "mdm_session.json"

            # iLoan: ห้าม reuse cookie ข้ามรัน — cookie เก่าทำให้ navigate หลัง login ค้าง/
            # timeout ซ้ำๆ (ยืนยันจากการทดสอบจริงสมัยระบบ Helpdesk) จึงล็อกอินสดทุกรัน
            iloan_context = browser.new_context()
            iloan_page = iloan_context.new_page()
            iloan = ILoanClient(iloan_page, iloan_context)

            logging.info("Logging in to all systems...")
            if not iloan.login(il_user, il_pass):
                raise RuntimeError("iLoan login failed")

            sevenchat_client: SevenChatClient | None = None
            if wait_7chat:
                sevenchat_context = browser.new_context()
                sevenchat_page = sevenchat_context.new_page()
                sevenchat_client = SevenChatClient(sevenchat_page)
                if not sevenchat_client.login(sevenchat_user, sevenchat_pass):
                    raise RuntimeError("7Chat login failed")

            crm_context = browser.new_context()
            crm_page = crm_context.new_page()
            crm_client = CRMClient(crm_page)
            if not crm_client.login(crm_user, crm_pass):
                raise RuntimeError("CRM login failed")

            mdm_context = browser.new_context(
                storage_state=str(mdm_state_path) if mdm_state_path.exists() else None
            )
            mdm_page = mdm_context.new_page()
            mdm = MDMClient(mdm_page)
            if not mdm.login(mdm_user, mdm_pass):
                raise RuntimeError("MDM login failed")

            for case_id in _iter_case_ids(
                case_ids, sevenchat_client, poll_interval_sec, wait_timeout_sec, limit
            ):
                logging.info("-------- Checking case %s --------", case_id)

                try:
                    logging.info("  [CRM] Reading case %s...", case_id)
                    crm_case = crm_client.get_case(case_id)
                    logging.info(
                        "    customer=%s branch=%s loan_app_number=%s serial/imei=%s",
                        crm_case.customer_name, crm_case.branch,
                        crm_case.loan_app_number or "(ไม่มี)", crm_case.imei_serial,
                    )

                    if not crm_case.loan_app_number:
                        # ไม่มีเลขที่ใบคำขอรูปแบบ ITOS — ข้ามเคสนี้ไปเลย (ไม่เขียนที่ไหน)
                        # ใช้กฎเดิมจากระบบ Helpdesk ไปก่อนตามที่ยืนยันจากผู้ใช้
                        logging.warning("  [SKIP] ไม่มีเลขที่ใบคำขอแบบ ITOS ในข้อมูลเคส")
                        continue

                    # กันบันทึกซ้ำ — เคสนี้อาจเคยถูกรันไปแล้วในโปรเซสก่อนหน้า (ยังค้างเป็น
                    # "เคสใหม่" ใน 7Chat เพราะไม่มีใครกด "รับเคส") เช็คจากชีทเองเพราะยังถูก
                    # ต้องแม้เป็นคนละรันกัน (ตัวแปร in-memory เช็คได้แค่ในรันเดียวกันเท่านั้น)
                    if sheets_writer is not None and sheets_writer.case_already_recorded(case_id):
                        logging.warning("  [SKIP] เคส %s เคยบันทึกลงชีทไปแล้ว (รันก่อนหน้า) — ข้าม", case_id)
                        continue

                    loan_num = crm_case.loan_app_number

                    logging.info("  [iLoan] Searching %s...", loan_num)
                    il_data = iloan.get_loan(loan_num)
                    logging.info("    model=%s  price_total=%s  down=%s  imei_old=%s  serial_new=%s",
                                 il_data.model if il_data else "-",
                                 il_data.price_total if il_data else "-",
                                 il_data.down_payment if il_data else "-",
                                 il_data.imei_old if il_data else "-",
                                 il_data.serial_new if il_data else "-")

                    # ค้นหาใน ITEC — ถ้าฝั่งไหนไม่เจอ (SBV หรือ SIV แยกกัน ไม่ใช่แค่ตอนไม่
                    # เจอทั้งคู่) ลองใหม่อีกสูงสุด 2 รอบต่อฝั่ง (หน่วงเวลาก่อนรอบถัดไป) เผื่อ
                    # ข้อมูลเคสเพิ่งสร้างยังไม่ sync เข้า ITEC ทัน (ยืนยันจากผู้ใช้ — ต่างจาก
                    # _search_with_recovery ใน itec_desktop.py ที่ retry เฉพาะตอน window
                    # ค้าง/error เท่านั้น ไม่ได้ retry ตอน "หาไม่เจอ") — ค้นซ้ำแล้วเก็บผลที่
                    # ดีที่สุดของแต่ละฝั่งไว้ (ฝั่งที่เคยเจอแล้วรอบก่อนไม่หายไปแม้รอบหลังจะหา
                    # ไม่เจอซ้ำ)
                    itec_rec = itec_client.search(loan_num)
                    for itec_attempt in range(2):
                        if itec_rec.sbv_found and itec_rec.siv_found:
                            break
                        missing = [
                            name for name, found in
                            (("SBV", itec_rec.sbv_found), ("SIV", itec_rec.siv_found))
                            if not found
                        ]
                        logging.info(
                            "  [ITEC] ยังไม่เจอ %s รอบที่ %d — รอ 10s แล้วลองค้นซ้ำ",
                            "/".join(missing), itec_attempt + 1,
                        )
                        time.sleep(10)
                        retry_rec = itec_client.search(loan_num)
                        if retry_rec.sbv_found and not itec_rec.sbv_found:
                            for f in fields(itec_rec):
                                if f.name.startswith("sbv_"):
                                    setattr(itec_rec, f.name, getattr(retry_rec, f.name))
                        if retry_rec.siv_found and not itec_rec.siv_found:
                            for f in fields(itec_rec):
                                if f.name.startswith("siv_"):
                                    setattr(itec_rec, f.name, getattr(retry_rec, f.name))
                    logging.info("  [ITEC] sbv_found=%s siv_found=%s  sbv_price=%s siv_price=%s",
                                 itec_rec.sbv_found, itec_rec.siv_found,
                                 itec_rec.sbv_price_total, itec_rec.siv_price_total)

                    is_ios = is_ios_device(il_data.brand, il_data.model)
                    platform = "IOS" if is_ios else "Android"
                    logging.info("  [Platform] %s (brand=%s model=%s)", platform, il_data.brand, il_data.model)

                    if is_ios:
                        if not il_data.serial_new:
                            # ไม่มี Serial Number เลย — ข้ามการค้นหา MDM ด้วย IMEI แทนไปเลย
                            # (MDM ค้นด้วย serial ไม่ใช่ IMEI ผลไม่น่าเชื่อถือ) และข้าม branch
                            # fallback ด้วย เพราะไม่มี serial ยืนยันไม่ได้ว่าเครื่องนี้ตรงกับ
                            # ที่สาขาขึ้นทะเบียนไว้จริงหรือไม่ ปล่อยให้ผ่านได้กรณีเดียวคือเจอ
                            # MDM Case ID ใน CMC Summary เท่านั้น
                            logging.info(
                                "  [MDM] ไม่มี Serial Number — ข้ามการค้นหา MDM/branch fallback ไปเช็ค CMC Summary เลย"
                            )
                            mdm_found_direct = False
                            macbook_mdm_found = False
                        else:
                            # Serial Number ของ Apple จริงไม่ขึ้นต้นด้วย "S"
                            mdm_serial = normalize_apple_serial(il_data.serial_new) or il_data.imei_old
                            mdm_device = mdm.search_device(mdm_serial) if mdm_serial else None
                            # เจอ device อย่างเดียวไม่พอ — ต้องติ๊กถูกสีเขียวครบทั้ง 3 อัน
                            # (Profile downloaded / Enrolled / Supervised) ด้วยถึงจะถือว่า
                            # MDM (yes/no) = Yes จริง
                            mdm_found_direct = bool(
                                mdm_device
                                and mdm_device.found
                                and mdm_device.supervised == "true"
                                and mdm_device.enrolled == "true"
                                and mdm_device.profile_downloaded == "true"
                            )
                            logging.info(
                                "  [MDM] serial=%s  found=%s  supervised=%s enrolled=%s profile=%s  -> mdm_found_direct=%s",
                                mdm_serial,
                                bool(mdm_device and mdm_device.found),
                                mdm_device.supervised if mdm_device else "",
                                mdm_device.enrolled if mdm_device else "",
                                mdm_device.profile_downloaded if mdm_device else "",
                                mdm_found_direct,
                            )

                            if mdm_found_direct:
                                macbook_mdm_found = True
                            else:
                                branch_ids = find_branch_device_ids(crm_case.branch or il_data.branch, mdm_sheet_data)
                                macbook_mdm_found = False
                                if branch_ids:
                                    fallback = mdm.search_device_by_branch(
                                        crm_case.branch or il_data.branch, mdm_sheet_data, il_data.model
                                    )
                                    macbook_mdm_found = bool(fallback and fallback.found)
                                logging.info("  [MDM] branch fallback found=%s", macbook_mdm_found)
                    else:
                        logging.info("  [MDM] skipped (Android)")

                    rec = evaluate_case(
                        case_id=case_id,
                        crm_case=crm_case,
                        iloan=il_data,
                        itec=itec_rec,
                    )
                    rec.platform = platform
                    rec.audit_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    if is_ios:
                        rec.mdm_found   = "Yes" if mdm_found_direct else "No"
                        rec.macbook_mdm = "Yes" if (mdm_found_direct or macbook_mdm_found) else "No"
                        # เกณฑ์ MDM/MACBOOK MDM:
                        #   Yes/Yes -> ผ่านปกติ (ใช้ผล evaluate_case ตามเดิม)
                        #   No/Yes  -> สาขามี MacBook ขึ้นทะเบียน MDM แต่ยังไม่ submit เครื่องนี้จริง -> FAIL
                        #   No/No   -> เช็คชีท CMC Summary แท็บ "เคสที่ไม่มีเครื่องลงโปรแกรม" ต่อ
                        #              เจอ MDM Case ID = ยังถือว่าผ่าน, ไม่เจอ = FAIL
                        if rec.mdm_found == "No" and rec.macbook_mdm == "Yes":
                            note = (
                                "ไม่พบ Serial Number เครื่องนี้ใน MDM (สาขามี MacBook ขึ้นทะเบียน "
                                "MDM แล้ว แต่ยังไม่ได้ submit เครื่องนี้เข้า MDM)"
                            )
                            logging.info("  [MDM] No/Yes — %s", note)
                            _force_fail(rec, note)
                        elif rec.mdm_found == "No" and rec.macbook_mdm == "No":
                            cmc_case_id = find_cmc_case_id(loan_num, cmc_data)
                            if cmc_case_id:
                                rec.mdm_case = cmc_case_id
                                logging.info("  [CMC] found MDM Case ID %s — treated as OK", cmc_case_id)
                            else:
                                note = (
                                    "ไม่พบเครื่องใน MDM, สาขาไม่มี MacBook ขึ้นทะเบียน, "
                                    "และไม่พบ MDM Case ID ใน CMC Summary"
                                )
                                logging.info("  [CMC] not found — %s", note)
                                _force_fail(rec, note)
                    else:
                        rec.mdm_found     = "N/A"
                        rec.macbook_mdm   = "N/A"
                        rec.serial_number = "N/A"
                        rec.serial_source = "N/A"
                    logging.info("  [RESULT] qa_status=%s  match=%s  reason=%s",
                                 rec.qa_status, rec.match, rec.reason)

                    records.append(rec)

                    if sheets_writer is not None:
                        try:
                            sheets_writer.write_case(rec)
                        except Exception as exc:
                            logging.exception(
                                "  [Sheet] เขียนเคส %s ลง Google Sheets ไม่สำเร็จ (จะยังเก็บใน "
                                "local xlsx ตอนจบอยู่): %s", case_id, exc,
                            )

                    # เคส match=Yes + เปิด --auto-reply: ต้องกดยืนยันลงทะเบียนใน ITOS
                    # (iloan.submit_mdm) ให้สำเร็จก่อน ถึงจะไปตอบกลับ+ปิดเคสใน CRM ต่อ — ถ้า
                    # ลงทะเบียน ITOS ไม่สำเร็จ ห้ามตอบกลับ/ปิดเคส (จะกลายเป็นโกหกลูกค้าว่า
                    # เสร็จแล้วทั้งที่ยังไม่ได้ลงทะเบียนจริง) ปล่อยรอรันรอบถัดไปมาลองใหม่แทน
                    # (ยืนยันจากผู้ใช้) — เคส match=No ข้ามไปรอเคสใหม่ถัดไปทันที ไม่แตะทั้งคู่เลย
                    if rec.match == "Yes" and auto_reply:
                        logging.info("  [iLoan] Submitting product registration (ITOS)...")
                        itos_ok = iloan.submit_mdm(loan_num)
                        logging.info("  [iLoan] submit_mdm result=%s", itos_ok)
                        if itos_ok:
                            logging.info("  [CRM] Submitting reply + closing case...")
                            ok = crm_client.submit_reply_and_close(loan_num)
                            logging.info("  [CRM] reply+close result=%s", ok)
                        else:
                            logging.warning(
                                "  [iLoan] ลงทะเบียน ITOS ไม่สำเร็จ — ข้ามการตอบกลับ/ปิดเคสใน "
                                "CRM ไปเลย (รอรันรอบถัดไปลองใหม่)"
                            )
                    elif rec.match == "Yes":
                        logging.info("  [CRM] match=Yes แต่ --auto-reply ปิดอยู่ — ข้ามการลงทะเบียน ITOS/ตอบกลับ/ปิดเคส")

                except Exception as exc:
                    logging.exception("  [SKIP] case %s failed: %s", case_id, exc)
                    continue

            browser.close()

        # -- ขั้น 2: เขียน Local XLSX (Google Sheets เขียนไปทีละเคสในลูปแล้ว) -------
        submit_records = [r for r in records if r.platform == "IOS" and r.match == "Yes"]
        android_records = [r for r in records if r.platform == "Android" and r.match == "Yes"]
        no_submit_records = [r for r in records if r.match != "Yes"]
        write_local_xlsx_report(submit_records, android_records, no_submit_records, output_file)

        print(f"\n[DONE] ตรวจสอบ {len(records)} เคสเสร็จสิ้น")
        print(f"  Local report: {output_file}")
        if sheets_writer is not None:
            print(f"  Google Sheet: {QA_SHEET_URL}")
        print(
            f"\n  Submit: {len(submit_records)}  Android: {len(android_records)}  "
            f"No Submit: {len(no_submit_records)}"
        )
    finally:
        itec_client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QA Automation (CRM) — ตรวจสอบ + บันทึกผล + ตอบกลับ/ปิดเคส")
    parser.add_argument("--case-id", type=str, action="append", dest="case_ids",
                        help="เลขเคส CRM ที่จะตรวจสอบ (ระบุซ้ำได้หลายครั้งเพื่อตรวจหลายเคส)")
    parser.add_argument("--wait-7chat", action="store_true",
                        help="แทนที่จะระบุ --case-id เอง ให้รอเคสใหม่จากแท็บ 'เคสใหม่' ใน 7Chat "
                             "(poll เป็นช่วงๆ) แล้วตรวจเคสแรกที่เจอ")
    parser.add_argument("--limit", type=int, default=None,
                        help="ใช้กับ --wait-7chat — จำนวนเคสสูงสุดที่จะวนทำในรันนี้ "
                             "(ไม่ใส่ = ทำแค่ 1 เคสจบ)")
    parser.add_argument("--poll-interval", type=float, default=5.0,
                        help="วินาทีต่อรอบที่เช็คเคสใหม่ใน 7Chat (ใช้กับ --wait-7chat เท่านั้น)")
    parser.add_argument("--wait-timeout", type=float, default=None,
                        help="เลิกรอถ้าไม่มีเคสใหม่ภายในกี่วินาที (ใช้กับ --wait-7chat, ค่า "
                             "default คือรอไม่จำกัดเวลา)")
    parser.add_argument("--auto-reply", action="store_true",
                        help="เคส match=Yes: ลงทะเบียนสินค้าใน ITOS (iloan.submit_mdm) แล้ว "
                             "ตอบกลับสคริปต์ 1.1 + ปิดเคสใน CRM จริงอัตโนมัติ (ธุรกรรมจริง "
                             "แก้คืนยาก — ปิดไว้เป็นค่าเริ่มต้น ต้องเปิดเองชัดเจน)")
    parser.add_argument("--output", type=Path, default=Path("output/qa_report.xlsx"),
                        help="Path สำหรับ local XLSX report")
    parser.add_argument("--headed", action="store_true",
                        help="แสดง browser window ระหว่างรัน")
    parser.add_argument("--skip-sheets", action="store_true",
                        help="ข้ามการเขียนกลับไปยัง Google Sheets (เขียนเฉพาะ local xlsx)")
    args = parser.parse_args()
    if not args.case_ids and not args.wait_7chat:
        parser.error("ต้องระบุ --case-id อย่างน้อย 1 ตัว หรือใช้ --wait-7chat")
    return args


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    run(
        args.case_ids, args.output, headless=not args.headed, skip_sheets=args.skip_sheets,
        wait_7chat=args.wait_7chat, poll_interval_sec=args.poll_interval,
        wait_timeout_sec=args.wait_timeout, limit=args.limit, auto_reply=args.auto_reply,
    )


if __name__ == "__main__":
    main()
