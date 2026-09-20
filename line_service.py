import logging
import json
try:
    import requests
except ImportError:
    requests = None
import urllib.request
import urllib.error
from database import get_setting

logger = logging.getLogger("line_service")

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

def send_line_message(text: str) -> dict:
    """
    ส่งข้อความแจ้งเตือนผ่าน LINE Messaging API
    """
    if get_setting("line_enabled", "1") != "1":
        return {"success": False, "message": "LINE notification disabled"}

    token = get_setting("line_channel_access_token", "").strip()
    user_id = get_setting("line_user_id", "").strip()

    if not token or not user_id:
        msg = "LINE Channel Access Token หรือ User ID ยังไม่ได้กรอกใน Settings"
        logger.warning(msg)
        return {"success": False, "message": msg}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    try:
        if requests:
            res = requests.post(LINE_PUSH_URL, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                logger.info("LINE push message sent successfully.")
                return {"success": True, "message": "ส่งข้อความ LINE สำเร็จ"}
            else:
                logger.error(f"LINE API error: {res.status_code} {res.text}")
                return {"success": False, "message": f"LINE API Error: {res.status_code} {res.text}"}
        else:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(LINE_PUSH_URL, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return {"success": True, "message": "ส่งข้อความ LINE สำเร็จ"}
                return {"success": False, "message": f"LINE HTTP status: {resp.status}"}
    except Exception as e:
        logger.error(f"Exception sending LINE message: {e}")
        return {"success": False, "message": str(e)}

def send_line_image(image_url: str, preview_url: str = None) -> dict:
    """
    ส่งรูปภาพผลลัพธ์ผ่าน LINE Messaging API (ต้องการ URL ที่เข้าถึงได้ผ่าน HTTPS)
    """
    if get_setting("line_enabled", "1") != "1":
        return {"success": False, "message": "LINE notification disabled"}

    token = get_setting("line_channel_access_token", "").strip()
    user_id = get_setting("line_user_id", "").strip()

    if not token or not user_id:
        return {"success": False, "message": "Missing token or user_id"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": preview_url or image_url
            }
        ]
    }

    try:
        res = requests.post(LINE_PUSH_URL, json=payload, headers=headers, timeout=10)
        return {"success": res.status_code == 200, "status_code": res.status_code}
    except Exception as e:
        return {"success": False, "message": str(e)}
