# ThaiFurnitureAnalyzer Training Studio

หน้า [วิเคราะห์แบบ](https://mootre.github.io/AIDesign/analysis.html) ใช้หลักอ่านแบบจาก ThaiFurnitureAnalyzer และสูตรประมาณวัสดุจาก `estimator.py` ผู้ใช้เปิด PDF เพื่อเทียบหลักฐาน กรอกชิ้นส่วนหรือนำเข้า JSON จากโปรแกรม Python แล้วดูพื้นที่ ขอบ และจำนวนแผ่นโดยประมาณได้ ผลคำนวณตามพื้นที่ใช้ประกอบการตรวจและยังต้องจัด Nesting ก่อนสั่งผลิต

หน้า GitHub Pages ใน `docs/` ใช้แบบตู้สังเคราะห์เพื่อสาธิตการแก้คำตอบ ลากกรอบหลักฐาน ส่งตรวจ อนุมัติ และส่งออก JSONL พร้อมภาพอ้างอิง

เปิด `docs/index.html` ในเบราว์เซอร์เพื่อทดลอง หรือเผยแพร่ผ่าน **Settings → Pages → Deploy from a branch → main → /docs** หน้าเว็บใช้พาธสัมพัทธ์จึงทำงานภายใต้ชื่อ repository ได้

Codex CLI, ฐานข้อมูล SQLite และการสำรอง Google Drive ใช้งานผ่าน Training Studio ฉบับ Python ในเครื่อง หน้า Pages ประมวลผลเฉพาะข้อมูลสังเคราะห์และแบบฟอร์มในแท็บนั้น

## โปรแกรม Training Studio บน Windows

เปิด [คู่มือ Training Studio](app/README-TRAINING.md) จากนั้นรัน `app/install.bat` และ `app/start.bat` โปรแกรมดาวน์โหลดโมเดล OCR ตอนติดตั้ง คลังงานจริงจัดเก็บในเครื่องและสำรองไป Google Drive ตามคู่มือ

บน GitHub Free ต้องใช้ repository แบบ Public จึงจะเปิด Pages ได้ สำหรับ repo Private ให้ตรวจสิทธิ์ Pages ของบัญชีใน Settings เว็บที่เผยแพร่ประกอบด้วยไฟล์ใน `docs/` เท่านั้น
