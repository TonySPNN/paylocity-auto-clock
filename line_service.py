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

def get_app_base_url() -> str:
    """
    ตรวจจับ URL ภายนอกของแอปสำหรับสร้างลิงก์คลิกดูผลลัพธ์
    1. ตรวจจาก RENDER_EXTERNAL_URL (Render environment variable)
    2. ตรวจจาก Settings 'app_base_url'
    3. ตรวจจาก Settings 'webhook_url'
    """
    import os
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        return render_url

    setting_url = get_setting("app_base_url", "").strip().rstrip("/")
    if setting_url:
        return setting_url

    wh_url = get_setting("webhook_url", "").strip()
    if wh_url and "://" in wh_url:
        try:
            parsed = urllib.parse.urlparse(wh_url)
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            pass

    return "http://localhost:8000"

def get_effective_line_user_id() -> str:
    """
    ดึง LINE User ID ที่ถูกต้องสำหรับส่ง Push Notification
    หากยังไม่มี หรือเป็น dummy ID 'U999888777' จะค้นหาจากผู้ใช้ล่าสุดใน line_chats อัตโนมัติ
    """
    user_id = get_setting("line_user_id", "").strip()
    if not user_id or user_id == "U999888777":
        try:
            from database import get_line_chats
            chats = get_line_chats()
            for c in chats:
                sid = str(c.get("source_id", "")).strip()
                if sid.startswith("U") and sid != "U999888777":
                    user_id = sid
                    update_settings({"line_user_id": user_id})
                    logger.info(f"Auto-selected active LINE user ID from chats: {user_id}")
                    break
        except Exception as e:
            logger.warning(f"Error resolving active LINE user: {e}")
    return user_id

def send_line_message(text: str) -> dict:
    """
    ส่งข้อความแจ้งเตือนผ่าน LINE Push Message API
    """
    if get_setting("line_enabled", "1") != "1":
        return {"success": False, "message": "LINE notification disabled"}

    token = get_channel_access_token()
    user_id = get_effective_line_user_id()

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
    รองรับทั้ง URL เต็ม (HTTPS) หรือ path สัมพัทธ์ เช่น /screenshots/...
    """
    if get_setting("line_enabled", "1") != "1":
        return {"success": False, "message": "LINE notification disabled"}

    token = get_channel_access_token()
    user_id = get_effective_line_user_id()

    if not token or not user_id:
        return {"success": False, "message": "Missing token or user_id"}

    base_url = get_app_base_url()
    # แปลง relative path เป็น public URL
    full_image_url = image_url
    if not image_url.startswith("http://") and not image_url.startswith("https://"):
        cleaned_path = "/" + image_url.lstrip("/")
        full_image_url = f"{base_url}{cleaned_path}"

    # LINE บังคับว่า image URL ต้องเป็น HTTPS เท่านั้น
    if not full_image_url.startswith("https://"):
        logger.info(f"Skipping direct LINE image push because URL is not HTTPS ({full_image_url}). User can still view via web link.")
        return {"success": False, "message": "LINE requires HTTPS image URL"}

    full_preview_url = preview_url or full_image_url
    if not full_preview_url.startswith("https://"):
        full_preview_url = full_image_url

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "image",
                "originalContentUrl": full_image_url,
                "previewImageUrl": full_preview_url
            }
        ]
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(LINE_PUSH_URL, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"success": resp.status == 200}
    except Exception as e:
        logger.warning(f"Failed to push image to LINE: {e}")
        return {"success": False, "message": str(e)}

def send_punch_notification(action: str, status: str, message: str,
                            history_id: Optional[int] = None,
                            screenshot_filename: Optional[str] = None,
                            target_time: Optional[str] = None) -> dict:
    """
    ส่งข้อความรายงานสถานะการลงเวลาเข้า LINE อย่างเป็นระบบ:
    - แจ้งเตือนเมื่อบอทเริ่มงาน
    - แจ้งเตือนเมื่อลงเวลาสำเร็จ/ล้มเหลว พร้อมส่งลิงก์ดูหน้าจอสรุปผลและภาพแคป
    """
    action_th = "เข้างาน (Clock In)" if action == "clock_in" else "ออกงาน (Clock Out)"
    base_url = get_app_base_url()
    now_str = datetime.now().strftime("%H:%M:%S (%d/%m/%Y)") if "datetime" in globals() else ""
    if not now_str:
        from datetime import datetime as dt
        now_str = dt.now().strftime("%H:%M:%S (%d/%m/%Y)")

    if status == "started":
        target_line = f"\n🎯 เวลากดเป้าหมาย: {target_time}" if target_time else ""
        text = (
            f"🚀 [Paylocity] เริ่มกระบวนการลงเวลา {action_th} แล้ว!{target_line}\n"
            f"บอทกำลังเปิดหน้าเว็บ SSO และเตรียม Standby พร้อมกดในเวลาที่กำหนดครับ..."
        )
        return send_line_message(text)

    # Result URL สำหรับคลิกดูหน้าจอสรุปและรูปภาพ
    result_url = f"{base_url}/result/{history_id}" if history_id else f"{base_url}/#history"

    if status in ["success", "warning"]:
        text = (
            f"🎉 [Paylocity] ลงเวลา {action_th} สำเร็จ!\n"
            f"⏱️ เวลาที่กด: {now_str}\n"
            f"📌 ผลลัพธ์: {message}\n\n"
            f"📱 คลิกดูหน้าจอสรุปผลและภาพแคปได้ที่:\n"
            f"{result_url}"
        )
        res = send_line_message(text)
        if screenshot_filename:
            send_line_image(f"/screenshots/{screenshot_filename}")
        return res
    else:
        text = (
            f"❌ [Paylocity] ลงเวลา {action_th} ไม่สำเร็จ\n"
            f"⏱️ เวลา: {now_str}\n"
            f"⚠️ รายละเอียด: {message}\n\n"
            f"📱 ตรวจสอบหน้าจอและรายละเอียดข้อผิดพลาดได้ที่:\n"
            f"{result_url}"
        )
        res = send_line_message(text)
        if screenshot_filename:
            send_line_image(f"/screenshots/{screenshot_filename}")
        return res


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
