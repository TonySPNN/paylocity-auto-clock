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


def get_browser_context_config(profile: str = "macos_sequoia") -> Dict[str, Any]:
    """
    สร้าง User-Agent และ Client Hints ให้สอดคล้องกับ Duo Security OS Compliance Policy
    ป้องกันการแจ้งเตือน 'macOS update required' หรือบล็อกเพราะ OS ตกรุ่น
    """
    profile = (profile or "macos_sequoia").lower()
    
    if "windows" in profile:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        sec_platform = '"Windows"'
        platform_ver = '"15.0.0"'
        client_platform = 'Windows'
        client_arch = 'x86'
    else:  # macos_sequoia (macOS 15.7.9 Sequoia)
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        sec_platform = '"macOS"'
        platform_ver = '"15.7.9"'
        client_platform = 'macOS'
        client_arch = 'arm'

    extra_headers = {
        "Sec-CH-UA": '"Chromium";v="127", "Google Chrome";v="127", "Not-A.Brand";v="99"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": sec_platform,
        "Sec-CH-UA-Platform-Version": platform_ver,
    }

    js_override = f"""
        // 1. Mask navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

        // 2. Client Hints / UserAgentData Override
        const brands = [
            {{ brand: 'Not-A.Brand', version: '99' }},
            {{ brand: 'Chromium', version: '127' }},
            {{ brand: 'Google Chrome', version: '127' }}
        ];

        if (!navigator.userAgentData) {{
            navigator.userAgentData = {{
                brands: brands,
                mobile: false,
                platform: '{client_platform}',
                getHighEntropyValues: async function(hints) {{
                    return {{
                        brands: brands,
                        mobile: false,
                        platform: '{client_platform}',
                        platformVersion: {platform_ver},
                        architecture: '{client_arch}',
                        bitness: '64',
                        model: '',
                        uaFullVersion: '127.0.6533.17',
                        fullVersionList: brands
                    }};
                }}
            }};
        }} else {{
            const originalGet = navigator.userAgentData.getHighEntropyValues ? navigator.userAgentData.getHighEntropyValues.bind(navigator.userAgentData) : null;
            navigator.userAgentData.getHighEntropyValues = async function(hints) {{
                let base = originalGet ? await originalGet(hints) : {{}};
                return Object.assign(base, {{
                    platform: '{client_platform}',
                    platformVersion: {platform_ver},
                    architecture: '{client_arch}',
                    bitness: '64',
                    model: ''
                }});
            }};
            Object.defineProperty(navigator.userAgentData, 'platform', {{ get: () => '{client_platform}' }});
        }}
    """

    return {
        "user_agent": user_agent,
        "extra_http_headers": extra_headers,
        "js_override": js_override
    }


async def handle_duo_prompts(page) -> bool:
    """
    ตรวจจับและคลิกปุ่มต่างๆ ของ Duo Security (ทั้งใน main page และทุก iframe):
    1. ปุ่ม Trust this browser / Yes, this is my device (หลัง Duo Approve)
    2. ปุ่ม Skip for now / Remind me later / Dismiss / Update later / Not now
    3. ปุ่ม Send Push (ถ้า Duo ยังไม่ส่งให้อัตโนมัติ หรือผู้ใช้ต้องกดเลือก Duo Push)
    """
    # 1. Action: Trust this browser (สำคัญมาก หลังกด Approve บนมือถือ)
    trust_selectors = [
        "button:has-text('Yes, trust browser')",
        "button:has-text('Yes, this is my device')",
        "button:has-text('Trust this browser')",
        "button:has-text('Trust browser')",
        "button:has-text('Trust this device')",
        "button#trust-browser-button",
        "button[data-testid='trust-browser-button']",
        "button:has-text('Trust')"
    ]

    # 2. Action: Skip / Dismiss notices (กรณีมีหน้าต่างเตือนให้อัปเดตซอฟต์แวร์หรือแจ้งเตือนความปลอดภัย)
    skip_selectors = [
        "button:has-text('Skip for now')",
        "a:has-text('Skip for now')",
        "button:has-text('Remind me later')",
        "a:has-text('Remind me later')",
        "button:has-text('Update later')",
        "a:has-text('Update later')",
        "button:has-text('Dismiss')",
        "a:has-text('Dismiss')",
        "button:has-text('Not now')",
        "a:has-text('Not now')",
        "button:has-text('Skip')",
        "a:has-text('Skip')",
        "button:has-text('Continue')",
        "button:has-text('Close')",
        "button:has-text('ข้าม')",
        "button:has-text('ภายหลัง')",
        "[aria-label='Dismiss' i]",
        "[aria-label='Close' i]"
    ]

    # 3. Action: Send Duo Push
    push_selectors = [
        "button:has-text('Send Me a Push')",
        "button:has-text('Duo Push')",
        "button:has-text('Send push')",
        "button.auth-button[type='submit']"
    ]

    all_actions = [
        ("Trust Browser", trust_selectors),
        ("Skip Notice", skip_selectors),
        ("Duo Push", push_selectors),
    ]

    try:
        frames = page.frames
    except Exception:
        frames = [page]

    for label, selectors in all_actions:
        for selector in selectors:
            for frame in frames:
                try:
                    locator = frame.locator(selector)
                    count = await locator.count()
                    if count > 0:
                        btn = locator.first
                        if await btn.is_visible():
                            logger.info(f"Duo Handler: Found visible '{label}' button ({selector}), clicking...")
                            await btn.click()
                            await asyncio.sleep(1)
                            return True
                except Exception:
                    pass
    return False


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

    # แจ้งเตือนสเต็ปที่ 1 เข้า LINE ทันทีที่เริ่มงาน
    send_line_message(f"🚀 [Paylocity] เริ่มกระบวนการลงเวลา {action_th} แล้ว!\nระบบกำลังเปิดหน้าเว็บและเข้าสู่ระบบ SSO ให้ครับ...")

    # อ่านค่าการตั้งค่าจาก SQLite
    paylocity_url = get_setting("paylocity_url", "https://access.paylocity.com/").strip()
    company_id = get_setting("company_id", "").strip()
    username = get_setting("username", "").strip()
    password = get_setting("password", "").strip()
    browser_profile = get_setting("browser_profile", "macos_sequoia").strip()

    if not username or not password:
        err_msg = "ยังไม่ได้ระบุ Email หรือ Password ในเมนู Settings"
        logger.error(err_msg)
        update_history(history_id, "failed", err_msg)
        send_line_message(f"❌ [Paylocity] ลงเวลา {action_th} ไม่สำเร็จ: {err_msg}")
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
        browser_config = get_browser_context_config(browser_profile)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=browser_config["user_agent"],
            extra_http_headers=browser_config["extra_http_headers"]
        )
        await context.add_init_script(browser_config["js_override"])
        page = await context.new_page()

        try:
            logger.info("Step 1: Navigating to Paylocity login URL...")
            await page.goto(paylocity_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            # Step 2: กดปุ่ม Single Sign-On (SSO)
            logger.info("Step 2: Looking for Single Sign-On (SSO) button...")
            sso_btn = page.locator(
                "button:has-text('Single Sign-On'), a:has-text('Single Sign-On'), "
                "button:has-text('SSO'), a:has-text('SSO'), "
                "button:has-text('Log in with SSO'), a:has-text('Log in with SSO'), "
                "[aria-label*='Single Sign-On' i], [aria-label*='SSO' i], [data-testid*='sso' i]"
            )
            if await sso_btn.count() > 0 and await sso_btn.first.is_visible():
                logger.info("Found SSO button, clicking...")
                await sso_btn.first.click()
                await asyncio.sleep(2)

            # Step 3: กรอก Company ID
            logger.info("Step 3: Looking for Company ID input...")
            company_input = page.locator(
                "input#CompanyId, input[name='CompanyId'], input#companyId, input[name='companyId'], "
                "input[placeholder*='Company' i], input[aria-label*='Company' i], input[id*='Company' i]"
            )
            if await company_input.count() > 0:
                await company_input.first.wait_for(state="visible", timeout=10000)
                if company_id:
                    logger.info(f"Filling Company ID: {company_id}")
                    await company_input.first.fill(company_id)
                
                # กดปุ่ม Continue / Next / Submit ของหน้า Company ID
                comp_submit = page.locator(
                    "button:has-text('Continue'), button:has-text('Next'), button:has-text('Submit'), "
                    "input[value='Continue'], input[value='Next'], button[type='submit']"
                )
                if await comp_submit.count() > 0 and await comp_submit.first.is_visible():
                    logger.info("Submitting Company ID...")
                    await comp_submit.first.click()
                else:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(3)

            # Step 4: กรอก Email
            logger.info("Step 4: Looking for Email / Username input...")
            user_input = page.locator(
                "input[type='email'], input#Username, input[name='Username'], input[name='loginfmt'], "
                "input#identification, input[name='identifier'], input#i0116, "
                "input[placeholder*='Email' i], input[placeholder*='Username' i], input[placeholder*='User' i]"
            )
            try:
                await user_input.first.wait_for(state="visible", timeout=15000)
                logger.info(f"Filling Email: {username}")
                await user_input.first.fill(username)
            except Exception:
                logger.warning("Could not locate email input directly, searching again...")

            # ตรวจสอบว่าช่อง Password แสดงอยู่แล้วหรือไม่
            pass_input = page.locator("input#Password, input[name='Password'], input[type='password'], input[name='passwd'], input#i0118")
            pass_visible = await pass_input.count() > 0 and await pass_input.first.is_visible()

            # หากเป็นระบบ Next ก่อนรหัสผ่าน (เช่น Microsoft / Okta)
            if not pass_visible:
                next_btn = page.locator("button:has-text('Next'), input[value='Next'], button:has-text('Continue'), input#idSIButton9, button[type='submit']")
                if await next_btn.count() > 0 and await next_btn.first.is_visible():
                    logger.info("Clicking Next to reveal password field...")
                    await next_btn.first.click()
                    await asyncio.sleep(2)

            # Step 5: กรอก Password
            logger.info("Step 5: Locating Password input field...")
            pass_input = page.locator("input#Password, input[name='Password'], input[type='password'], input[name='passwd'], input#i0118")
            await pass_input.first.wait_for(state="visible", timeout=15000)
            
            # คลิกเพื่อโฟกัสที่ช่องรหัสผ่าน
            await pass_input.first.click()
            await asyncio.sleep(0.3)

            # เคลียร์ค่าที่อาจมีตกค้าง (เช่น จุดจำลอง, placeholder, หรือ autofill)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await pass_input.first.fill("")
            await asyncio.sleep(0.2)

            # พิมพ์รหัสผ่านจริงทีละตัวอักษร (delay 40ms) เพื่อให้ event ของเบราว์เซอร์รับค่าจริงแน่นอน 100%
            real_pass = str(password).strip()
            logger.info(f"Typing user real password (length: {len(real_pass)} chars)...")
            await pass_input.first.press_sequentially(real_pass, delay=40)
            await asyncio.sleep(0.5)

            # กดปุ่ม Login / Sign In
            login_btn = page.locator("button[type='submit'], input[type='submit'], button:has-text('Log In'), button:has-text('Sign In'), input[value='Login'], input[value='Sign in'], input#idSIButton9")
            if await login_btn.count() > 0 and await login_btn.first.is_visible():
                logger.info("Clicking Sign In button...")
                await login_btn.first.click()
            else:
                await page.keyboard.press("Enter")

            await asyncio.sleep(3)

            # ตรวจสอบปุ่ม 'Stay signed in?' (ถ้ามี เช่น Microsoft SSO)
            stay_signed_in = page.locator("input#idSIButton9[value='Yes'], button:has-text('Yes'), button:has-text('Stay signed in')")
            if await stay_signed_in.count() > 0 and await stay_signed_in.first.is_visible():
                logger.info("Clicking 'Yes' on Stay signed in prompt...")
                await stay_signed_in.first.click()
                await asyncio.sleep(2)

            # Step 6: เข้าสู่หน้า Duo Security 2FA
            logger.info("Step 6: Duo 2FA stage reached - sending LINE alert...")
            # ส่งสายโทรเฉพาะเมื่อเปิดใช้งานใน Settings
            if get_setting("voice_call_enabled", "0") == "1":
                make_voice_call(action_text="เข้างาน" if action == "clock_in" else "ออกงาน")

            # ส่งข้อความเตือนให้กด Duo เข้า LINE ทันที
            send_line_message(
                f"🔔 [Paylocity] กรอกรหัส SSO เรียบร้อยแล้ว!\n"
                f"👉 กรุณาเปิดแอป Duo บนมือถือของคุณ แล้วกด 'Approve / ติ๊กถูก' ได้เลยครับ (ระบบกำลังรออยู่)"
            )

            # ตรวจสอบหาปุ่ม Send Push หรือจัดการ prompt ในเบื้องต้น (ทั้งหน้าหลักและทุก iframe)
            try:
                await handle_duo_prompts(page)
            except Exception as e:
                logger.debug(f"Duo initial prompt handler note: {e}")

            # Step 7: รอผู้ใช้กดยืนยันตัวตนในแอป Duo (สูงสุด 120 วินาที)
            logger.info("Step 7: Waiting for Duo approval on user's phone (up to 120s)...")
            start_wait = time.time()
            approved = False

            while time.time() - start_wait < 120:
                current_url = page.url

                # ตรวจจับและคลิกปุ่มอัตโนมัติ (Trust this browser, Skip/Dismiss notices, Duo Push)
                try:
                    await handle_duo_prompts(page)
                except Exception as e:
                    logger.debug(f"Duo loop prompt error: {e}")

                # ตรวจสอบปุ่ม 'Stay signed in?' (ถ้ามี เช่น Microsoft SSO)
                try:
                    stay_signed_in = page.locator("input#idSIButton9[value='Yes'], button:has-text('Yes'), button:has-text('Stay signed in')")
                    if await stay_signed_in.count() > 0 and await stay_signed_in.first.is_visible():
                        logger.info("Clicking 'Yes' on Stay signed in prompt...")
                        await stay_signed_in.first.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass

                # ถ้าหลุดออกจากหน้า access.paylocity.com/duo หรือเข้าสู่ dashboard/portal แล้ว
                if "escher" in current_url.lower() or "login.paylocity.com" in current_url.lower() or "punch" in current_url.lower() or "portal" in current_url.lower() or "workforce" in current_url.lower():
                    approved = True
                    logger.info("Duo approval detected! Redirecting to Paylocity portal.")
                    # แจ้งเตือนใน LINE ว่าได้รับ Approve แล้ว
                    send_line_message(f"👍 [Paylocity] ตรวจพบการ Approve จาก Duo แล้ว!\nกำลังเข้าสู่หน้าหลักเพื่อกดลงเวลา {action_th}...")
                    break
                await asyncio.sleep(2)

            if not approved:
                # แคปเจอร์หน้าจอตอนที่รอหมดเวลาเพื่อดูว่าติดตรงไหน
                await page.screenshot(path=screenshot_path)
                err_msg = "หมดเวลาการรออนุมัติ Duo (ไม่ได้กด Approve บนมือถือภายใน 120 วินาที)"
                logger.error(err_msg)
                update_history(history_id, "failed", err_msg, f"/screenshots/{screenshot_filename}")
                send_line_message(f"❌ ลงเวลา {action_th} ล้มเหลว:\n{err_msg}")
                await browser.close()
                return {"success": False, "message": err_msg}

            # Step 8: รอให้หน้าหลัก Paylocity โหลดเสร็จสมบูรณ์
            logger.info("Step 8: Waiting for Paylocity main dashboard to load...")
            await asyncio.sleep(5)

            # ตรวจสอบเผื่อมี prompt ตกค้างหรือ popup ข้ามในหน้าหลัก
            try:
                await handle_duo_prompts(page)
            except Exception:
                pass

            post_login_skip = page.locator("button:has-text('Remind me later'), button:has-text('Dismiss'), button:has-text('Not now'), button:has-text('Close'), [aria-label='Close' i]")
            if await post_login_skip.count() > 0 and await post_login_skip.first.is_visible():
                await post_login_skip.first.click()
                await asyncio.sleep(2)

            # Step 9: เลื่อนหน้าจอหาปุ่ม Clock In หรือ Clock Out
            logger.info(f"Step 9: Scrolling and looking for {action} button...")
            # เลื่อนหน้าจอลงเล็กน้อยเพื่อให้เห็นวิดเจ็ตลงเวลา
            await page.evaluate("window.scrollTo(0, 300)")
            await asyncio.sleep(1)

            if action == "clock_in":
                punch_selectors = [
                    "button:has-text('Clock In')",
                    "a:has-text('Clock In')",
                    "button:has-text('Clock-In')",
                    "a:has-text('Clock-In')",
                    "button:has-text('Clock-in')",
                    "a:has-text('Clock-in')",
                    "[aria-label*='Clock In' i]",
                    "[aria-label*='Clock-In' i]",
                    "[title*='Clock In' i]",
                    "text=Clock In",
                    "text=Clock-In"
                ]
            else:
                punch_selectors = [
                    "button:has-text('Clock Out')",
                    "a:has-text('Clock Out')",
                    "button:has-text('Clock-Out')",
                    "a:has-text('Clock-Out')",
                    "button:has-text('Clock-out')",
                    "a:has-text('Clock-out')",
                    "[aria-label*='Clock Out' i]",
                    "[aria-label*='Clock-Out' i]",
                    "[title*='Clock Out' i]",
                    "text=Clock Out",
                    "text=Clock-Out"
                ]

            button_found = False
            for sel in punch_selectors:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    logger.info(f"Found {action} button with selector '{sel}', clicking...")
                    await loc.first.scroll_into_view_if_needed()
                    await loc.first.click()
                    button_found = True
                    break

            if not button_found:
                # ลองเลื่อนต่ออีกนิด
                await page.evaluate("window.scrollTo(0, 600)")
                await asyncio.sleep(1)
                for sel in punch_selectors:
                    loc = page.locator(sel)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        logger.info(f"Found {action} button after scroll with selector '{sel}', clicking...")
                        await loc.first.scroll_into_view_if_needed()
                        await loc.first.click()
                        button_found = True
                        break

            if not button_found:
                # ลองค้นหาปุ่ม Punch ทั่วไป
                generic_punch = page.locator("button:has-text('Punch'), [aria-label*='Punch' i], [data-testid*='punch' i]")
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
