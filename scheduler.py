import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import pytz
from datetime import datetime, timedelta

from typing import Optional
from database import get_schedules, get_setting
import paylocity_bot

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()

async def execute_scheduled_job(schedule_id: int, action: str, target_time: Optional[str] = None):
    logger.info(f"Triggering scheduled job #{schedule_id} for action: {action} (Target time: {target_time})")
    await paylocity_bot.run_punch(action=action, target_time=target_time)

def reload_schedules():
    """
    โหลดตารางงานทั้งหมดจากฐานข้อมูลเข้ามาใน APScheduler
    โดยจะตั้งเวลา Trigger ล่วงหน้า 3-5 นาที (ค่าเริ่มต้น 4 นาที)
    เพื่อให้บอทเปิดเว็บ, ผ่าน SSO, รอ Duo Approve และเข้าสู่โหมด Standby พร้อมกดในเวลาที่กำหนด
    """
    scheduler.remove_all_jobs()
    schedules = get_schedules()
    tz_str = get_setting("timezone", "Asia/Bangkok")
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.timezone("Asia/Bangkok")

    # Configurable Lead Time (1 to 15 minutes, default 5)
    lead_minutes = 5
    try:
        lead_val_str = get_setting("lead_minutes", get_setting("pre_login_lead_minutes", "5")).strip()
        lead_val = int(lead_val_str)
        lead_minutes = max(1, min(15, lead_val))
    except Exception:
        lead_minutes = 5
    logger.info(f"Scheduling jobs with lead time: {lead_minutes} minutes in advance.")

    count = 0
    for sch in schedules:
        if not sch["is_enabled"]:
            continue

        job_id = f"sch_{sch['id']}"
        action = sch["action"]
        target_time = sch["target_time"]
        hour, minute = map(int, target_time.split(":"))

        if sch["schedule_type"] == "date" and sch["target_date"]:
            # Specific date
            year, month, day = map(int, sch["target_date"].split("-"))
            target_dt_obj = tz.localize(datetime(year, month, day, hour, minute, 0))
            run_date = target_dt_obj - timedelta(minutes=lead_minutes)
            if run_date > datetime.now(tz):
                trigger = DateTrigger(run_date=run_date, timezone=tz)
                scheduler.add_job(
                    execute_scheduled_job,
                    trigger=trigger,
                    id=job_id,
                    name=sch["title"],
                    kwargs={"schedule_id": sch["id"], "action": action, "target_time": target_time},
                    replace_existing=True
                )
                count += 1
        else:
            # Recurring weekly
            days_str = sch["days_of_week"] or "0,1,2,3,4"
            # คำนวณเวลา Trigger ล่วงหน้า (พร้อมคำนวณการข้ามวันหากย้อนข้ามเที่ยงคืน)
            ref_dt = datetime(2026, 1, 5, hour, minute, 0)  # วันจันทร์ (0)
            early_dt = ref_dt - timedelta(minutes=lead_minutes)
            trigger_hour = early_dt.hour
            trigger_minute = early_dt.minute
            day_offset = (early_dt.date() - ref_dt.date()).days

            if day_offset == 0:
                trigger_days_str = days_str
            else:
                days_list = [int(d.strip()) for d in days_str.split(",") if d.strip().isdigit()]
                shifted_days = [(d + day_offset) % 7 for d in days_list]
                trigger_days_str = ",".join(map(str, sorted(shifted_days)))

            trigger = CronTrigger(
                day_of_week=trigger_days_str,
                hour=trigger_hour,
                minute=trigger_minute,
                timezone=tz
            )
            scheduler.add_job(
                execute_scheduled_job,
                trigger=trigger,
                id=job_id,
                name=sch["title"],
                kwargs={"schedule_id": sch["id"], "action": action, "target_time": target_time},
                replace_existing=True
            )
            count += 1


    logger.info(f"Loaded {count} active schedules into APScheduler.")

def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        reload_schedules()
        logger.info("APScheduler started successfully.")
