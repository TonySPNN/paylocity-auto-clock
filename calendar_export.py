import datetime
import uuid
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None
from database import get_schedules, get_setting

DAY_MAP_ICS = {
    "0": "MO",
    "1": "TU",
    "2": "WE",
    "3": "TH",
    "4": "FR",
    "5": "SA",
    "6": "SU"
}

def generate_ics_content() -> str:
    """
    สร้างไฟล์ iCalendar (.ics) จากตารางเวลาทั้งหมดในระบบ
    พร้อมฝังการตั้งปลุก/แจ้งเตือน (VALARM) ล่วงหน้า 2 นาที และ ณ เวลาเป้าหมาย
    เพื่อให้ Google Calendar / Apple Calendar สั่นและส่งเสียงปลุกบนมือถือ
    """
    schedules = get_schedules()
    tz_name = get_setting("timezone", "Asia/Bangkok")
    
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Paylocity Auto Clock//TH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-TIMEZONE:{tz_name}",
        f"X-WR-CALNAME:Paylocity Clock Alerts"
    ]
    
    now = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    
    for sch in schedules:
        if not sch["is_enabled"]:
            continue
            
        action_title = "ลงเวลาเข้างาน (Clock In)" if sch["action"] == "clock_in" else "ลงเวลาออกงาน (Clock Out)"
        summary = f"⏰ {action_title} - {sch['title']}"
        desc = "กรุณาเตรียมเปิดแอป Duo บนโทรศัพท์มือถือ เพื่อกดยืนยันตัวตน (Approve) สำหรับ Paylocity"
        uid = f"paylocity-{sch['id']}-{uuid.uuid4().hex[:8]}@autoclock"
        
        target_time_parts = sch["target_time"].split(":")
        hour = int(target_time_parts[0])
        minute = int(target_time_parts[1])
        
        if sch["schedule_type"] == "date" and sch["target_date"]:
            # Specific date
            date_parts = sch["target_date"].split("-")
            dt_start = f"{date_parts[0]}{date_parts[1]}{date_parts[2]}T{hour:02d}{minute:02d}00"
            dt_end = f"{date_parts[0]}{date_parts[1]}{date_parts[2]}T{hour:02d}{(minute+15)%60:02d}00"
            rrule = None
        else:
            # Recurring weekly
            # Start from next occurrence or today
            today = datetime.date.today()
            dt_start = f"{today.strftime('%Y%m%d')}T{hour:02d}{minute:02d}00"
            dt_end = f"{today.strftime('%Y%m%d')}T{hour:02d}{(minute+15)%60:02d}00"
            
            days = sch["days_of_week"].split(",") if sch["days_of_week"] else ["0", "1", "2", "3", "4"]
            ics_days = [DAY_MAP_ICS.get(d.strip(), "MO") for d in days if d.strip() in DAY_MAP_ICS]
            rrule = f"RRULE:FREQ=WEEKLY;BYDAY={','.join(ics_days)}"

        ics_lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID={tz_name}:{dt_start}",
            f"DTEND;TZID={tz_name}:{dt_end}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc}",
            "STATUS:CONFIRMED"
        ])
        
        if rrule:
            ics_lines.append(rrule)
            
        # Alarm 1: 2 minutes before
        ics_lines.extend([
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:เตรียมตัวเปิดแอป Duo สำหรับลงเวลา Paylocity ในอีก 2 นาที",
            "TRIGGER:-PT2M",
            "END:VALARM"
        ])
        
        # Alarm 2: Exact minute
        ics_lines.extend([
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:ถึงเวลา{action_title}! เปิดแอป Duo กด Approve ทันที",
            "TRIGGER:-PT0M",
            "END:VALARM"
        ])
        
        ics_lines.append("END:VEVENT")

    ics_lines.append("END:VCALENDAR")
    return "\r\n".join(ics_lines)
