import logging
import urllib.parse
from database import get_setting

logger = logging.getLogger("voice_caller")

def make_voice_call(action_text: str = "เข้างาน") -> dict:
    """
    โทรเข้าเบอร์โทรศัพท์ผ่าน Twilio Voice API
    เพื่อปลุกหรือแจ้งเตือนผู้ใช้ให้เปิดแอป Duo
    """
    voice_enabled = get_setting("voice_call_enabled", "1")
    if voice_enabled != "1":
        logger.info("Voice call is disabled in settings.")
        return {"success": False, "message": "Voice call disabled in settings"}

    account_sid = get_setting("twilio_account_sid", "").strip()
    auth_token = get_setting("twilio_auth_token", "").strip()
    from_number = get_setting("twilio_from_number", "").strip()
    to_number = get_setting("phone_number", "").strip()

    if not (account_sid and auth_token and from_number and to_number):
        msg = "Twilio credentials หรือเบอร์โทรศัพท์ยังไม่ได้กรอกใน Settings"
        logger.warning(msg)
        return {"success": False, "message": msg}

    # ข้อความเสียงภาษาไทยที่ต้องการให้พูดเมื่อรับสาย
    twiml_msg = (
        f"สวัสดีครับ ถึงเวลาลงเวลา{action_text} Paylocity แล้วครับ "
        "กรุณาเปิดแอปพลิเคชัน Duo บนโทรศัพท์มือถือ เพื่อกดยืนยันตัวตน Approve ได้เลยครับ"
    )
    
    # สร้าง TwiML XML
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say language="th-TH">{twiml_msg}</Say>'
        '<Pause length="2"/>'
        '<Say language="th-TH">กรุณาเปิดแอป Duo แล้วกดติ๊กถูกเพื่อยืนยันตัวตนครับ ขอบคุณครับ</Say>'
        '</Response>'
    )

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        
        call = client.calls.create(
            twiml=twiml,
            to=to_number,
            from_=from_number
        )
        logger.info(f"Twilio call initiated successfully. SID: {call.sid}")
        return {"success": True, "call_sid": call.sid, "message": f"โทรสำเร็จ (Call SID: {call.sid})"}
    except ImportError:
        # Fallback to direct HTTP request using requests or urllib
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"
        data = {
            "To": to_number,
            "From": from_number,
            "Twiml": twiml
        }
        try:
            import requests
            res = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=15)
            if res.status_code in (200, 201):
                sid = res.json().get("sid", "")
                return {"success": True, "call_sid": sid, "message": f"โทรสำเร็จ (Call SID: {sid})"}
            else:
                return {"success": False, "message": f"Twilio API Error: {res.status_code} {res.text}"}
        except ImportError:
            import urllib.request
            import urllib.parse
            import urllib.error
            import base64
            import json
            import ssl

            encoded_data = urllib.parse.urlencode(data).encode("utf-8")
            auth_str = f"{account_sid}:{auth_token}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
            headers = {
                "Authorization": f"Basic {b64_auth}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            req = urllib.request.Request(url, data=encoded_data, headers=headers, method="POST")
            
            # Use default context or unverified if certificates missing locally
            ctx = ssl.create_default_context()
            try:
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    sid = resp_json.get("sid", "")
                    return {"success": True, "call_sid": sid, "message": f"โทรสำเร็จ (Call SID: {sid})"}
            except urllib.error.HTTPError as he:
                err_body = he.read().decode("utf-8")
                return {"success": False, "message": f"Twilio HTTP Error {he.code}: {err_body}"}
            except urllib.error.URLError as ue:
                # Retry with unverified ssl if system certs missing
                ctx_unverified = ssl._create_unverified_context()
                try:
                    with urllib.request.urlopen(req, timeout=15, context=ctx_unverified) as resp:
                        resp_json = json.loads(resp.read().decode("utf-8"))
                        sid = resp_json.get("sid", "")
                        return {"success": True, "call_sid": sid, "message": f"โทรสำเร็จ (Call SID: {sid})"}
                except Exception as inner_e:
                    return {"success": False, "message": f"Twilio Connection Error: {inner_e}"}
    except Exception as e:
        logger.error(f"Error making voice call: {e}")
        return {"success": False, "message": f"Error: {str(e)}"}

if __name__ == "__main__":
    # Quick standalone test
    print("Testing voice caller...")
    res = make_voice_call("เข้างาน")
    print("Result:", res)
