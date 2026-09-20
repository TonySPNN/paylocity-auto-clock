import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import pytz
from datetime import datetime

from database import get_schedules, get_setting
import paylocity_bot

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()

async def execute_scheduled_job(schedule_id: int, action: str):
    logger.info(f"Triggering scheduled job #{schedule_id} for action: {action}")
    await paylocity_bot.run_punch(action=action)

def reload_schedules():
    """
    โหลดตารางงานทั้งหมดจากฐานข้อมูลเข้ามาใน APScheduler
    """
    scheduler.remove_all_jobs()
    schedules = get_schedules()
    tz_str = get_setting("timezone", "Asia/Bangkok")
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.timezone("Asia/Bangkok")

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
            run_date = tz.localize(datetime(year, month, day, hour, minute, 0))
            if run_date > datetime.now(tz):
                trigger = DateTrigger(run_date=run_date, timezone=tz)
                scheduler.add_job(
                    execute_scheduled_job,
                    trigger=trigger,
                    id=job_id,
                    name=sch["title"],
                    kwargs={"schedule_id": sch["id"], "action": action},
                    replace_existing=True
                )
                count += 1
        else:
            # Recurring weekly
            days_str = sch["days_of_week"] or "0,1,2,3,4"
            # APScheduler cron uses 0-6 or mon,tue,wed,thu,fri,sat,sun
            trigger = CronTrigger(
                day_of_week=days_str,
                hour=hour,
                minute=minute,
                timezone=tz
            )
            scheduler.add_job(
                execute_scheduled_job,
                trigger=trigger,
                id=job_id,
                name=sch["title"],
                kwargs={"schedule_id": sch["id"], "action": action},
                replace_existing=True
            )
            count += 1

    logger.info(f"Loaded {count} active schedules into APScheduler.")

def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        reload_schedules()
        logger.info("APScheduler started successfully.")
