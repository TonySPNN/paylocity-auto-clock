import os
import asyncio
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
import calendar_export

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
