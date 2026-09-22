import os
import time
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from database import get_setting, add_history, update_history
from voice_caller import make_voice_call
from line_service import send_line_message, send_line_image

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("paylocity_bot")

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def run_punch(action: str = "clock_in", history_id: Optional[int] = None) -> Dict[str, Any]:
    """
    รันบอท Playwright เพื่อทำการ Clock In หรือ Clock Out
    พร้อมระบบโทรแจ้งเตือน Duo และส่งผลเข้า LINE
    """
    action_th = "เข้างาน (Clock In)" if action == "clock_in" else "ออกงาน (Clock Out)"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_filename = f"{action}_{timestamp}.png"
    screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_filename)

    if not history_id:
        history_id = add_history(action, "running", f"กำลังเริ่มต้นกระบวนการลงเวลา {action_th}...")
    else:
        update_history(history_id, "running", f"กำลังเริ่มต้นกระบวนการลงเวลา {action_th}...")

    logger.info(f"Starting punch workflow for {action} (History ID: {history_id})")

    # อ่านค่าการตั้งค่าจาก SQLite
    paylocity_url = get_setting("paylocity_url", "https://access.paylocity.com/").strip()
    company_id = get_setting("company_id", "").strip()
    username = get_setting("username", "").strip()
    password = get_setting("password", "").strip()

    if not username or not password:
        err_msg = "ยังไม่ได้ระบุ Username หรือ Password ในเมนู Settings"
        logger.error(err_msg)
        update_history(history_id, "failed", err_msg)
        send_line_message(f"❌ ลงเวลา {action_th} ไม่สำเร็จ: {err_msg}")
        return {"success": False, "message": err_msg}

    # นำเข้า Playwright
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        err_msg = "ยังไม่ได้ติดตั้ง Playwright กรุณารัน 'pip install playwright && playwright install chromium'"
        logger.error(err_msg)
        update_history(history_id, "failed", err_msg)
        return {"success": False, "message": err_msg}

    async with async_playwright() as p:
        # เปิดเบราว์เซอร์ Chromium
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            logger.info(f"Navigating to Paylocity login URL...")
            await page.goto(paylocity_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            # 1. กรอกข้อมูลล็อกอิน & ตรวจสอบปุ่ม Single Sign-On (SSO)
            logger.info("Checking for SSO or Login form...")

            # หากมีปุ่ม SSO ให้กดเข้าสู่ระบบผ่าน SSO ทันที
            sso_btn = page.locator("button:has-text('Single Sign-On'), a:has-text('Single Sign-On'), button:has-text('SSO'), a:has-text('SSO'), [aria-label*='SSO' i], [data-testid*='sso' i]")
            if await sso_btn.count() > 0 and await sso_btn.first.is_visible():
                logger.info("Found Single Sign-On (SSO) button, clicking...")
                await sso_btn.first.click()
                await asyncio.sleep(2)

            # ตรวจสอบว่ามีช่อง Company ID หรือไม่ (ถ้ามีค่าค่อยกรอก)
            if company_id:
                company_input = page.locator("input#CompanyId, input[name='CompanyId'], input[placeholder*='Company' i]")
                if await company_input.count() > 0 and await company_input.first.is_visible():
                    logger.info("Filling Company ID...")
                    await company_input.first.fill(company_id)

            # กรอก Email / Username
            user_input = page.locator("input[type='email'], input#Username, input[name='Username'], input[name='loginfmt'], input#identification, input[name='identifier'], input[placeholder*='Email' i], input[placeholder*='User' i]")
            if await user_input.count() > 0 and await user_input.first.is_visible():
                logger.info("Filling Email/Username...")
                await user_input.first.fill(username)

            # ตรวจสอบว่าช่อง Password ปรากฏอยู่บนหน้าจอแล้วหรือไม่
            pass_input = page.locator("input#Password, input[name='Password'], input[type='password'], input[name='passwd']")
            pass_visible = await pass_input.count() > 0 and await pass_input.first.is_visible()

            # หากเป็นระบบล็อกอินแบบ 2 สเต็ป (เช่น Microsoft / Okta ที่ต้องกด Next ก่อนกรอกรหัส)
            if not pass_visible:
                next_btn = page.locator("button:has-text('Next'), input[value='Next'], button:has-text('Continue'), input#idSIButton9, button[type='submit']")
                if await next_btn.count() > 0 and await next_btn.first.is_visible():
                    logger.info("Clicking Next to proceed to password...")
                    await next_btn.first.click()
                    await asyncio.sleep(2)

            # กรอก Password
            pass_input = page.locator("input#Password, input[name='Password'], input[type='password'], input[name='passwd']")
            if await pass_input.count() > 0 and await pass_input.first.is_visible():
                logger.info("Filling Password...")
                await pass_input.first.fill(password)

            # กดปุ่ม Login / Sign In / Submit
            login_btn = page.locator("button[type='submit'], input[type='submit'], button:has-text('Log In'), button:has-text('Sign In'), input[value='Login'], input[value='Sign in'], input#idSIButton9")
            if await login_btn.count() > 0 and await login_btn.first.is_visible():
                logger.info("Clicking Login button...")
                await login_btn.first.click()
            else:
                await page.keyboard.press("Enter")

            await asyncio.sleep(3)

            # จัดการหน้าต่าง 'Stay signed in?' (ถ้ามี เช่น Microsoft SSO)
            stay_signed_in = page.locator("input#idSIButton9[value='Yes'], button:has-text('Yes'), button:has-text('Stay signed in')")
            if await stay_signed_in.count() > 0 and await stay_signed_in.first.is_visible():
                logger.info("Clicking 'Yes' on Stay signed in prompt...")
                await stay_signed_in.first.click()
                await asyncio.sleep(2)

            # 2. ตรวจจับหน้า Duo Security 2FA
            logger.info("Checking for Duo 2FA prompt...")
            # ส่งสายโทรเข้ามือถือ และส่งข้อความเตือน LINE ทันที
            make_voice_call(action_text="เข้างาน" if action == "clock_in" else "ออกงาน")
            send_line_message(
                f"⏰ ถึงเวลาลงเวลา {action_th} แล้ว!\n"
                "ระบบกำลังเชื่อมต่อ Paylocity...\n"
                "👉 กรุณาเปิดแอปพลิเคชัน Duo บนมือถือของคุณ แล้วแตะ 'Approve / ติ๊กถูก' ได้เลยครับ"
            )

            # ตรวจสอบหาปุ่ม Send Push ของ Duo เผื่อระบบไม่ได้ส่งอัตโนมัติ
            try:
                # ตรวจสอบทั้งในหน้าเว็บหลักและใน iframe
                duo_push_btn = page.locator("button:has-text('Send Me a Push'), button:has-text('Duo Push'), button:has-text('Push')")
                if await duo_push_btn.count() > 0 and await duo_push_btn.first.is_visible():
                    logger.info("Clicking Duo Send Push button...")
                    await duo_push_btn.first.click()
            except Exception as e:
                logger.debug(f"Duo push button search note: {e}")

            # 3. รอผู้ใช้กดยืนยันตัวตนในแอป Duo (สูงสุด 120 วินาที)
            logger.info("Waiting for Duo approval on user's phone (up to 120s)...")
            start_wait = time.time()
            approved = False

            while time.time() - start_wait < 120:
                current_url = page.url
                # ถ้าหลุดออกจากหน้า access.paylocity.com/duo หรือเข้าสู่ dashboard/portal แล้ว
                if "escher" in current_url.lower() or "login.paylocity.com" in current_url.lower() or "punch" in current_url.lower() or "portal" in current_url.lower() or "workforce" in current_url.lower():
                    # ตรวจสอบว่ามี element ของ Paylocity dashboard หรือไม่
                    dashboard_elem = page.locator("text=Clock, text=Punch, text=Paylocity, [aria-label*='Clock' i], [aria-label*='Punch' i]")
                    if await dashboard_elem.count() > 0:
                        approved = True
                        logger.info("Duo approval detected! Redirected to Paylocity portal.")
                        break
                await asyncio.sleep(3)

            if not approved:
                # แคปเจอร์หน้าจอตอนที่รอหมดเวลาเพื่อดูว่าติดตรงไหน
                await page.screenshot(path=screenshot_path)
                err_msg = "หมดเวลาการรออนุมัติ Duo (ไม่ได้กด Approve บนมือถือภายใน 120 วินาที)"
                logger.error(err_msg)
                update_history(history_id, "failed", err_msg, f"/screenshots/{screenshot_filename}")
                send_line_message(f"❌ ลงเวลา {action_th} ล้มเหลว:\n{err_msg}")
                await browser.close()
                return {"success": False, "message": err_msg}

            # 4. ค้นหาและกดปุ่ม Clock In หรือ Clock Out
            logger.info(f"Looking for {action} button...")
            await asyncio.sleep(3)

            if action == "clock_in":
                punch_btn = page.locator("button:has-text('Clock In'), a:has-text('Clock In'), [aria-label='Clock In' i], [title='Clock In' i]")
            else:
                punch_btn = page.locator("button:has-text('Clock Out'), a:has-text('Clock Out'), [aria-label='Clock Out' i], [title='Clock Out' i]")

            button_found = False
            if await punch_btn.count() > 0 and await punch_btn.first.is_visible():
                logger.info(f"Found {action} button directly, clicking...")
                await punch_btn.first.click()
                button_found = True
            else:
                # ลองค้นหาปุ่ม Punch ทั่วไป
                generic_punch = page.locator("button:has-text('Punch'), [aria-label*='Punch' i]")
                if await generic_punch.count() > 0 and await generic_punch.first.is_visible():
                    logger.info("Found generic Punch button, clicking...")
                    await generic_punch.first.click()
                    button_found = True

            await asyncio.sleep(4)

            # 5. ถ่ายภาพหน้าจอ (Screenshot) บันทึกหลักฐาน
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info(f"Screenshot saved to {screenshot_path}")

            now_str = datetime.now().strftime("%H:%M:%S (%d/%m/%Y)")
            if button_found:
                success_msg = f"กดปุ่ม {action_th} สำเร็จเรียบร้อยเมื่อ {now_str}"
                status = "success"
            else:
                success_msg = f"เข้าสู่ระบบได้สำเร็จ แต่ไม่พบปุ่ม {action_th} อัตโนมัติ (บันทึกภาพหน้าจอไว้ให้ตรวจสอบ)"
                status = "warning"

            update_history(history_id, status, success_msg, f"/screenshots/{screenshot_filename}")
            
            # ส่งแจ้งเตือนสรุปผลเข้า LINE
            send_line_message(
                f"✅ {action_th} เรียบร้อยแล้ว!\n"
                f"⏰ เวลา: {now_str}\n"
                f"📌 สถานะ: {success_msg}"
            )

            await browser.close()
            return {"success": True, "message": success_msg, "screenshot": f"/screenshots/{screenshot_filename}"}

        except Exception as e:
            logger.exception(f"Error during bot execution: {e}")
            try:
                await page.screenshot(path=screenshot_path)
            except Exception:
                pass
            
            err_msg = f"เกิดข้อผิดพลาดระหว่างรันบอท: {str(e)}"
            update_history(history_id, "failed", err_msg, f"/screenshots/{screenshot_filename}")
            send_line_message(f"❌ ลงเวลา {action_th} ล้มเหลว:\n{err_msg}")
            await browser.close()
            return {"success": False, "message": err_msg}

def sync_run_punch(action: str = "clock_in") -> Dict[str, Any]:
    """Sync wrapper for calling from APScheduler"""
    return asyncio.run(run_punch(action))

if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "clock_in"
    print(f"Running punch test for action: {action}")
    res = sync_run_punch(action)
    print("Execution Result:", res)
