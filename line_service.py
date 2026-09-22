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

from typing import Optional, Dict, Any, List
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
            return {"success": resp.status == 200, "message": "Reply สำเร็จ"}
    except urllib.error.HTTPError as he:
        err_msg = he.read().decode("utf-8", errors="ignore")
        logger.warning(f"LINE Reply API HTTP error {he.code}: {err_msg}")
        return {"success": False, "message": f"HTTP {he.code}: {err_msg}"}
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

def get_bot_info() -> dict:
    """
    ตรวจสอบและดึงข้อมูล Bot Info จาก LINE Messaging API
    https://developers.line.biz/en/reference/messaging-api/#get-bot-info
    """
    token = get_channel_access_token()
    if not token:
        return {"success": False, "message": "ไม่พบ Channel Access Token หรือ Channel ID/Secret ไม่ถูกต้อง"}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        req = urllib.request.Request("https://api.line.me/v2/bot/info", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                bot_data = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "bot": bot_data, "message": "เชื่อมต่อบอทสำเร็จ"}
            return {"success": False, "message": f"LINE Status: {resp.status}"}
    except urllib.error.HTTPError as he:
        err_msg = he.read().decode("utf-8")
        logger.error(f"Error get_bot_info: {he.code} {err_msg}")
        return {"success": False, "message": f"LINE API Error {he.code}: {err_msg}"}
    except Exception as e:
        logger.error(f"Exception get_bot_info: {e}")
        return {"success": False, "message": str(e)}

def get_user_profile(user_id: str) -> dict:
    """
    ดึงข้อมูลโปรไฟล์ผู้ใช้จาก LINE Messaging API
    https://developers.line.biz/en/reference/messaging-api/#get-profile
    """
    token = get_channel_access_token()
    if not token or not user_id:
        return {"success": False, "message": "Missing token or user_id"}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        req = urllib.request.Request(f"https://api.line.me/v2/bot/profile/{user_id}", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                profile = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "profile": profile}
            return {"success": False, "message": f"Status: {resp.status}"}
    except Exception as e:
        logger.warning(f"Could not fetch profile for user {user_id}: {e}")
        return {"success": False, "message": str(e)}

def get_group_summary(group_id: str) -> dict:
    """
    ดึงข้อมูลสรุปกลุ่มจาก LINE Messaging API
    https://developers.line.biz/en/reference/messaging-api/#get-group-summary
    """
    token = get_channel_access_token()
    if not token or not group_id:
        return {"success": False, "message": "Missing token or group_id"}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        req = urllib.request.Request(f"https://api.line.me/v2/bot/group/{group_id}/summary", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                summary = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "summary": summary}
            return {"success": False, "message": f"Status: {resp.status}"}
    except Exception as e:
        logger.warning(f"Could not fetch summary for group {group_id}: {e}")
        return {"success": False, "message": str(e)}

def send_line_push_to(to_id: str, text: str) -> dict:
    """
    ส่งข้อความ Push Message ไปยัง User ID, Group ID หรือ Room ID ที่ระบุ
    """
    token = get_channel_access_token()
    if not token:
        return {"success": False, "message": "ยังไม่ได้ระบุ LINE Token หรือ Channel ID/Secret"}
    if not to_id:
        return {"success": False, "message": "ยังไม่ได้ระบุปลายทาง (to_id)"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "to": to_id,
        "messages": [{"type": "text", "text": text}]
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(LINE_PUSH_URL, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return {"success": True, "message": "ส่งข้อความสำเร็จ"}
            return {"success": False, "message": f"Status: {resp.status}"}
    except urllib.error.HTTPError as he:
        err_msg = he.read().decode("utf-8")
        return {"success": False, "message": f"HTTP {he.code}: {err_msg}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_room_member_profile(room_id: str, user_id: str) -> dict:
    """
    ดึงข้อมูลโปรไฟล์สมาชิกในห้องแชท (Multi-person chat)
    https://developers.line.biz/en/reference/messaging-api/#get-room-member-profile
    """
    token = get_channel_access_token()
    if not token or not room_id or not user_id:
        return {"success": False, "message": "Missing token, room_id or user_id"}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        req = urllib.request.Request(f"https://api.line.me/v2/bot/room/{room_id}/member/{user_id}", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                profile = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "profile": profile}
            return {"success": False, "message": f"Status: {resp.status}"}
    except Exception as e:
        logger.warning(f"Could not fetch member profile for room {room_id}: {e}")
        return {"success": False, "message": str(e)}

def send_reply_or_push(to_id: str, text: str, reply_token: Optional[str] = None) -> dict:
    """
    ส่งข้อความตอบกลับหาลูกค้า:
    1. พยายามส่งผ่าน Reply API ด้วย replyToken ก่อน (ประหยัดโควต้า Push ข้อความฟรี)
    2. หาก Reply ไม่สำเร็จ (เช่น token หมดอายุเกิน 1 นาที หรือ invalid) ให้ Fallback ยิงผ่าน Push API ทันที
    """
    # ตรวจสอบว่า reply_token ใช้ได้หรือไม่
    if reply_token and reply_token not in ("00000000000000000000000000000000", "ffffffffffffffffffffffffffffffff"):
        reply_res = reply_line_message(reply_token, text)
        if reply_res.get("success"):
            logger.info(f"Message to {to_id} sent successfully via Reply API.")
            return {"success": True, "method": "reply", "message": "ส่งข้อความสำเร็จ (Reply API)"}
        else:
            logger.info(f"Reply API failed ({reply_res.get('message')}), falling back to Push Message API for {to_id}...")

    # Fallback to Push Message API
    push_res = send_line_push_to(to_id, text)
    if push_res.get("success"):
        logger.info(f"Message to {to_id} sent successfully via fallback Push API.")
        return {"success": True, "method": "push", "message": "ส่งข้อความสำเร็จ (Fallback Push API)"}
    else:
        logger.error(f"Both Reply and Push failed for {to_id}: {push_res.get('message')}")
        return push_res
