import os
import re
import time
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from database import get_setting, add_history, update_history
from voice_caller import make_voice_call
from line_service import send_line_message, send_line_image, send_punch_notification

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("paylocity_bot")

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Global cancellation tracking
_cancel_event = asyncio.Event()
_active_browser = None
_active_task = None

def is_cancel_requested() -> bool:
    return _cancel_event.is_set()

async def cancel_punch() -> Dict[str, Any]:
    global _cancel_event, _active_browser, _active_task
    _cancel_event.set()
    logger.info("cancel_punch() requested by user.")
    
    from database import get_running_history, update_history
    running_h = get_running_history()
    if running_h:
        update_history(running_h["id"], "cancelled", "⏹️ การทำงานถูกยกเลิกโดยผู้ใช้งาน (Cancelled by user)")
        
    if _active_browser:
        try:
            await _active_browser.close()
        except Exception as e:
            logger.warning(f"Error closing browser on cancel: {e}")
            
    if _active_task and not _active_task.done():
        try:
            _active_task.cancel()
        except Exception:
            pass
            
    return {"success": True, "message": "ยกเลิกการทำงานเรียบร้อยแล้ว"}

async def wait_until_target_time(target_time: Optional[str], history_id: int, page) -> str:
    """
    หากมีการระบุ target_time (เช่น '06:00') บอทจะ Standby รอจนถึงเวลาเป้าหมายจึงค่อยกด
    ระหว่างรอ จะอัปเดตหน้าจอสด และตรวจสอบคำสั่งกดยกเลิก
    คืนค่า Timing Report เช่น 'ตรงเวลาเป้าหมาย 06:00 (กดเมื่อ 06:00:03)'
    """
    if not target_time:
        clicked_str = datetime.now().strftime("%H:%M:%S")
        return f"กดเมื่อ {clicked_str}"

    import pytz
    tz_str = get_setting("timezone", "Asia/Bangkok")
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.timezone("Asia/Bangkok")

    now_tz = datetime.now(tz)
    try:
        t_hour, t_minute = map(int, target_time.strip().split(":"))
    except Exception:
        clicked_str = datetime.now(tz).strftime("%H:%M:%S")
        return f"กดเมื่อ {clicked_str}"

    target_dt = now_tz.replace(hour=t_hour, minute=t_minute, second=0, microsecond=0)
    
    # ถ้าเริ่มก่อนเวลา (เช่น ล่วงหน้า 4-5 นาที) ให้ Standby รอจนถึงเป้าหมาย
    wait_sec = (target_dt - now_tz).total_seconds()
    if wait_sec > 0:
        logger.info(f"Button ready! Standby for target time {target_time} (waiting {wait_sec:.1f}s)...")
        await record_step(
            history_id, page,
            f"[สเต็ป 6/7] ⏳ เข้าสู่ระบบสำเร็จและพบปุ่มแล้ว! กำลัง Standby รอเวลากดเป้าหมาย {target_time} (เหลืออีก {int(wait_sec)} วินาที)...",
            take_screenshot=True
        )
        
        last_reported_rem = int(wait_sec)
        while datetime.now(tz) < target_dt:
            if is_cancel_requested():
                raise asyncio.CancelledError("User cancelled while waiting for target time")
            
            curr_rem = int((target_dt - datetime.now(tz)).total_seconds())
            if curr_rem > 0 and (last_reported_rem - curr_rem >= 15 or curr_rem <= 10):
                last_reported_rem = curr_rem
                await record_step(
                    history_id, page,
                    f"[สเต็ป 6/7] ⏳ Standby รอเวลากด {target_time} (เหลืออีก {curr_rem} วินาที)...",
                    take_screenshot=False
                )
            await asyncio.sleep(0.5)

    clicked_dt = datetime.now(tz)
    clicked_str = clicked_dt.strftime("%H:%M:%S")
    
    if clicked_dt.hour == t_hour and clicked_dt.minute == t_minute:
        return f"ตรงเวลาเป้าหมาย {target_time} (กดเมื่อ {clicked_str})"
    elif clicked_dt > target_dt:
        delay = int((clicked_dt - target_dt).total_seconds())
        return f"กดเมื่อ {clicked_str} (เลยเวลาเป้าหมาย {target_time} ไป {delay} วินาที)"
    else:
        return f"กดเมื่อ {clicked_str} (เป้าหมาย {target_time})"


def get_browser_context_config(profile: str = "macos_sequoia") -> Dict[str, Any]:

    """
    สร้าง User-Agent และ Client Hints ให้สอดคล้องกับ Duo Security OS & Browser Compliance Policy
    ป้องกันการแจ้งเตือน 'macOS update required' หรือ 'Chrome update required'
    """
    profile = (profile or "macos_sequoia").lower()
    chrome_major = "146"
    chrome_full = "146.0.7680.179"
    
    if "windows" in profile:
        user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
        sec_platform = '"Windows"'
        platform_ver = '"15.0.0"'
        client_platform = 'Windows'
        client_arch = 'x86'
    else:  # macos_sequoia (macOS 15.7.9 Sequoia)
        user_agent = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
        sec_platform = '"macOS"'
        platform_ver = '"15.7.9"'
        client_platform = 'macOS'
        client_arch = 'arm'

    extra_headers = {
        "Sec-CH-UA": f'"Google Chrome";v="{chrome_major}", "Chromium";v="{chrome_major}", "Not(A:Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": sec_platform,
        "Sec-CH-UA-Platform-Version": platform_ver,
        "Sec-CH-UA-Full-Version-List": f'"Google Chrome";v="{chrome_full}", "Chromium";v="{chrome_full}", "Not(A:Brand";v="24.0.0.0"',
        "Sec-CH-UA-Arch": f'"{client_arch}"',
        "Sec-CH-UA-Bitness": '"64"',
        "Sec-CH-UA-Model": '""',
    }

    js_override = f"""
        // 1. ซ่อน navigator.webdriver เพื่อไม่ให้ตรวจจับว่าเป็นบอท
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

        // 2. กำหนดชุดข้อมูล Brand & Version ของ Chrome ล่าสุด
        const modernBrands = [
            {{ brand: 'Google Chrome', version: '{chrome_major}' }},
            {{ brand: 'Chromium', version: '{chrome_major}' }},
            {{ brand: 'Not(A:Brand', version: '24' }}
        ];
        const modernFullVersions = [
            {{ brand: 'Google Chrome', version: '{chrome_full}' }},
            {{ brand: 'Chromium', version: '{chrome_full}' }},
            {{ brand: 'Not(A:Brand', version: '24.0.0.0' }}
        ];

        const mockUserAgentData = {{
            brands: modernBrands,
            mobile: false,
            platform: '{client_platform}',
            getHighEntropyValues: async function(hints) {{
                return {{
                    architecture: '{client_arch}',
                    bitness: '64',
                    brands: modernBrands,
                    fullVersionList: modernFullVersions,
                    mobile: false,
                    model: '',
                    platform: '{client_platform}',
                    platformVersion: {platform_ver},
                    uaFullVersion: '{chrome_full}'
                }};
            }},
            toJSON: function() {{
                return {{
                    brands: modernBrands,
                    mobile: false,
                    platform: '{client_platform}'
                }};
            }}
        }};

        // Override navigator.userAgentData อย่างสมบูรณ์ เพื่อกำจัด HeadlessChrome และเวอร์ชันเก่าทิ้งทั้งหมด
        Object.defineProperty(navigator, 'userAgentData', {{
            get: () => mockUserAgentData,
            configurable: true,
            enumerable: true
        }});

        // 3. จำลอง plugins ให้เหมือน Desktop Chrome ปกติ
        if (!navigator.plugins || navigator.plugins.length === 0) {{
            const mockPlugins = [
                {{ name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
                {{ name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
                {{ name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }}
            ];
            Object.defineProperty(navigator, 'plugins', {{
                get: () => mockPlugins,
                configurable: true,
                enumerable: true
            }});
        }}

        // 4. จำลอง window.chrome ให้สมบูรณ์
        if (!window.chrome) {{
            window.chrome = {{}};
        }}
        if (!window.chrome.runtime) {{
            window.chrome.runtime = {{}};
        }}
        if (!window.chrome.loadTimes) {{
            window.chrome.loadTimes = function() {{}};
        }}
        if (!window.chrome.csi) {{
            window.chrome.csi = function() {{}};
        }}
        if (!window.chrome.app) {{
            window.chrome.app = {{
                isInstalled: false,
                InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
                RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }}
            }};
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


async def record_step(history_id: Optional[int], page, message: str, take_screenshot: bool = True):
    """
    บันทึกความคืบหน้าระหว่างทำงานลง Database เพื่อให้หน้าบ้านและผู้ใช้ติดตามสถานะได้ Realtime
    พร้อมบันทึกภาพถ่ายหน้าจอสด (live_{history_id}.png) เพื่อให้เห็นหน้าจอเบราว์เซอร์จริง
    """
    if not history_id:
        return
    logger.info(f"Progress [{history_id}]: {message}")
    live_shot_path = None
    if take_screenshot and page:
        try:
            live_filename = f"live_{history_id}.png"
            live_full = os.path.join(SCREENSHOT_DIR, live_filename)
            await page.screenshot(path=live_full)
            live_shot_path = f"/screenshots/{live_filename}"
        except Exception as e:
            logger.debug(f"Live screenshot note: {e}")
    update_history(history_id, "running", message, screenshot_path=live_shot_path)


async def run_punch(action: str = "clock_in", history_id: Optional[int] = None, target_time: Optional[str] = None) -> Dict[str, Any]:
    """
    รันบอท Playwright เพื่อทำการ Clock In หรือ Clock Out
    พร้อมระบบโทรแจ้งเตือน Duo และส่งผลเข้า LINE
    """
    global _cancel_event, _active_browser, _active_task
    _cancel_event.clear()
    _active_task = asyncio.current_task()

    action_th = "เข้างาน (Clock In)" if action == "clock_in" else "ออกงาน (Clock Out)"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_filename = f"{action}_{timestamp}.png"
    screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_filename)

    target_info = f" (เวลากดเป้าหมาย {target_time})" if target_time else ""
    if not history_id:
        history_id = add_history(action, "running", f"[สเต็ป 1/7] กำลังเริ่มต้นกระบวนการลงเวลา {action_th}{target_info}...")
    else:
        update_history(history_id, "running", f"[สเต็ป 1/7] กำลังเริ่มต้นกระบวนการลงเวลา {action_th}{target_info}...")

    logger.info(f"Starting punch workflow for {action}{target_info} (History ID: {history_id})")

    # แจ้งเตือนสเต็ปที่ 1 เข้า LINE ทันทีที่เริ่มงาน
    send_punch_notification(action=action, status="started", message="เริ่มงาน", history_id=history_id, target_time=target_time)

    # อ่านค่าการตั้งค่าจาก Database
    paylocity_url = get_setting("paylocity_url", "https://access.paylocity.com/").strip()
    if "escher" in paylocity_url.lower() or "redirect_uri" in paylocity_url.lower():
        paylocity_url = "https://access.paylocity.com/"
    company_id = get_setting("company_id", "").strip()
    username = get_setting("username", "").strip()
    password = get_setting("password", "").strip()
    browser_profile = get_setting("browser_profile", "macos_sequoia").strip()

    if not username or not password:
        err_msg = "ยังไม่ได้ระบุ Email หรือ Password ในเมนู Settings"
        logger.error(err_msg)
        update_history(history_id, "failed", err_msg)
        send_punch_notification(action=action, status="failed", message=err_msg, history_id=history_id, target_time=target_time)
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
        _active_browser = browser
        browser_config = get_browser_context_config(browser_profile)

        # Configure Proxy if enabled (to route requests via Thai IP for Duo/Paylocity)
        proxy_config = None
        if get_setting("proxy_enabled", "0").strip() == "1":
            proxy_server = get_setting("proxy_server", "").strip()
            if proxy_server:
                if "://" not in proxy_server:
                    proxy_server = f"http://{proxy_server}"
                proxy_config = {"server": proxy_server}
                proxy_user = get_setting("proxy_username", "").strip()
                proxy_pass = get_setting("proxy_password", "").strip()
                if proxy_user:
                    proxy_config["username"] = proxy_user
                if proxy_pass:
                    proxy_config["password"] = proxy_pass
                logger.info(f"Using Thai Proxy for Browser: {proxy_server}")

        context_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": browser_config["user_agent"],
            "extra_http_headers": browser_config["extra_http_headers"],
            "locale": "th-TH",
            "timezone_id": "Asia/Bangkok",
            "geolocation": {"latitude": 13.7563, "longitude": 100.5018},
            "permissions": ["geolocation"]
        }
        if proxy_config:
            context_kwargs["proxy"] = proxy_config

        context = await browser.new_context(**context_kwargs)
        await context.add_init_script(browser_config["js_override"])
        page = await context.new_page()

        try:
            logger.info("Step 1: Navigating to Paylocity login URL...")
            await record_step(history_id, page, f"[สเต็ป 1/7] กำลังเปิดหน้าเว็บเข้าสู่ระบบ Paylocity SSO...", take_screenshot=True)
            await page.goto(paylocity_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)
            await record_step(history_id, page, f"[สเต็ป 1/7] หน้าเว็บ Paylocity โหลดเสร็จแล้ว กำลังค้นหาปุ่ม SSO...", take_screenshot=True)

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
                await record_step(history_id, page, "[สเต็ป 2/7] พบบริการ Single Sign-On (SSO) กำลังคลิก...", take_screenshot=True)
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
                    await record_step(history_id, page, f"[สเต็ป 3/7] กำลังกรอก Company ID ({company_id})...", take_screenshot=True)
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
                await record_step(history_id, page, f"[สเต็ป 4/7] กำลังกรอก Email บริษัท ({username})...", take_screenshot=True)
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
            await record_step(history_id, page, f"[สเต็ป 5/7] กำลังป้อนรหัสผ่านจริงทีละตัวอักษร...", take_screenshot=False)
            await pass_input.first.press_sequentially(real_pass, delay=40)
            await asyncio.sleep(0.5)

            # กดปุ่ม Login / Sign In
            login_btn = page.locator("button[type='submit'], input[type='submit'], button:has-text('Log In'), button:has-text('Sign In'), input[value='Login'], input[value='Sign in'], input#idSIButton9")
            if await login_btn.count() > 0 and await login_btn.first.is_visible():
                logger.info("Clicking Sign In button...")
                await record_step(history_id, page, "[สเต็ป 5/7] กำลังกด Sign In เข้าสู่ระบบ...", take_screenshot=True)
                await login_btn.first.click()
            else:
                await page.keyboard.press("Enter")

            await asyncio.sleep(3)

            # ตรวจสอบปุ่ม 'Stay signed in?' (ถ้ามี เช่น Microsoft SSO)
            stay_signed_in = page.locator("input#idSIButton9[value='Yes'], button:has-text('Yes'), button:has-text('Stay signed in')")
            if await stay_signed_in.count() > 0 and await stay_signed_in.first.is_visible():
                logger.info("Clicking 'Yes' on Stay signed in prompt...")
                await record_step(history_id, page, "[สเต็ป 5/7] ตอบรับหน้าต่าง Stay signed in...", take_screenshot=True)
                await stay_signed_in.first.click()
                await asyncio.sleep(2)

            # Step 6: เข้าสู่หน้า Duo Security 2FA
            logger.info("Step 6: Duo 2FA stage reached - sending LINE alert...")
            await record_step(history_id, page, "[สเต็ป 6/7] 📱 ถึงหน้า Duo 2FA แล้ว! กำลังส่ง Duo Push ไปยังโทรศัพท์ของคุณ...", take_screenshot=True)
            
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
                clicked_init = await handle_duo_prompts(page)
                if clicked_init:
                    await record_step(history_id, page, "[สเต็ป 6/7] คลิกปุ่ม Duo Push / Prompt สำเร็จ...", take_screenshot=True)
            except Exception as e:
                logger.debug(f"Duo initial prompt handler note: {e}")

            # Step 7: รอผู้ใช้กดยืนยันตัวตนในแอป Duo (สูงสุด 120 วินาที)
            logger.info("Step 7: Waiting for Duo approval on user's phone (up to 120s)...")
            start_wait = time.time()
            approved = False
            last_record_time = 0

            while time.time() - start_wait < 120:
                elapsed = int(time.time() - start_wait)
                remain = max(0, 120 - elapsed)
                current_url = page.url

                # บันทึกความคืบหน้ารวมถึงนับเวลาถอยหลังและแคปเจอร์จอสดทุก 3 วินาที
                if time.time() - last_record_time >= 3:
                    await record_step(
                        history_id,
                        page,
                        f"[สเต็ป 6/7] ⏳ กำลังรอคุณกด Approve บนมือถือในแอป Duo (เหลือเวลาอีก {remain} วินาที)...",
                        take_screenshot=True
                    )
                    last_record_time = time.time()

                # ตรวจจับและคลิกปุ่มอัตโนมัติ (Trust this browser, Skip/Dismiss notices, Duo Push)
                try:
                    clicked_duo = await handle_duo_prompts(page)
                    if clicked_duo:
                        await record_step(history_id, page, "[สเต็ป 6/7] ตรวจพบและคลิกปุ่มของ Duo (Trust/Skip/Push) เรียบร้อยแล้ว...", take_screenshot=True)
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
                is_portal_url = any(k in current_url.lower() for k in ["go.paylocity.com", "escher", "login.paylocity.com", "punch", "portal", "workforce"])
                is_post_duo = ("paylocity.com" in current_url.lower() and "duo" not in current_url.lower() and "sso" not in current_url.lower() and "login." not in current_url.lower())

                if is_portal_url or is_post_duo:
                    approved = True
                    logger.info(f"Duo approval detected! URL is {current_url}. Redirecting to Paylocity portal.")
                    await record_step(history_id, page, f"[สเต็ป 7/7] 👍 ตรวจพบการ Approve จาก Duo แล้ว! กำลังเข้าสู่หน้าหลักเพื่อลงเวลา {action_th}...", take_screenshot=True)
                    # แจ้งเตือนใน LINE ว่าได้รับ Approve แล้ว
                    send_line_message(f"👍 [Paylocity] ตรวจพบการ Approve จาก Duo แล้ว!\nกำลังเข้าสู่หน้าหลักเพื่อกดลงเวลา {action_th}...")
                    break
                await asyncio.sleep(2)

            if not approved:
                # แคปเจอร์หน้าจอตอนที่รอหมดเวลาเพื่อดูว่าติดตรงไหน
                await page.screenshot(path=screenshot_path)
                err_msg = "หมดเวลาการรออนุมัติ Duo (ไม่ได้กด Approve บนมือถือภายใน 120 วินาที)"
                logger.error(err_msg)
                update_history(history_id, "failed", f"[ล้มเหลว] ❌ {err_msg}", f"/screenshots/{screenshot_filename}")
                send_line_message(f"❌ ลงเวลา {action_th} ล้มเหลว:\n{err_msg}")
                await browser.close()
                return {"success": False, "message": err_msg}

            # Step 8: รอให้เข้าสู่หน้าหลัก Dashboard (go.paylocity.com)
            logger.info("Step 8: Waiting for redirect to Paylocity main dashboard...")
            await record_step(history_id, page, "[สเต็ป 7/7] 👍 ได้รับการ Approve จาก Duo แล้ว กำลังโหลดหน้าหลัก Paylocity...", take_screenshot=True)

            # รอ 3-4 วินาทีให้กระบวนการเปลี่ยนเส้นทาง / Token exchange เสร็จสิ้น
            await asyncio.sleep(4)

            # ตรวจสอบ URL ปัจจุบัน: ถ้าไม่ได้อยู่ที่ go.paylocity.com หรือถ้าหลุดไปหน้า Employee Timesheet / Escher
            # ให้นำทางตรงเข้าสู่ https://go.paylocity.com/ ทันที เพราะเซสชันผ่านการยืนยันตัวตนแล้ว
            current_url = page.url.lower()
            if "go.paylocity.com" not in current_url:
                logger.info(f"Current page is at '{page.url}'. Navigating directly to https://go.paylocity.com/ ...")
                await record_step(history_id, page, "[สเต็ป 7/7] กำลังเข้าสู่หน้าหลัก Dashboard (go.paylocity.com)...", take_screenshot=True)
                try:
                    await page.goto("https://go.paylocity.com/", wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    logger.warning(f"Navigate to go.paylocity.com note: {e}")

            # รอให้เนื้อหาบนหน้า Dashboard เรนเดอร์เสร็จสมบูรณ์ (ป้องกันการแคปเจอร์จอขาว)
            logger.info("Waiting for dashboard widgets (Time card) to render...")
            try:
                await page.wait_for_selector("text=/Time|Community|Clocked/i", timeout=25000)
            except Exception:
                pass

            await asyncio.sleep(2)
            await record_step(history_id, page, "[สเต็ป 7/7] หน้าหลัก Paylocity โหลดเสร็จสมบูรณ์แล้ว...", take_screenshot=True)

            # Step 9: เลื่อนหน้าจอไปยังการ์ด Time และค้นหาปุ่ม Clock in / Clock out
            logger.info(f"Step 9: Scrolling and looking for {action} button in Time widget...")
            await record_step(history_id, page, f"[สเต็ป 7/7] เลื่อนหน้าจอไปยังการ์ด Time เพื่อค้นหาปุ่ม {action_th}...", take_screenshot=True)
            
            # เลื่อนหน้าจอลงเล็กน้อยเพื่อให้เห็นวิดเจ็ตลงเวลา Time ทางขวา
            await page.evaluate("window.scrollTo(0, 320)")
            await asyncio.sleep(2)

            # กำหนดคำค้นหาเป้าหมายและคำตรงข้าม
            if action == "clock_in":
                target_text = "Clock in"
                opp_text = "Clock out"
                target_re = re.compile(r"^Clock\s*in$", re.I)
                opp_re = re.compile(r"^Clock\s*out$", re.I)
            else:
                target_text = "Clock out"
                opp_text = "Clock in"
                target_re = re.compile(r"^Clock\s*out$", re.I)
                opp_re = re.compile(r"^Clock\s*in$", re.I)

            button_found = False
            already_punched = False
            clicked_btn_text = ""
            timing_info = ""

            # วนลูปตรวจสอบสูงสุด 20 วินาทีเพื่อให้เวลาหน้าเว็บโหลดและเรนเดอร์ข้อมูลการ์ด Time
            search_start = time.time()
            while time.time() - search_start < 20:
                if is_cancel_requested():
                    raise asyncio.CancelledError("User cancelled execution")

                # 1. ค้นหาด้วย Playwright get_by_role('button') (แม่นยำที่สุด)
                target_btn = page.get_by_role("button", name=target_re)
                if await target_btn.count() > 0 and await target_btn.first.is_visible():
                    logger.info(f"Found target button '{target_text}' via get_by_role. Preparing standby or click...")
                    await target_btn.first.scroll_into_view_if_needed()
                    timing_info = await wait_until_target_time(target_time, history_id, page)
                    await record_step(history_id, page, f"[สเต็ป 7/7] พบปุ่ม {target_text} แล้ว กำลังคลิก ({timing_info})...", take_screenshot=True)
                    await asyncio.sleep(0.5)
                    await target_btn.first.click()
                    button_found = True
                    clicked_btn_text = target_text
                    break

                # 2. ค้นหาปุ่มที่อยู่ก่อนหน้าปุ่ม 'More' ในการ์ด Time (ใน container เดียวกัน)
                try:
                    more_btn = page.get_by_role("button", name=re.compile(r"^More$", re.I))
                    if await more_btn.count() > 0 and await more_btn.first.is_visible():
                        sibling_btn = more_btn.first.locator("xpath=preceding-sibling::button[1] | preceding-sibling::*[@role='button'][1]")
                        if await sibling_btn.count() > 0 and await sibling_btn.first.is_visible():
                            sib_text = (await sibling_btn.first.inner_text()).strip()
                            if target_re.match(sib_text):
                                logger.info(f"Found target button '{sib_text}' next to 'More'. Preparing standby or click...")
                                timing_info = await wait_until_target_time(target_time, history_id, page)
                                await record_step(history_id, page, f"[สเต็ป 7/7] พบปุ่ม {sib_text} ข้างปุ่ม More กำลังคลิก ({timing_info})...", take_screenshot=True)
                                await sibling_btn.first.click()
                                button_found = True
                                clicked_btn_text = sib_text
                                break
                            elif opp_re.match(sib_text):
                                logger.info(f"Found opposite button '{sib_text}' next to 'More'. User is already punched.")
                                already_punched = True
                                clicked_btn_text = sib_text
                                break
                except Exception as e:
                    logger.debug(f"Note on More sibling search: {e}")

                if already_punched:
                    break

                # 3. ค้นหาภายในกล่องการ์ด Time (จำกัดขอบเขตไม่ให้โดนเมนู Timesheet ด้านบน)
                try:
                    time_cards = page.locator("div, section, article").filter(has_text=re.compile(r"Clocked\s*(out|in)", re.I))
                    if await time_cards.count() > 0:
                        tc_btn = time_cards.last.get_by_role("button", name=target_re)
                        if await tc_btn.count() > 0 and await tc_btn.first.is_visible():
                            logger.info(f"Found button inside Time card. Preparing standby or click...")
                            timing_info = await wait_until_target_time(target_time, history_id, page)
                            await record_step(history_id, page, f"[สเต็ป 7/7] พบปุ่ม {target_text} ในกล่อง Time กำลังคลิก ({timing_info})...", take_screenshot=True)
                            await tc_btn.first.click()
                            button_found = True
                            clicked_btn_text = target_text
                            break
                except Exception as e:
                    logger.debug(f"Time card search error: {e}")

                if button_found:
                    break

                # 4. ตรวจสอบว่าปุ่มตรงข้ามแสดงอยู่แล้วหรือไม่ (เช่น ขอ Clock In แต่ปุ่มเป็น Clock Out แปลว่าเข้างานอยู่แล้ว)
                opp_btn = page.get_by_role("button", name=opp_re)
                if await opp_btn.count() > 0 and await opp_btn.first.is_visible():
                    logger.info(f"Detected opposite button '{opp_text}' is already visible! User is already in requested state.")
                    already_punched = True
                    clicked_btn_text = opp_text
                    break

                # 5. ค้นหาผ่านทุก Frame (เผื่อเป็น iframe)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        f_btn = frame.get_by_role("button", name=target_re)
                        if await f_btn.count() > 0 and await f_btn.first.is_visible():
                            logger.info(f"Found target button in iframe. Preparing standby or click...")
                            timing_info = await wait_until_target_time(target_time, history_id, page)
                            await record_step(history_id, page, f"[สเต็ป 7/7] พบปุ่ม {target_text} ในเฟรม กำลังคลิก ({timing_info})...", take_screenshot=True)
                            await f_btn.first.click()
                            button_found = True
                            clicked_btn_text = target_text
                            break
                    except Exception:
                        pass
                if button_found:
                    break


                await asyncio.sleep(1.5)

            # 6. Fallback: JavaScript DOM Traverser (เจาะจงเฉพาะ button และ [role='button'] เท่านั้น ไม่คลิกลิงก์ Timesheet)
            if not button_found and not already_punched:
                logger.info("Trying JavaScript DOM traverser fallback...")
                try:
                    js_script = """
                        (targetAction) => {
                            function queryAll(selector, root = document) {
                                let results = Array.from(root.querySelectorAll(selector));
                                const allNodes = root.querySelectorAll('*');
                                for (const node of allNodes) {
                                    if (node.shadowRoot) {
                                        results = results.concat(queryAll(selector, node.shadowRoot));
                                    }
                                }
                                return results;
                            }
                            const targetRegex = targetAction === 'clock_in' ? /^Clock\\s*in$/i : /^Clock\\s*out$/i;
                            const oppRegex = targetAction === 'clock_in' ? /^Clock\\s*out$/i : /^Clock\\s*in$/i;
                            const clickables = queryAll('button, [role="button"]');
                            for (const el of clickables) {
                                const txt = (el.innerText || el.textContent || '').trim();
                                if (txt.toLowerCase().includes('timesheet') || txt.toLowerCase().includes('activity')) continue;
                                if (targetRegex.test(txt)) {
                                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                    const target = el.closest('button, [role="button"]') || el;
                                    target.click();
                                    return { found: true, clicked: true, text: txt };
                                }
                            }
                            for (const el of clickables) {
                                const txt = (el.innerText || el.textContent || '').trim();
                                if (txt.toLowerCase().includes('timesheet') || txt.toLowerCase().includes('activity')) continue;
                                if (oppRegex.test(txt)) {
                                    return { found: true, already: true, text: txt };
                                }
                            }
                            return { found: false };
                        }
                    """
                    js_res = await page.evaluate(js_script, action)
                    if js_res and js_res.get("clicked"):
                        button_found = True
                        clicked_btn_text = js_res.get("text", target_text)
                        logger.info(f"JS fallback clicked '{clicked_btn_text}' successfully!")
                    elif js_res and js_res.get("already"):
                        already_punched = True
                        clicked_btn_text = js_res.get("text", opp_text)
                        logger.info(f"JS fallback detected already punched ('{clicked_btn_text}')!")
                except Exception as e:
                    logger.debug(f"JS fallback error: {e}")

            # ตรวจสอบหน้าต่างยืนยัน (Confirmation modal/dialog ถ้ามี)
            if button_found:
                await asyncio.sleep(2)
                confirm_selectors = [
                    "button:has-text('Submit')",
                    "button:has-text('Confirm')",
                    "button:has-text('Yes')",
                    "button:has-text('OK')",
                    "button:has-text('Save')",
                    "button:has-text('Punch')"
                ]
                for c_sel in confirm_selectors:
                    c_loc = page.locator(c_sel)
                    if await c_loc.count() > 0 and await c_loc.first.is_visible():
                        logger.info(f"Found confirmation dialog button '{c_sel}', clicking...")
                        await c_loc.first.click()
                        await asyncio.sleep(2)
                        break

            # รอ 3-4 วินาทีให้ระบบ Paylocity บันทึกและอัปเดตสถานะ
            await asyncio.sleep(3)

            # ตรวจสอบการสลับของปุ่มเพื่อยืนยันผล 100%
            verified_toggle = False
            if button_found:
                try:
                    opp_check = page.get_by_role("button", name=opp_re)
                    if await opp_check.count() > 0 and await opp_check.first.is_visible():
                        verified_toggle = True
                        logger.info(f"Verified: Button successfully toggled to '{opp_text}'!")
                except Exception:
                    pass

            # ถ่ายภาพหน้าจอ (Screenshot) บันทึกหลักฐานผลลัพธ์
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info(f"Final screenshot saved to {screenshot_path}")

            now_str = datetime.now().strftime("%H:%M:%S (%d/%m/%Y)")
            timing_str = f" ({timing_info})" if timing_info else ""
            if button_found:
                if verified_toggle:
                    success_msg = f"🎉 ลงเวลา {action_th} สำเร็จ 100%!{timing_str} (ปุ่มสลับเป็น {opp_text} เรียบร้อย) เมื่อ {now_str}"
                else:
                    success_msg = f"🎉 กดปุ่ม {action_th} เรียบร้อย{timing_str} เมื่อ {now_str}"
                status = "success"
            elif already_punched:
                opp_status_th = "เข้างาน (Clocked in)" if action == "clock_in" else "ออกงาน (Clocked out)"
                success_msg = f"ℹ️ คุณอยู่ในสถานะ {opp_status_th} อยู่แล้ว (ปุ่มบนหน้าเว็บเป็น '{clicked_btn_text}') จึงไม่จำเป็นต้องกดซ้ำ เมื่อ {now_str}"
                status = "success"
            else:
                success_msg = f"⚠️ เข้าสู่ระบบได้สำเร็จ แต่ไม่พบบาร์ลงเวลา {action_th} อัตโนมัติ (บันทึกภาพหน้าจอไว้ให้ตรวจสอบ)"
                status = "warning"

            update_history(history_id, status, success_msg, f"/screenshots/{screenshot_filename}")
            
            # ส่งแจ้งเตือนสรุปผลและลิงก์ดูหน้าจอผลลัพธ์เข้า LINE
            send_punch_notification(
                action=action,
                status=status,
                message=success_msg,
                history_id=history_id,
                screenshot_filename=screenshot_filename,
                target_time=target_time
            )

            await browser.close()
            return {"success": (status == "success"), "message": success_msg, "screenshot": f"/screenshots/{screenshot_filename}"}

        except asyncio.CancelledError:
            logger.info(f"Execution for {action} was cancelled by user.")
            err_msg = "⏹️ การทำงานถูกยกเลิกโดยผู้ใช้งาน (Cancelled by user)"
            update_history(history_id, "cancelled", err_msg, f"/screenshots/{screenshot_filename}")
            send_punch_notification(
                action=action,
                status="cancelled",
                message=err_msg,
                history_id=history_id,
                screenshot_filename=screenshot_filename,
                target_time=target_time
            )
            try:
                await browser.close()
            except Exception:
                pass
            return {"success": False, "message": err_msg}

        except Exception as e:
            logger.exception(f"Error during bot execution: {e}")
            try:
                await page.screenshot(path=screenshot_path)
            except Exception:
                pass
            
            err_msg = f"[ล้มเหลว] ❌ เกิดข้อผิดพลาดระหว่างรันบอท: {str(e)}"
            update_history(history_id, "failed", err_msg, f"/screenshots/{screenshot_filename}")
            send_punch_notification(
                action=action,
                status="failed",
                message=err_msg,
                history_id=history_id,
                screenshot_filename=screenshot_filename,
                target_time=target_time
            )
            try:
                await browser.close()
            except Exception:
                pass
            return {"success": False, "message": err_msg}

        finally:
            _active_browser = None
            _active_task = None

def sync_run_punch(action: str = "clock_in", target_time: Optional[str] = None) -> Dict[str, Any]:
    """Sync wrapper for calling from APScheduler"""
    return asyncio.run(run_punch(action=action, target_time=target_time))


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "clock_in"
    print(f"Running punch test for action: {action}")
    res = sync_run_punch(action)
    print("Execution Result:", res)
