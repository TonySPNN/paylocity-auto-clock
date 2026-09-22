import logging
import json
import base64
import hmac
import hashlib
import time
import urllib.request
import urllib.parse
import urllib.error

try:
    import requests
except ImportError:
    requests = None

from database import get_setting, update_settings

logger = logging.getLogger("line_service")

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_OAUTH_TOKEN_URL = "https://api.line.me/v2/oauth/accessToken"

def get_channel_access_token() -> str:
    """
    ดึง Channel Access Token:
    1. หากมี Token บันทึกไว้อยู่แล้ว ให้ใช้ทันที
    2. หากมี Channel ID และ Channel Secret ให้ยิงขอ Access Token จาก LINE OAuth อัตโนมัติ
    """
    token = get_setting("line_channel_access_token", "").strip()
    if token:
        return token

    channel_id = get_setting("line_channel_id", "").strip()
    channel_secret = get_setting("line_channel_secret", "").strip()

    if channel_id and channel_secret:
        logger.info("Requesting Channel Access Token from LINE OAuth v2.1...")
        data = {
            "grant_type": "client_credentials",
            "client_id": channel_id,
            "client_secret": channel_secret
        }
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            req = urllib.request.Request(LINE_OAUTH_TOKEN_URL, data=encoded, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                new_token = result.get("access_token", "")
                if new_token:
                    logger.info("LINE Access Token generated successfully via OAuth.")
                    update_settings({"line_channel_access_token": new_token})
                    return new_token
        except Exception as e:
            logger.error(f"Failed to obtain LINE Access Token via OAuth: {e}")

    return ""

def verify_signature(body_bytes: bytes, signature: str) -> bool:
    """
    ตรวจสอบความถูกต้องของ Webhook Signature จาก LINE
    """
    secret = get_setting("line_channel_secret", "").strip()
    if not secret:
        # หากยังไม่ได้ระบุ Secret ให้ผ่านเพื่อความสะดวกในการเทส
        return True

    hash_val = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    expected_sig = base64.b64encode(hash_val).decode("utf-8")
    return hmac.compare_digest(expected_sig, signature)

def reply_line_message(reply_token: str, text: str) -> dict:
    """
    ตอบกลับข้อความผ่าน LINE Reply API
    """
    token = get_channel_access_token()
    if not token or not reply_token:
        return {"success": False, "message": "Missing token or replyToken"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(LINE_REPLY_URL, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"success": resp.status == 200}
    except Exception as e:
        logger.error(f"Error replying LINE message: {e}")
        return {"success": False, "message": str(e)}

def send_line_message(text: str) -> dict:
    """
    ส่งข้อความแจ้งเตือนผ่าน LINE Push Message API
    """
    if get_setting("line_enabled", "1") != "1":
        return {"success": False, "message": "LINE notification disabled"}

    token = get_channel_access_token()
    user_id = get_setting("line_user_id", "").strip()

    if not token:
        msg = "LINE Access Token ยังไม่มี (กรุณาใส่ Channel ID และ Channel Secret หรือใส่ Token ใน Settings)"
        logger.warning(msg)
        return {"success": False, "message": msg}

    if not user_id:
        msg = "LINE User ID ยังไม่มี (กรุณาส่งข้อความหาบอท หรือกรอก User ID ใน Settings)"
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
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(LINE_PUSH_URL, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("LINE push message sent successfully.")
                return {"success": True, "message": "ส่งข้อความ LINE สำเร็จ"}
            return {"success": False, "message": f"LINE HTTP status: {resp.status}"}
    except urllib.error.HTTPError as he:
        err_msg = he.read().decode("utf-8")
        logger.error(f"LINE API HTTP error: {he.code} {err_msg}")
        return {"success": False, "message": f"LINE API Error {he.code}: {err_msg}"}
    except Exception as e:
        logger.error(f"Exception sending LINE message: {e}")
        return {"success": False, "message": str(e)}

def send_line_image(image_url: str, preview_url: str = None) -> dict:
    """
    ส่งรูปภาพผลลัพธ์ผ่าน LINE Messaging API
    """
    if get_setting("line_enabled", "1") != "1":
        return {"success": False, "message": "LINE notification disabled"}

    token = get_channel_access_token()
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
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(LINE_PUSH_URL, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"success": resp.status == 200}
    except Exception as e:
        return {"success": False, "message": str(e)}
