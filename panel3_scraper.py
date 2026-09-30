# panel3_scraper.py — bot2.py ka exact clone (scraper version)
"""
Panel 3 - Chandio Panel (mr_chandio account)
Bilkul bot2.py ki tarah kaam karta hai — same login, same XHR URL, same row parsing
Credentials env vars se lega
"""

import requests
import re
import time
import hashlib
import logging
import os
import phonenumbers
import pycountry
from bs4 import BeautifulSoup
from datetime import datetime
from collections import deque
from shared_storage import shared_storage

logger = logging.getLogger(__name__)

PANEL_ID   = "panel3"
PANEL_NAME = "Chandio Panel"

# ===== CONFIG — bilkul bot2.py jaisa =====
USERNAME   = os.getenv("PANEL3_USERNAME", "vasu_pareek")
PASSWORD   = os.getenv("PANEL3_PASSWORD", "Vasu89")

LOGIN_URL  = "http://85.195.94.50/sms/SignIn"
LOGIN_POST = "http://85.195.94.50/sms/signmein"

# Bilkul bot2.py wala exact XHR URL — kuch nahi badla
XHR_URL = (
    "http://85.195.94.50/sms/reseller/ajax/dt_reports.php"
    "?fdate1=2026-01-05%2000:00:00&fdate2=2027-01-05%2023:59:59"
    "&ftermination=&fclient=&fnum=&fcli=&fgdate=0&fgtermination=0"
    "&fgclient=0&fgnumber=0&fgcli=0&fg=0"
    "&sEcho=1&iColumns=11&sColumns=%2C%2C%2C%2C%2C%2C%2C%2C%2C%2C"
    "&iDisplayStart=0&iDisplayLength=10"   # 10 rows (bot2 mein 3 tha, thoda zyada kiya)
    "&mDataProp_0=0&sSearch_0=&bRegex_0=false&bSearchable_0=true&bSortable_0=true"
    "&mDataProp_1=1&sSearch_1=&bRegex_1=false&bSearchable_1=true&bSortable_1=true"
    "&mDataProp_2=2&sSearch_2=&bRegex_2=false&bSearchable_2=true&bSortable_2=true"
    "&mDataProp_3=3&sSearch_3=&bRegex_3=false&bSearchable_3=true&bSortable_3=true"
    "&mDataProp_4=4&sSearch_4=&bRegex_4=false&bSearchable_4=true&bSortable_4=true"
    "&mDataProp_5=5&sSearch_5=&bRegex_5=false&bSearchable_5=true&bSortable_5=true"
    "&mDataProp_6=6&sSearch_6=&bRegex_6=false&bSearchable_6=true&bSortable_6=true"
    "&mDataProp_7=7&sSearch_7=&bRegex_7=false&bSearchable_7=true&bSortable_7=true"
    "&mDataProp_8=8&sSearch_8=&bRegex_8=false&bSearchable_8=true&bSortable_8=true"
    "&mDataProp_9=9&sSearch_9=&bRegex_9=false&bSearchable_9=true&bSortable_9=true"
    "&mDataProp_10=10&sSearch_10=&bRegex_10=false&bSearchable_10=true&bSortable_10=true"
    "&sSearch=&bRegex=false&iSortCol_0=0&sSortDir_0=desc&iSortingCols=1"
    f"&_={int(time.time()*1000)}"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": LOGIN_URL
}
AJAX_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest"
}

# Session — bilkul bot2.py jaisa
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

seen_messages = set()
seen_order    = deque(maxlen=200000)  # bot2.py mein bhi 200000 tha

# ===== HELPERS — bilkul bot2.py jaisa =====

def mask_number(number: str) -> str:
    return number[:2] + "••" + number[-4:] if len(number) > 6 else number

def country_from_number(num: str):
    try:
        parsed = phonenumbers.parse("+" + num)
        region = phonenumbers.region_code_for_number(parsed)
        country = pycountry.countries.get(alpha_2=region).name
        flag = "".join(chr(127397 + ord(c)) for c in region)
        return country, flag
    except Exception:
        return "Unknown", "🌍"

def extract_otp(message: str):
    """Bilkul bot2.py wala exact logic"""
    text = message.replace("\n", " ").strip()

    m = re.search(r"\b(\d{3})[-\s](\d{3})\b", text)
    if m:
        return m.group(1) + m.group(2)

    m = re.search(r"(otp|code|pin|password|verification|код|кода)[^\d]{0,10}(\d{4,8})", text, re.I)
    if m:
        return m.group(2)

    m = re.search(r"(\d{4,8})[^\w]{0,10}(otp|code|pin|password|verification|код|кода)", text, re.I)
    if m:
        return m.group(1)

    for g in re.findall(r"\b\d{4,8}\b", text):
        if not (1900 <= int(g) <= 2099):
            return g

    return None

# ===== LOGIN — bilkul bot2.py jaisa =====

def login():
    try:
        r    = session.get(LOGIN_URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # bot2.py: soup.find(id="captcha")
        captcha = soup.find(id="captcha")
        if not captcha:
            logger.error(f"[{PANEL_ID}] Captcha not found")
            return False

        # bot2.py: a, b = map(int, re.findall(r"\d+", captcha.text))
        nums = re.findall(r"\d+", captcha.text)
        if len(nums) < 2:
            logger.error(f"[{PANEL_ID}] Captcha parse failed")
            return False

        a, b = int(nums[0]), int(nums[1])
        logger.info(f"[{PANEL_ID}] Captcha: {a}+{b}={a+b}")

        payload = {"username": USERNAME, "password": PASSWORD, "capt": str(a + b)}
        res = session.post(LOGIN_POST, data=payload, headers=HEADERS, timeout=15)

        # bot2.py: "SMSReports" in res.text or "dashboard" in res.text
        if "SMSReports" in res.text or "dashboard" in res.text:
            logger.info(f"[{PANEL_ID}] ✅ Login successful")
            return True
        else:
            logger.error(f"[{PANEL_ID}] ❌ Login failed")
            return False

    except Exception as e:
        logger.error(f"[{PANEL_ID}] Login error: {e}")
        return False

# ===== OTP LOOP — bilkul bot2.py jaisa =====

def fetch_otp_loop():
    logger.info(f"[{PANEL_ID}] 🔄 OTP loop starting...")

    while True:
        try:
            r    = session.get(XHR_URL, headers=AJAX_HEADERS, timeout=15)
            data = r.json()

            for row in data.get("aaData", []):
                # bot2.py: row[2]=number, row[0]=time, row[10]=message
                # Garbage rows skip karo
                if len(row) < 11:
                    continue
                if str(row[2]).strip() in ("0", "", "None"):
                    continue

                # bot2.py: uid = hashlib.md5(f"{row[2]}{row[0]}{row[10]}".encode()).hexdigest()
                uid = hashlib.md5(f"{row[2]}{row[0]}{row[10]}".encode()).hexdigest()

                if uid in seen_messages:
                    continue

                seen_messages.add(uid)
                seen_order.append(uid)

                # bot2.py: record = {"dt": row[0], "num": row[2], "cli": row[3], "message": row[10]}
                num     = str(row[2]).strip()
                time_   = str(row[0]).strip()
                sender  = str(row[3]).strip()
                message = str(row[10]).strip()

                country, flag = country_from_number(num)

                otp_data = {
                    'id':            uid,
                    'time':          time_,
                    'country':       country,
                    'flag':          flag,
                    'number':        num,
                    'masked_number': mask_number(num),
                    'sender':        sender,
                    'message':       message,
                    'otp':           extract_otp(message),
                    'timestamp':     datetime.now().isoformat(),
                    'panel':         PANEL_NAME
                }

                if shared_storage.add_otp(otp_data):
                    logger.info(
                        f"[{PANEL_ID}] 📨 {num} | "
                        f"{otp_data['otp'] or 'N/A'} | {sender}"
                    )

        except Exception as e:
            logger.error(f"[{PANEL_ID}] Error: {e}")
            # Error pe re-login karo (bot2.py style)
            try:
                login()
            except Exception:
                pass

        # bot2.py: time.sleep(1)
        time.sleep(1)

# ===== ENTRY POINT =====

def start_scraper():
    logger.info(f"[{PANEL_ID}] 🚀 Starting {PANEL_NAME} (mr_chandio account)...")

    for attempt in range(5):
        if login():
            fetch_otp_loop()
            return
        wait = min(30 * (attempt + 1), 300)
        logger.error(f"[{PANEL_ID}] Attempt {attempt+1} failed. Waiting {wait}s...")
        time.sleep(wait)

    logger.critical(f"[{PANEL_ID}] All login attempts failed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    start_scraper()
