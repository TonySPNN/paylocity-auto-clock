import logging
import json
import re
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

from database import get_setting

logger = logging.getLogger("gemini_service")

DEFAULT_MODEL = "gemini-1.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

def get_gemini_api_keys() -> List[str]:
    """
    ดึงรายการ Gemini API Keys ทั้งหมดที่ผู้ใช้ตั้งค่าไว้
    รองรับการใส่หลายคีย์ (คั่นด้วยบรรทัดใหม่ หรือเครื่องหมายจุลภาค)
    """
    raw = get_setting("gemini_api_keys", "").strip()
    if not raw:
        return []
    # แยกตาม newline หรือ comma
    keys = [k.strip() for k in re.split(r'[\n,]+', raw) if k.strip()]
    return keys

def list_available_models(api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    ดึงรายการโมเดลทั้งหมดที่รองรับ generateContent จาก Gemini API
    """
    keys = [api_key] if api_key else get_gemini_api_keys()
    if not keys or not keys[0]:
        return {"success": False, "message": "ยังไม่ได้ระบุ Gemini API Key"}

    for key in keys:
        url = f"{GEMINI_API_BASE}/models?key={key}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = []
                    for m in data.get("models", []):
                        methods = m.get("supportedGenerationMethods", [])
                        if "generateContent" in methods:
                            name = m.get("name", "").replace("models/", "")
                            display_name = m.get("displayName", name)
                            models.append({"name": name, "displayName": display_name})
                    return {"success": True, "models": models}
        except Exception as e:
            logger.warning(f"Failed to fetch models with key {key[:8]}...: {e}")
            continue

    return {"success": False, "message": "ไม่สามารถดึงรายชื่อโมเดลได้ โปรดตรวจสอบ API Key"}

def generate_ai_reply(prompt: str, history: Optional[List[Dict[str, Any]]] = None,
                      system_instruction: Optional[str] = None,
                      model: Optional[str] = None) -> Dict[str, Any]:
    """
    สร้างคำตอบอัตโนมัติด้วย Gemini API พร้อมระบบ Multi-Key Failover
    หากคีย์ใดหมดโควต้า (429 RateLimit/QuotaExceeded) หรือ Error จะสลับไปใช้คีย์ถัดไปทันที
    """
    keys = get_gemini_api_keys()
    if not keys:
        return {"success": False, "message": "ยังไม่ได้กรอก Gemini API Key ในการตั้งค่า"}

    active_model = model or get_setting("gemini_model", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    sys_prompt = system_instruction or get_setting("ai_system_prompt", "").strip()

    # จัดเตรียมโครงสร้าง Contents สำหรับ Gemini API
    contents = []
    if history:
        for msg in history:
            sender = msg.get("sender_type")
            content = msg.get("content", "").strip()
            if not content:
                continue
            role = "user" if sender == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": content}]
            })

    # ใส่คำถาม/ข้อความล่าสุดของผู้ใช้
    contents.append({
        "role": "user",
        "parts": [{"text": prompt}]
    })

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000
        }
    }

    if sys_prompt:
        payload["system_instruction"] = {
            "parts": [{"text": sys_prompt}]
        }

    encoded_data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    last_error = ""

    # วนลูปทดลองแต่ละ Key ในระบบ (Failover Mechanism)
    for idx, key in enumerate(keys):
        url = f"{GEMINI_API_BASE}/models/{active_model}:generateContent?key={key}"
        try:
            logger.info(f"Calling Gemini API with Key #{idx + 1} (model: {active_model})...")
            req = urllib.request.Request(url, data=encoded_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        reply_text = "".join([p.get("text", "") for p in parts]).strip()
                        if reply_text:
                            logger.info(f"Gemini generated response successfully with Key #{idx + 1}")
                            return {
                                "success": True,
                                "reply_text": reply_text,
                                "model_used": active_model,
                                "key_index": idx + 1
                            }
                    return {"success": False, "message": "Gemini ส่งผลลัพธ์ว่างกลับมา"}
        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")
            logger.warning(f"Gemini Key #{idx + 1} HTTP error {he.code}: {err_body}")
            last_error = f"HTTP {he.code}: {err_body}"
            # หากเป็น 429 Quota Exceeded หรือ 403 Forbidden ให้ failover ไปใช้ key ถัดไปทันที
            continue
        except Exception as e:
            logger.warning(f"Gemini Key #{idx + 1} exception: {e}")
            last_error = str(e)
            continue

    logger.error(f"All {len(keys)} Gemini API keys failed. Last error: {last_error}")
    return {
        "success": False,
        "message": f"Gemini API keys ทั้งหมดหมดโควต้าหรือเกิดข้อผิดพลาด: {last_error}"
    }

def test_gemini_connection(api_keys: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    ทดสอบการเชื่อมต่อ Gemini API กับ Key ที่ระบุ
    """
    raw_keys = [k.strip() for k in re.split(r'[\n,]+', api_keys) if k.strip()]
    if not raw_keys:
        return {"success": False, "message": "โปรดระบุ Gemini API Key อย่างน้อย 1 คีย์"}

    target_model = model.strip() if model else DEFAULT_MODEL

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": "ทดสอบระบบ ตอบสั้นๆ ว่า 'พร้อมใช้งาน'"}]
        }],
        "generationConfig": {"maxOutputTokens": 30}
    }
    encoded_data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    errors = []
    for idx, key in enumerate(raw_keys):
        url = f"{GEMINI_API_BASE}/models/{target_model}:generateContent?key={key}"
        try:
            req = urllib.request.Request(url, data=encoded_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    candidates = res_json.get("candidates", [])
                    reply = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip() if candidates else "OK"
                    return {
                        "success": True,
                        "message": f"เชื่อมต่อสำเร็จด้วย Key #{idx + 1}!",
                        "model": target_model,
                        "key_used_index": idx + 1,
                        "sample_reply": reply,
                        "total_keys": len(raw_keys)
                    }
        except urllib.error.HTTPError as he:
            err_msg = he.read().decode("utf-8", errors="ignore")
            errors.append(f"Key #{idx + 1} (HTTP {he.code}): {err_msg[:120]}")
        except Exception as e:
            errors.append(f"Key #{idx + 1}: {str(e)}")

    return {
        "success": False,
        "message": "ทดสอบไม่สำเร็จ:\n" + "\n".join(errors)
    }
