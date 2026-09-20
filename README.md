# 🕒 Paylocity Auto Clock System

ระบบช่วยลงเวลาเข้า-ออกงานอัตโนมัติบน Paylocity พร้อม **หน้าเว็บหลังบ้าน (Web Dashboard)** สำหรับตั้งเวลาล่วงหน้า, **ระบบโทรเข้ามือถือจริงเมื่อถึงเวลากด Duo 2FA**, และ **แจ้งเตือนพร้อมส่งรูปภาพผลลัพธ์ผ่าน LINE** 
ออกแบบมาให้รันบน Cloud ได้ตลอด 24 ชั่วโมง โดยที่คุณ **ไม่ต้องเปิดคอมพิวเตอร์ทิ้งไว้** เลยแม้แต่นาทีเดียว!

---

## 🌟 ฟีเจอร์เด่นของระบบ

1. **🌐 Web Dashboard (หน้าเว็บหลังบ้าน)**
   - เปิดเข้าใช้งานผ่านโทรศัพท์มือถือหรือคอมพิวเตอร์ได้ตลอด 24 ชั่วโมง
   - กำหนดเวลาเข้างาน (Clock In) และเลิกงาน (Clock Out) แต่ละวันได้อย่างอิสระ (ตั้งแบบรายวัน หรือประจำสัปดาห์)
   - มีปุ่มสั่ง "Clock In ทันที" / "Clock Out ทันที" ได้ทุกเมื่อ
   - ดูประวัติย้อนหลัง พร้อมคลิกดูภาพถ่ายหน้าจอ (Screenshot) หลักฐานที่บอทกดลงเวลาให้
   - มีปุ่มดาวน์โหลดปฏิทิน `.ics` นำเข้า Google Calendar เพื่อให้มือถือสั่นเตือนล่วงหน้า

2. **📞 บอทโทรเข้าเบอร์มือถืออัตโนมัติ (Twilio Voice Call)**
   - เมื่อถึงเวลาที่ตั้งไว้ บอทจะเปิดเว็บ Paylocity และสั่ง **"โทรเข้าเบอร์มือถือของคุณจริงๆ ทันที"**
   - มือถือของคุณจะมีสายเรียกเข้าและเสียงริงโทนดังขึ้น
   - เมื่อรับสาย จะมีเสียงสังเคราะห์ภาษาไทยพูดเตือนว่า: *"สวัสดีครับ ถึงเวลาลงเวลา Paylocity แล้ว กรุณาเปิดแอป Duo เพื่อกดยืนยันตัวตนครับ"*
   - ช่วยแก้ปัญหาการพลาดการแจ้งเตือนจาก Duo ได้อย่างเด็ดขาด

3. **💬 แจ้งเตือนและส่งรูปภาพผ่าน LINE (LINE Messaging API)**
   - ส่งข้อความเตือนเมื่อเริ่มรัน
   - ส่งรูปภาพหน้าจอ (Screenshot) บันทึกหลักฐานผลการลงเวลาเข้าแชท LINE ทันที

4. **☁️ Cloud Ready (ไม่ต้องเปิดคอม)**
   - รันผ่าน Docker Container สามารถนำไปโฮสต์บน Cloud Server ฟรี (เช่น Render.com, Fly.io, Railway หรือ VPS)

---

## 🚀 วิธีเปิดใช้งานและทดสอบบนเครื่อง Mac ของคุณ

### 1. ติดตั้ง Dependencies
เปิด Terminal แล้วเข้าไปที่โฟลเดอร์โปรเจกต์:
```bash
cd /Users/supanatmarach/.gemini/antigravity/scratch/paylocity-auto-clock

# สร้าง virtualenv (แนะนำ)
python3 -m venv venv
source venv/bin/activate

# ติดตั้งแพ็กเกจและเบราว์เซอร์ Playwright
pip install -r requirements.txt
playwright install chromium
```

### 2. เริ่มต้นรัน Web Dashboard
```bash
python3 app.py
```
เปิดเบราว์เซอร์แล้วไปที่: **`http://localhost:8000`** คุณจะพบกับหน้าเว็บหลังบ้านทันที!

---

## ⚙️ วิธีตั้งค่าระบบในหน้าเว็บ (Settings)

คลิกที่ปุ่ม **"ตั้งค่าระบบ"** (มุมขวาบนของหน้าเว็บ):

### 1. ข้อมูล Paylocity
- **Paylocity Login URL**: ใส่ URL OIDC ที่ระบุไว้
- **Company ID**: รหัสบริษัท (ถ้ามี)
- **Username / Email**: ชื่อผู้ใช้หรืออีเมลที่ใช้ล็อกอิน
- **Password**: รหัสผ่านล็อกอิน

### 2. ข้อมูลระบบโทรเข้ามือถือ (Twilio Voice API)
*Twilio มีเครดิตฟรีให้ทดลองใช้ประมาณ $15 (โทรได้หลายร้อยครั้ง)*
1. สมัครบัญชีฟรีที่ [twilio.com](https://www.twilio.com/)
2. ในหน้า Dashboard คุณจะได้รับ:
   - **Account SID**
   - **Auth Token**
   - **Twilio Phone Number** (กดขอเบอร์ฟรี 1 เบอร์)
3. ในเมนู Phone Numbers > Verified Caller IDs: ใส่เบอร์มือถือของคุณเพื่อยืนยัน
4. นำค่าทั้ง 4 อย่างมากรอกในหน้า Settings และเปิดสวิตช์ใช้งาน

### 3. ข้อมูล LINE Messaging API
1. ไปที่ [LINE Developers Console](https://developers.line.biz/) แล้วล็อกอินด้วยบัญชี LINE
2. สร้าง Provider และเลือกสร้าง **Messaging API channel** (ทำหน้าที่เป็น LINE Official Account ส่งข้อความหาคุณฟรี)
3. ในแท็บ **Messaging API**:
   - กด Issue เพื่อรับ **Channel access token (long-lived)**
   - เลื่อนลงมาสแกน QR Code เพื่อเพิ่มบอทเป็นเพื่อนใน LINE
4. ในแท็บ **Basic settings**: คัดลอก **Your user ID**
5. นำค่า Token และ User ID มากรอกในหน้า Settings และเปิดสวิตช์ใช้งาน

---

## ☁️ วิธีนำขึ้น Cloud ฟรี (เพื่อให้ทำงาน 24 ชม. โดยไม่ต้องเปิดคอม)

### ทางเลือกที่ 1: Deploy ผ่าน Render.com (ฟรี & สะดวกมาก)
1. นำโฟลเดอร์นี้อัปขึ้น GitHub (ตั้งเป็น **Private Repository** เพื่อความปลอดภัย)
2. ไปที่ [Render.com](https://render.com/) แล้วเลือก **New Web Service**
3. เชื่อมต่อกับ GitHub Repo ของคุณ
4. Render จะตรวจพบ `Dockerfile` ให้อัตโนมัติ:
   - เลือก Environment เป็น **Docker**
   - ตั้ง Instance Type เป็น Free
5. กด Deploy! เมื่อเสร็จแล้ว คุณจะได้ URL เว็บหลังบ้าน เช่น `https://paylocity-clock.onrender.com` สามารถเปิดจากมือถือได้ทุกที่ ทุกเวลา

### ทางเลือกที่ 2: Deploy บน VPS หรือ Home Server (Docker Compose)
หากมี VPS (เช่น Oracle Cloud Free Tier, DigitalOcean, Ubuntu Server):
```bash
git clone <your-repo>
cd paylocity-auto-clock
docker compose up -d
```
ระบบจะรัน background ตลอด 24 ชั่วโมง

---

## 📅 การนำตารางไปใส่ใน Google Calendar บนมือถือ

1. ในหน้าเว็บ Dashboard เมื่อคุณตั้งเวลาเสร็จแล้ว ให้กดปุ่ม **"Google Calendar"** หรือ **"ดาวน์โหลด .ics"**
2. นำไฟล์ `.ics` ไปเปิดใน Google Calendar:
   - ไปที่ [calendar.google.com](https://calendar.google.com/) > Settings > Import & Export > เลือกไฟล์แล้วกด Import
3. ปฏิทินจะสร้างนัดหมายเวลาเข้างาน-ออกงาน พร้อมตั้งปลุกและสั่นเตือนบนมือถือคุณล่วงหน้า 2 นาทีทันที!
