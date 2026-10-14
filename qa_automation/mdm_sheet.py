"""MDM Branch Sheet reader — อ่านข้อมูล MDM device IDs ตามสาขา/Mall.

Sheet structure (MDM_SHEET_URL ใน .env, tab "เช็คเครื่อง MDM"):
  Row 0: Section headers (Studio7, BKK, BaNana Mobile, BaNana)
  Row 1: Column headers (Mall, No. Of Store, Store MDM, Status, ..., BNN(1)..BNN(7))
  Data rows: one row per Mall/location
"""
from __future__ import annotations

import csv
import io
import re
import urllib.request


# Column indices (0-based) — based on sheet structure
_COL_MALL = 1
_COL_STORE_MDM = 3
_COL_STATUS = 4
# BaNana device IDs: columns 13-19
_BNN_COLS = list(range(13, 20))    # BNN(1)..BNN(7)
# BaNana Mobile device IDs: columns 11-12
_BNN_MB_COLS = list(range(11, 13))  # BNN MB(1), BNN MB(2)


_MDM_BRANCH_SHEET_GID = "1734888522"  # แท็บ "เช็คเครื่อง MDM" (ยืนยันจาก URL จริง)


def read_mdm_branch_sheet(sheet_url: str, gid: str = _MDM_BRANCH_SHEET_GID) -> list[dict]:
    """
    อ่าน MDM Branch Sheet แล้วคืน list ของสาขาพร้อม Device IDs.

    Returns list of:
      {
        'mall': str,          # ชื่อ Mall/Location
        'store_mdm': str,     # Store MDM number (Studio7 ID)
        'status': str,        # มีเครื่อง / ยังไม่มีเครื่อง
        'bnn_ids': list[str], # BaNana device IDs
        'bnn_mb_ids': list[str], # BaNana Mobile device IDs
        'all_ids': list[str], # รวมทุก device ID ในสาขา
      }
    """
    csv_url = sheet_url.split("/edit")[0] + f"/export?format=csv&gid={gid}"
    try:
        with urllib.request.urlopen(csv_url, timeout=15) as resp:
            content = resp.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Cannot read MDM Sheet: {e}") from e

    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    if len(rows) < 2:
        return []

    results: list[dict] = []
    for row in rows[2:]:  # skip 2 header rows
        if not row or not row[0].strip():
            continue
        if not row[0].strip().isdigit():
            continue  # skip non-data rows
        if len(row) <= _COL_STATUS:
            continue

        mall = row[_COL_MALL].strip() if len(row) > _COL_MALL else ""
        if not mall:
            continue

        store_mdm = row[_COL_STORE_MDM].strip() if len(row) > _COL_STORE_MDM else ""
        status = row[_COL_STATUS].strip() if len(row) > _COL_STATUS else ""

        # Collect BaNana device IDs
        bnn_ids = [
            row[i].strip() for i in _BNN_COLS
            if i < len(row) and row[i].strip().isdigit()
        ]
        bnn_mb_ids = [
            row[i].strip() for i in _BNN_MB_COLS
            if i < len(row) and row[i].strip().isdigit()
        ]

        results.append({
            "mall": mall,
            "store_mdm": store_mdm,
            "status": status,
            "bnn_ids": bnn_ids,
            "bnn_mb_ids": bnn_mb_ids,
            "all_ids": bnn_ids + bnn_mb_ids,
        })

    return results


def find_branch_device_ids(branch_name: str, sheet_data: list[dict]) -> list[str]:
    """
    จับคู่ branch_name กับ Mall ใน sheet แล้วคืน Device IDs.

    Normalization:
    - ลบเครื่องหมายขีด, ช่องว่าง, พิมพ์เล็ก
    - แปลง 'Bigc' → 'bigc', 'PaIn' → 'pain', 'BnFranchise' → 'bn'
    - ใช้ทั้ง word-overlap และ substring matching
    """
    stopwords = {"bn", "bnn", "bnm", "banana", "franchise", "store", "co", "ltd",
                 "the", "of", "bcfr", "bigc franchise", "fcb", "bcf"}

    def norm(s: str) -> str:
        """Normalize to lowercase alphanumeric string."""
        s = s.lower()
        # แปลงชื่อย่อที่พบบ่อย
        s = s.replace("bigc", "bigc")
        s = re.sub(r"[^a-z0-9\u0e00-\u0e7f]", "", s)
        return s

    def tokenize(s: str) -> list[str]:
        """แยกคำจาก branch name."""
        s = s.lower()
        tokens = re.findall(r"[a-z0-9]+|[\u0e00-\u0e7f]+", s)
        return [t for t in tokens if t not in stopwords and len(t) >= 2]

    branch_norm = norm(branch_name)
    branch_tokens = set(tokenize(branch_name))

    # หา Mall ที่ชื่อตรงที่สุดก่อน — ไม่กรองด้วย status/all_ids ตรงนี้ เพราะการกรองก่อน
    # จับคู่ชื่อ จะทำให้สาขาที่ status="ยังไม่มีเครื่อง" ถูกข้ามไปเจอ Mall อื่นที่ชื่อ
    # คล้ายกันแต่ไม่ใช่สาขาเดียวกันแทน (คะแนนต่ำกว่ามาก) ยืนยันจากเคสจริง:
    # สาขา "BN-Lotus Chalong R.2" ควรจับคู่กับ Mall "Lotus Chalong" (คะแนนสูงสุดจริง)
    # แต่ status ของ Mall นั้น = "ยังไม่มีเครื่อง" — ถ้ากรองก่อนจับคู่ จะได้ Mall "Lotus
    # Cha Am" ซึ่งเป็นคนละสาขากันมาแทน ทั้งที่ควรตอบว่า "ไม่มีเครื่อง" ไปเลย
    best_entry = None
    best_score = 0.0

    for entry in sheet_data:
        mall_norm = norm(entry["mall"])
        mall_tokens = set(tokenize(entry["mall"]))

        score = 0.0

        # 1. Word overlap score
        overlap = branch_tokens & mall_tokens
        if overlap:
            score += len(overlap) * 2.0

        # 2. Substring match (normalized)
        if len(mall_norm) >= 4 and mall_norm in branch_norm:
            score += 3.0
        if len(branch_norm) >= 4 and branch_norm in mall_norm:
            score += 2.0

        # 3. Partial token substring
        for bt in branch_tokens:
            if len(bt) >= 4:
                for mt in mall_tokens:
                    if bt in mt or mt in bt:
                        score += 0.5

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry is None or best_score < 2.0:
        return []

    # เจอสาขาที่ตรงที่สุดแล้ว — คืน device ids ก็ต่อเมื่อ status ขึ้นต้นด้วย "มีเครื่อง"
    # จริงๆ เท่านั้น (สาขาที่ status = "ยังไม่มีเครื่อง"/"เครื่องพัง" ฯลฯ อาจมีตัวเลข
    # ค้างอยู่ในคอลัมน์ BNN แต่ไม่ใช่ device ID ที่ใช้งานจริง) — ไม่ fallback ไปสาขาอื่น
    if best_entry["status"].strip().startswith("มีเครื่อง") and best_entry["all_ids"]:
        return best_entry["all_ids"]

    return []

