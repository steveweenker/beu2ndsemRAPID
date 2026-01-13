import asyncio
import os
import time
import aiohttp
import zipfile
import pytz
import urllib.parse
from io import BytesIO
from datetime import datetime
from typing import Optional, List, Tuple
from playwright.async_api import async_playwright

# --- CONFIGURATION ---

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

EXAM_CONFIG = {
    "ordinal_sem": "6th",
    "roman_sem": "VI",
    "session": "2025",
    "held_month": "November",
    "held_year": "2025"
}

# The Canary (First ID) determines if the site is UP
REG_LIST = [
    "22156148040", "22156148042", "22156148018", "22156148051", "22156148012",
    "22156148001", "22156148002", "22156148003", "22156148004", "22156148005",
    "22156148006", "22156148007", "22156148008", "22156148009", "22156148011",
    "22156148013", "22156148014", "22156148015", "22156148016", "22156148017",
    "22156148019", "22156148020", "22156148021", "22156148022", "22156148023",
    "22156148024", "22156148026", "22156148027", "22156148028", "22156148029",
    "22156148030", "22156148031", "22156148032", "22156148033", "22156148034",
    "22156148035", "22156148036", "22156148037", "22156148038", "22156148039",
    "22156148041", "22156148044", "22156148045", "22156148046", "22156148047",
    "22156148048", "22156148050", "22156148052", "22156148053", "23156148901",
    "23156148902", "23156148903", "23156148904", "22101148001", "22101148002",
    "22101148003", "22101148004", "22101148005", "22101148006", "22101148007",
    "22101148008", "22101148009", "22101148010", "22101148011", "22101148012",
    "22101148013", "22101148014", "22101148015", "22101148016", "22101148019",
    "22101148021", "22101148023", "22101148024", "22101148025", "22101148026",
    "22101148027", "22101148028", "22101148029", "22101148030", "22101148031",
    "22101148033", "22101148034", "22101148035", "22101148036", "22101148038",
    "22101148039", "22101148040", "22101148041", "22101148042", "23101148901",
    "23101148902", "23101148903", "23101148904", "23101148905", "23101148906",
    "23101148908", "23101148909", "23101148910", "23101148911", "23101148913",
    "23101148914", "23101148915", "23101148916", "23101148918", "23101148919",
    "22103148001", "22103148004", "22103148006", "22103148007", "22103148008",
    "23103148901", "23103148902", "23103148903", "23103148904", "23103148905",
    "23103148906", "23103148907", "23103148908", "23103148909", "23103148910",
    "23103148911", "23103148912", "23103148913", "23103148914", "23103148916",
    "23103148917", "23103148918", "23103148920", "23103148921", "23103148922",
    "23103148923", "23103148924", "23103148925", "23103148926", "23103148928",
    "23103148930", "23103148931", "23103148932", "23103148933", "23103148934",
    "22104148001", "22104148002", "22104148003", "22104148004", "22104148005",
    "22104148006", "22104148007", "22104148008", "22104148009", "22104148010",
    "22104148012", "22104148013", "22104148014", "22104148015", "23104148901",
    "23104148902", "23104148903", "23104148904", "23104148905", "23104148906",
    "23104148907", "23104148908", "23102148901", "23102148902", "23102148903",
    "23102148904", "23102148905"
]

# --- SETTINGS ---
CHECK_INTERVAL = 10      # Check every 10 seconds
CONTINUOUS_DURATION = 900 # Run for 15 minutes (matches Github Action timeout)
CONCURRENCY_LIMIT = 6    # 6 browsers at once

class DiscordMonitor:
    def __init__(self):
        self.last_status: Optional[str] = None
        self.rate_limit_remaining = 5
        self.rate_limit_reset = 0
        self.ist_timezone = pytz.timezone('Asia/Kolkata')

    def get_indian_time(self) -> str:
        utc_now = datetime.now(pytz.utc)
        ist_now = utc_now.astimezone(self.ist_timezone)
        return ist_now.strftime("%d-%m-%Y %I:%M:%S %p IST")

    # --- DISCORD UTILS ---
    async def send_discord_message(self, content: str) -> bool:
        if not DISCORD_WEBHOOK_URL: return False
        
        now = time.time()
        if self.rate_limit_remaining <= 0 and now < self.rate_limit_reset:
            await asyncio.sleep(self.rate_limit_reset - now)
            
        payload = {"content": content, "username": "BEU Monitor"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
                    self.rate_limit_remaining = int(resp.headers.get("X-RateLimit-Remaining", 5))
                    reset_after = resp.headers.get("X-RateLimit-Reset-After")
                    if reset_after: self.rate_limit_reset = now + float(reset_after)
                    
                    if resp.status == 429:
                        retry = float(resp.headers.get("retry-after", 1))
                        await asyncio.sleep(retry)
                        return await self.send_discord_message(content)
                    return resp.status in (200, 204)
        except:
            return False

    async def send_file(self, filename: str, data: BytesIO) -> bool:
        if not DISCORD_WEBHOOK_URL: return False
        form = aiohttp.FormData()
        data.seek(0)
        form.add_field("file", data, filename=filename, content_type="application/zip")
        form.add_field("username", "BEU Monitor")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(DISCORD_WEBHOOK_URL, data=form, timeout=300) as resp:
                    if resp.status == 429:
                        retry = float(resp.headers.get("retry-after", 1))
                        await asyncio.sleep(retry)
                        return await self.send_file(filename, data)
                    return resp.status in (200, 204)
        except:
            return False

    # --- SITE & DOWNLOAD LOGIC ---
    def construct_url(self, reg_no):
        params = {
            'name': f"B.Tech. {EXAM_CONFIG['ordinal_sem']} Semester Examination, {EXAM_CONFIG['session']}",
            'semester': EXAM_CONFIG['roman_sem'],
            'session': EXAM_CONFIG['session'],
            'regNo': str(reg_no),
            'exam_held': f"{EXAM_CONFIG['held_month']}/{EXAM_CONFIG['held_year']}"
        }
        return f"https://beu-bih.ac.in/result-three?{urllib.parse.urlencode(params)}"

    async def check_site_status(self) -> str:
        # Quick ping to Canary
        canary_url = self.construct_url(REG_LIST[0])
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(canary_url, timeout=10) as resp:
                    return "UP" if resp.status == 200 else "DOWN"
        except:
            return "DOWN"

    async def fetch_student_pdf(self, context, reg_no, semaphore) -> Tuple[str, Optional[bytes]]:
        async with semaphore:
            page = await context.new_page()
            try:
                await page.goto(self.construct_url(reg_no), timeout=30000)
                # If "Print" or regNo text appears, it's valid
                await page.wait_for_selector(f"text={reg_no}", timeout=10000)
                pdf = await page.pdf(format="A4", print_background=True)
                await page.close()
                return (reg_no, pdf)
            except:
                await page.close()
                return (reg_no, None)

    async def parallel_download_zip(self) -> BytesIO:
        buffer = BytesIO()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
            
            tasks = [self.fetch_student_pdf(context, reg, sem) for reg in REG_LIST]
            results = await asyncio.gather(*tasks)
            
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                count = 0
                for reg, pdf in results:
                    if pdf:
                        zf.writestr(f"{reg}.pdf", pdf)
                        count += 1
            await browser.close()
            print(f"Downloaded {count} results")
        buffer.seek(0)
        return buffer

    # --- MAIN EXECUTION (Matches Code 2 Structure) ---
    async def run(self):
        print(f"Starting Monitor for {CONTINUOUS_DURATION} seconds...")
        
        # We don't send "Monitor Started" every time because on GitHub this runs frequently.
        # We only notify if status changes or we download something.
        
        end_time = time.time() + CONTINUOUS_DURATION
        
        while time.time() < end_time:
            current_status = await self.check_site_status()
            
            # Logic: If status changed from DOWN to UP (or started UP)
            if current_status == "UP" and self.last_status != "UP":
                await self.send_discord_message("🚨 **SITE IS LIVE!** Starting Bulk Download...")
                
                # Download
                zip_data = await self.parallel_download_zip()
                
                # Upload
                zip_size = zip_data.getbuffer().nbytes / (1024*1024)
                await self.send_discord_message(f"📤 Download Complete ({zip_size:.2f} MB). Uploading...")
                
                success = await self.send_file(f"Results_{int(time.time())}.zip", zip_data)
                if success:
                    await self.send_discord_message("✅ **ZIP Uploaded Successfully!**")
                else:
                    await self.send_discord_message("❌ Upload Failed (File might be too large for Discord).")
                
                # Stop monitoring for this session to save resources
                return

            elif current_status == "DOWN" and self.last_status == "UP":
                await self.send_discord_message(f"🔴 Website went **DOWN** at {self.get_indian_time()}")

            # Update status and wait
            self.last_status = current_status
            
            # Use small sleep to allow loop to check time
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(DiscordMonitor().run())
