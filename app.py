import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database
import scheduler
import paylocity_bot
import voice_caller
import line_service
import gemini_service
import calendar_export

logger = logging.getLogger("app")

# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    database.init_db()
    scheduler.start_scheduler()
    yield
    # Shutdown
    if scheduler.scheduler.running:
        scheduler.scheduler.shutdown()

app = FastAPI(title="Paylocity Auto Clock", lifespan=lifespan)

BASE_DIR = os.path.dirname(__file__)
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(SCREENSHOT_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/screenshots", StaticFiles(directory=SCREENSHOT_DIR), name="screenshots")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Pydantic Schemas
class ScheduleIn(BaseModel):
    title: str
    action: str
    schedule_type: str
    target_time: str
    target_date: Optional[str] = None
    days_of_week: Optional[str] = None

class ToggleIn(BaseModel):
    is_enabled: int

class PunchIn(BaseModel):
    action: str

class SendChatMsgIn(BaseModel):
    text: str

class GeminiTestIn(BaseModel):
    api_keys: str
    model: Optional[str] = None

# Web Routes
@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# API Routes: Schedules
@app.get("/api/schedules")
async def api_get_schedules():
    return database.get_schedules()

@app.post("/api/schedules")
async def api_create_schedule(sch: ScheduleIn):
    sch_id = database.add_schedule(
        title=sch.title,
        action=sch.action,
        schedule_type=sch.schedule_type,
        target_time=sch.target_time,
        target_date=sch.target_date,
        days_of_week=sch.days_of_week
    )
    scheduler.reload_schedules()
    return {"id": sch_id, "message": "เพิ่มตารางเวลาสำเร็จ"}

@app.put("/api/schedules/{schedule_id}")
async def api_update_schedule(schedule_id: int, sch: ScheduleIn):
    database.update_schedule(
        schedule_id=schedule_id,
        title=sch.title,
        action=sch.action,
        schedule_type=sch.schedule_type,
        target_time=sch.target_time,
        target_date=sch.target_date,
        days_of_week=sch.days_of_week
    )
    scheduler.reload_schedules()
    return {"message": "แก้ไขตารางเวลาสำเร็จ"}

@app.post("/api/schedules/{schedule_id}/toggle")
async def api_toggle_schedule(schedule_id: int, toggle: ToggleIn):
    database.toggle_schedule(schedule_id, toggle.is_enabled)
    scheduler.reload_schedules()
    return {"message": "เปลี่ยนสถานะสำเร็จ"}

@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int):
    database.delete_schedule(schedule_id)
    scheduler.reload_schedules()
    return {"message": "ลบตารางเวลาสำเร็จ"}

# API Routes: History
@app.get("/api/history")
async def api_get_history():
    return database.get_histories(limit=30)

# API Routes: Settings
@app.get("/api/settings")
async def api_get_settings():
    return database.get_all_settings()

@app.post("/api/settings")
async def api_save_settings(settings: Dict[str, str]):
    database.update_settings(settings)
    scheduler.reload_schedules()
    return {"message": "บันทึกการตั้งค่าสำเร็จ"}

# API Routes: Punch Trigger
@app.post("/api/punch")
async def api_trigger_punch(punch: PunchIn, background_tasks: BackgroundTasks):
    action = punch.action
    action_th = "เข้างาน (Clock In)" if action == "clock_in" else "ออกงาน (Clock Out)"
    history_id = database.add_history(action, "running", f"เริ่มกระบวนการลงเวลา {action_th}...")
    
    # Run in background so HTTP response returns immediately
    background_tasks.add_task(paylocity_bot.run_punch, action, history_id)
    return {"message": f"เริ่มกระบวนการลงเวลา {action_th} แล้ว ระบบจะโทรเข้ามือถือและส่งผลเข้า LINE"}

# API Routes: Test Voice Call
@app.post("/api/test-call")
async def api_test_call():
    result = voice_caller.make_voice_call(action_text="เข้างาน (ทดสอบระบบ)")
    return result

# API Routes: Test LINE Message
@app.post("/api/test-line")
async def api_test_line():
    result = line_service.send_line_message("🔔 ทดสอบการแจ้งเตือนจาก Paylocity Auto Clock! ระบบเชื่อมต่อ LINE สำเร็จเรียบร้อยครับ 🎉")
    return result

# Helper: Background Task to Sync LINE Chat Profile (User, Group, Room)
def sync_chat_profile(source_type: str, source_id: str, sender_id: Optional[str] = None):
    try:
        if source_type == "user":
            res = line_service.get_user_profile(source_id)
            if res.get("success"):
                prof = res.get("profile", {})
                database.update_chat_profile(
                    source_id=source_id,
                    display_name=prof.get("displayName", source_id),
                    picture_url=prof.get("pictureUrl"),
                    status_message=prof.get("statusMessage")
                )
        elif source_type == "group":
            res = line_service.get_group_summary(source_id)
            if res.get("success"):
                summ = res.get("summary", {})
                database.update_chat_profile(
                    source_id=source_id,
                    display_name=summ.get("groupName", f"กลุ่ม {source_id[:6]}"),
                    picture_url=summ.get("pictureUrl")
                )
        elif source_type == "room":
            # สำหรับห้องแชทหลายคน (Multi-person Chat)
            if sender_id:
                res = line_service.get_room_member_profile(source_id, sender_id)
                if res.get("success"):
                    prof = res.get("profile", {})
                    database.update_chat_profile(
                        source_id=source_id,
                        display_name=f"ห้องแชท ({prof.get('displayName', sender_id)})",
                        picture_url=prof.get("pictureUrl")
                    )
                    return
            database.update_chat_profile(
                source_id=source_id,
                display_name=f"ห้องแชทหลายคน ({source_id[-4:]})",
                picture_url=None
            )
    except Exception as e:
        logger.warning(f"Error sync_chat_profile: {e}")

# Helper: Background Task to Generate and Send AI Auto-Reply
def handle_ai_auto_reply(source_id: str, incoming_text: str, reply_token: Optional[str] = None):
    """
    ประมวลผลข้อความด้วย Gemini AI และส่งคำตอบกลับผ่าน Reply API (Fallback เป็น Push)
    """
    try:
        # ตรวจสอบการเปิดใช้งาน AI
        if database.get_setting("ai_reply_enabled", "1") != "1":
            return

        api_keys = database.get_setting("gemini_api_keys", "").strip()
        if not api_keys:
            logger.info("Gemini API key is not configured, skipping AI auto-reply.")
            return

        # ดึงประวัติการสนทนาย้อนหลัง
        history_records = database.get_line_messages(source_id, limit=6)
        # เอาข้อความก่อนหน้ามาทำบริบท (ยกเว้นข้อความล่าสุดที่เพิ่งเข้ามา)
        context_history = history_records[:-1] if len(history_records) > 1 else []

        logger.info(f"Generating Gemini AI reply for {source_id}...")
        ai_result = gemini_service.generate_ai_reply(
            prompt=incoming_text,
            history=context_history
        )

        if ai_result.get("success"):
            reply_text = ai_result.get("reply_text", "").strip()
            if reply_text:
                # ส่งข้อความผ่าน Reply API (หรือ Fallback เป็น Push API)
                dispatch_res = line_service.send_reply_or_push(
                    to_id=source_id,
                    text=reply_text,
                    reply_token=reply_token
                )

                # บันทึกคำตอบของ AI ลงฐานข้อมูล
                database.add_line_message(
                    chat_id=source_id,
                    sender_type="bot",
                    sender_id="gemini_ai",
                    message_type="text",
                    content=reply_text
                )

                # เคลียร์ reply_token หลังจากใช้งานแล้ว
                database.clear_chat_reply_token(source_id)
                logger.info(f"AI reply dispatched to {source_id} via {dispatch_res.get('method')}")
        else:
            logger.warning(f"Gemini reply generation failed: {ai_result.get('message')}")
    except Exception as e:
        logger.error(f"Error in handle_ai_auto_reply: {e}")

# API Routes: LINE Webhook (Auto-capture, Chat Logging, AI Auto-Reply & Webhook Reports)
@app.post("/api/line/webhook")
async def api_line_webhook(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    # 1. ตรวจสอบความถูกต้องของ signature
    is_valid = line_service.verify_signature(body_bytes, signature)
    if not is_valid:
        logger.warning("Invalid LINE webhook signature received.")
        database.add_webhook_log(
            event_types="invalid_signature",
            source_id="unknown",
            payload=body_bytes.decode("utf-8", errors="ignore"),
            status="error"
        )
        raise HTTPException(status_code=400, detail="Invalid signature")

    raw_text = body_bytes.decode("utf-8", errors="ignore")
    try:
        data = json.loads(raw_text) if raw_text else {}
    except Exception:
        data = {}

    events = data.get("events", [])
    event_types_list = []
    primary_source_id = None

    # Handle Webhook Verification ping from LINE Developers console
    if not events:
        database.add_webhook_log(
            event_types="verify_ping",
            source_id=data.get("destination", "verify"),
            payload=raw_text,
            status="verified"
        )
        return {"status": "ok"}

    for event in events:
        ev_type = event.get("type", "unknown")
        source = event.get("source", {})
        source_type = source.get("type", "user")  # 'user', 'group', 'room'
        source_id = source.get("userId") if source_type == "user" else (source.get("groupId") or source.get("roomId") or source.get("userId"))
        sender_id = source.get("userId")
        reply_token = event.get("replyToken")
        timestamp_ms = event.get("timestamp")
        timestamp_iso = datetime.fromtimestamp(timestamp_ms / 1000.0).isoformat() if timestamp_ms else datetime.now().isoformat()

        if source_id:
            primary_source_id = source_id

        if ev_type == "message":
            msg_obj = event.get("message", {})
            msg_type = msg_obj.get("type", "text")
            event_types_list.append(f"message:{msg_type}")

            content = ""
            if msg_type == "text":
                content = msg_obj.get("text", "")
            elif msg_type == "sticker":
                content = f"🏷️ [สติกเกอร์]"
            elif msg_type == "image":
                content = "📷 [รูปภาพ]"
            elif msg_type == "video":
                content = "🎥 [วิดีโอ]"
            elif msg_type == "audio":
                content = "🎵 [ข้อความเสียง]"
            elif msg_type == "location":
                content = f"📍 [{msg_obj.get('title', 'ตำแหน่งที่ตั้ง')}]"
            else:
                content = f"[{msg_type}]"

            if source_id:
                # 1. บันทึก / อัปเดตห้องแชท (พร้อม reply_token ล่าสุด)
                database.upsert_line_chat(source_type=source_type, source_id=source_id, reply_token=reply_token)

                # 2. บันทึกข้อความเข้า line_messages
                database.add_line_message(
                    chat_id=source_id,
                    sender_type="user",
                    sender_id=sender_id,
                    message_type=msg_type,
                    content=content,
                    raw_data=json.dumps(event, ensure_ascii=False),
                    reply_token=reply_token,
                    timestamp=timestamp_iso
                )

                # 3. ดึงชื่อโปรไฟล์และรูปภาพหากยังไม่มี
                chat_info = database.get_line_chat(source_id)
                if not chat_info or not chat_info.get("display_name"):
                    background_tasks.add_task(sync_chat_profile, source_type, source_id, sender_id)

                # 4. บันทึกเป็นผู้รับแจ้งเตือนหลักถ้าเป็นผู้ใช้เดี่ยวและยังไม่มี
                current_user_id = database.get_setting("line_user_id", "").strip()
                if not current_user_id and source_type == "user":
                    database.update_settings({"line_user_id": source_id})

                # 5. ประมวลผลการตอบกลับอัตโนมัติด้วย AI หรือ Greeting
                if msg_type == "text" and content.strip():
                    ai_enabled = database.get_setting("ai_reply_enabled", "1") == "1"
                    gemini_keys = database.get_setting("gemini_api_keys", "").strip()

                    if ai_enabled and gemini_keys:
                        # สั่งรัน AI Auto-reply เบื้องหลัง
                        background_tasks.add_task(handle_ai_auto_reply, source_id, content, reply_token)
                    elif reply_token and reply_token not in ("00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"):
                        t_lower = content.strip().lower()
                        if t_lower in ["สวัสดี", "hello", "hi", "หวัดดี", "test", "ทดสอบ"]:
                            reply_msg = (
                                "🎉 เชื่อมต่อระบบสำเร็จแล้วครับ!\n"
                                "ระบบบันทึกข้อความของคุณเรียบร้อยแล้ว\n"
                                "แอดมินสามารถดูประวัติและตอบแชทกลับผ่านหน้าเว็บได้แบบเรียลไทม์ครับ 😊"
                            )
                            line_service.send_reply_or_push(source_id, reply_msg, reply_token)
                            database.add_line_message(
                                chat_id=source_id,
                                sender_type="bot",
                                sender_id="bot",
                                message_type="text",
                                content=reply_msg
                            )
                            database.clear_chat_reply_token(source_id)

        elif ev_type in ["follow", "join"]:
            event_types_list.append(ev_type)
            if source_id:
                database.upsert_line_chat(source_type=source_type, source_id=source_id, reply_token=reply_token)
                background_tasks.add_task(sync_chat_profile, source_type, source_id, sender_id)
                if reply_token and reply_token not in ("00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"):
                    welcome_msg = "ยินดีต้อนรับครับ! ขอบคุณที่เพิ่มเพื่อนกับเรา 😊"
                    line_service.send_reply_or_push(source_id, welcome_msg, reply_token)
                    database.add_line_message(
                        chat_id=source_id,
                        sender_type="bot",
                        sender_id="bot",
                        message_type="text",
                        content=welcome_msg
                    )
                    database.clear_chat_reply_token(source_id)
        else:
            event_types_list.append(ev_type)

    # บันทึก Raw Webhook Log ลงฐานข้อมูล
    summary_types = ", ".join(event_types_list) if event_types_list else "event"
    database.add_webhook_log(
        event_types=summary_types,
        source_id=primary_source_id,
        payload=raw_text,
        status="success"
    )

    return {"status": "ok"}

# API Routes: LINE Bot Info & Connection Test
@app.post("/api/line/test-connection")
async def api_line_test_connection():
    return line_service.get_bot_info()

# API Routes: Gemini AI Models & Connection Test
@app.post("/api/gemini/test")
async def api_test_gemini(body: GeminiTestIn):
    return gemini_service.test_gemini_connection(api_keys=body.api_keys, model=body.model)

@app.get("/api/gemini/models")
async def api_get_gemini_models():
    return gemini_service.list_available_models()

# API Routes: LINE Chats & Messages
@app.get("/api/line/chats")
async def api_get_line_chats():
    return database.get_line_chats()

@app.get("/api/line/chats/{source_id}/messages")
async def api_get_line_chat_messages(source_id: str):
    chat = database.get_line_chat(source_id)
    messages = database.get_line_messages(source_id, limit=100)
    return {"chat": chat, "messages": messages}

@app.post("/api/line/chats/{source_id}/messages")
async def api_send_line_chat_message(source_id: str, body: SendChatMsgIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="ข้อความต้องไม่ว่างเปล่า")

    # ตรวจสอบว่าห้องแชทมี replyToken ที่ยังใช้งานได้อยู่หรือไม่
    chat = database.get_line_chat(source_id)
    reply_token = chat.get("latest_reply_token") if chat else None

    # ส่งข้อความผ่าน Reply API ก่อน ถ้าไม่สำเร็จให้ Fallback เป็น Push API ทันที
    res = line_service.send_reply_or_push(to_id=source_id, text=text, reply_token=reply_token)
    if res.get("success"):
        # บันทึกลง database
        msg_id = database.add_line_message(
            chat_id=source_id,
            sender_type="admin",
            sender_id="admin",
            message_type="text",
            content=text
        )
        # เคลียร์ reply_token หลังจากใช้งานแล้ว
        database.clear_chat_reply_token(source_id)
        return {
            "success": True,
            "message_id": msg_id,
            "method": res.get("method"),
            "message": f"ส่งข้อความสำเร็จ ({res.get('method').upper()})"
        }
    else:
        return {"success": False, "message": res.get("message", "ส่งข้อความไม่สำเร็จ")}

@app.post("/api/line/chats/{source_id}/refresh-profile")
async def api_refresh_chat_profile(source_id: str):
    chat = database.get_line_chat(source_id)
    source_type = chat.get("source_type", "user") if chat else "user"
    sync_chat_profile(source_type, source_id)
    updated_chat = database.get_line_chat(source_id)
    return {"success": True, "chat": updated_chat}

# API Routes: Webhook Logs & JSON Report
@app.get("/api/line/webhook-logs")
async def api_get_webhook_logs(page: int = 1, limit: int = 10):
    return database.get_webhook_logs(page=page, limit=limit)

@app.get("/api/line/webhook-logs/{log_id}")
async def api_get_webhook_log_detail(log_id: int):
    log = database.get_webhook_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log

# API Routes: Export Google Calendar .ics
@app.get("/api/calendar/export")
async def api_export_calendar():
    ics_text = calendar_export.generate_ics_content()
    return Response(
        content=ics_text,
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=paylocity_schedule.ics"}
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 7860))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
