# QA Automation: ตรวจเคส Submit MDM อัตโนมัติ

## เรื่องมันเริ่มจากอะไร

ทุกครั้งที่ลูกค้าผ่อนมือถือรับเครื่องที่สาขา สาขาจะส่งเคสเข้ามาขอ "Submit MDM"
(ลงทะเบียนเครื่องเข้าระบบจัดการอุปกรณ์) ก่อนจะกดยืนยัน เจ้าหน้าที่ต้องเปิดดู **5 ระบบ**
เพื่อเช็กว่าข้อมูลตรงกันทุกที่:

- **7Chat / CRM**: เคสที่สาขาส่งมา
- **iLoan (ITOS)**: ใบคำขอสินเชื่อ รุ่น ความจุ สี ราคา เงินดาวน์
- **ITEC**: บิลขายจริง (โปรแกรม desktop)
- **MDM**: ระบบจัดการเครื่อง ว่าเครื่องลงทะเบียนครบหรือยัง

เคสหนึ่งต้องเทียบชื่อลูกค้า เลขใบคำขอ สาขา ราคา รุ่นเครื่อง และ Serial/IMEI ข้ามหลายระบบ
ทำมือทีละเคสทั้งช้าและพลาดง่าย

## ระบบนี้ทำอะไร

```
7Chat รอเคสใหม่ ─► อ่านข้อมูลเคสจาก CRM ─► ค้น iLoan ─► ค้น ITEC ─► เช็ก MDM
                                                                    │
                ┌───────────────────────────────────────────────────┘
                ▼
     ตรวจทุกช่องข้ามทุกระบบ (rules.py)
                │
     ┌──────────┴──────────┐
  ตรงครบทุกจุด          มีจุดไม่ตรง
     │                     │
  เขียนผลลงชีท        เขียนลงชีท "No Submit"
  (ถ้าเปิด --auto-reply:   พร้อมบอกว่าไม่ตรงตรงไหน
   ลงทะเบียน ITOS +
   ตอบลูกค้า + ปิดเคส)
```

- รอเคสใหม่ใน 7Chat เอง แล้วตามลิงก์ไปเปิดเคสใน CRM
- ค้นข้อมูลในแต่ละระบบด้วย Playwright (เว็บ) และ pywinauto (โปรแกรม ITEC บน Windows)
- `rules.py` ตรวจแบบเข้ม: ทุกช่องต้อง **มีข้อมูลและตรงกันจริง** ในทุกระบบ ถ้าระบบไหนไม่มีข้อมูล
  ก็ถือว่าไม่ผ่าน และเขียนเหตุผลเป็นข้อๆ ให้คนอ่านต่อได้ทันที
- เขียนผลลง Google Sheets ผ่าน API (service account) แยกเป็นแท็บ iOS / Android / ไม่ผ่าน

## จุดที่ภูมิใจ

- **ปลอดภัยไว้ก่อน** ส่วนที่แก้ข้อมูลจริง (ลงทะเบียนสินค้า ตอบลูกค้า ปิดเคส) **ปิดไว้เป็นค่าเริ่มต้น**
  ต้องสั่ง `--auto-reply` เองถึงจะทำ และจะตอบลูกค้าก็ต่อเมื่อลงทะเบียนสำเร็จจริงเท่านั้น
- **รับมือข้อมูลไม่สะอาด** เช่น ช่อง Serial ที่มี IMEI หรือ URL หลุดมา, Serial ของ Apple ที่มีตัว S
  เกินมาข้างหน้า, รูปแบบรหัสสาขาที่แต่ละระบบเขียนไม่เหมือนกัน และชื่อสาขาที่ต้องจับคู่แบบ fuzzy
- **มีทางสำรองเมื่อหาไม่เจอ** ถ้าหาเครื่องใน MDM ด้วย Serial ไม่เจอ ระบบจะลองหาจากเครื่องของสาขา
  ถ้ายังไม่เจออีก ก็เช็กกับบันทึกคำขออนุมัติพิเศษ (CMC Summary) แทน และถ้าข้อมูลในตารางไม่มี
  ก็อ่าน Serial/IMEI จากไฟล์สัญญา PDF ด้วย OCR (Tesseract)
- **เลิกพึ่งหน้าจอ Google Sheets** เวอร์ชันแรกเปิดเบราว์เซอร์แล้ว paste ลงชีท
  เปลี่ยนมาใช้ Sheets API ทำให้เร็วขึ้น รันแบบ headless ได้ และไม่พังเวลา UI เปลี่ยน

## เทคโนโลยีที่ใช้

Python · Playwright · pywinauto · gspread (Google Sheets API) · PyMuPDF + Tesseract OCR · openpyxl

## โครงสร้างโปรเจกต์

```
main.py                 ควบคุม flow ทั้งหมด
qa_automation/
  sevenchat.py          รอเคสใหม่ใน 7Chat (อ่านอย่างเดียว)
  crm.py                อ่านข้อมูลเคสจาก CRM
  iloan.py              ค้นใบคำขอ / ลงทะเบียนสินค้าใน iLoan (ITOS)
  itec_desktop.py       ค้นบิลในโปรแกรม ITEC (desktop)
  mdm.py, mdm_sheet.py  เช็กเครื่องใน MDM + ทางสำรองจากเครื่องของสาขา
  cmc_summary.py        ทางสำรองจากบันทึกคำขออนุมัติพิเศษ
  rules.py              กฎตรวจความถูกต้องข้ามทุกระบบ
  sheet_writer.py       เขียนผลลง Google Sheets + ไฟล์ xlsx ในเครื่อง
  models.py, config.py  โครงสร้างข้อมูล / อ่านค่าจาก .env
```

## วิธีรัน (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\playwright.exe install chromium
copy .env.example .env      # กรอก URL ของแต่ละระบบ, user/password, Sheet ID

# ตรวจเคสที่ระบุเลข
.\.venv\Scripts\python.exe main.py --case-id <เลขเคส> --headed

# รอเคสใหม่จาก 7Chat แล้วตรวจต่อกัน 10 เคส
.\.venv\Scripts\python.exe main.py --wait-7chat --limit 10 --headed
```

ถ้ายังไม่ได้ตั้งค่า Google Sheets ระบบจะเขียนผลลง `output/qa_report.xlsx` ในเครื่องแทน

<details>
<summary>ตั้งค่า Google Sheets API (ทำครั้งเดียว)</summary>

1. ใน [Google Cloud Console](https://console.cloud.google.com/) เปิดใช้ **Google Sheets API**
2. สร้าง **Service Account** แล้วสร้าง key แบบ JSON
3. เก็บไฟล์ไว้ที่ `credentials/service_account.json` (อยู่ใน `.gitignore` แล้ว) แล้วตั้งค่าใน `.env`:
   `GOOGLE_SERVICE_ACCOUNT_FILE=./credentials/service_account.json`
4. แชร์ชีทปลายทางให้ email ของ service account (`xxx@your-project.iam.gserviceaccount.com`) เป็นผู้แก้ไข

</details>

## หมายเหตุ

โปรเจกต์นี้ทำขึ้นระหว่างทำงานจริง โค้ดที่เผยแพร่ตรงนี้เอา URL ระบบภายใน, Sheet ID, รหัสผ่าน
และข้อมูลลูกค้าออกหมดแล้ว ส่วนที่เป็นความลับของระบบต้นทาง (เช่น รหัสผ่านไฟล์สัญญา)
ไม่ได้ใส่ไว้ในเวอร์ชันนี้
