"""
Telegram Proxy Auto-Poster
---------------------------
Yeh script kya karti hai:
1. Kai saari PUBLIC/open-source proxy list repos se proxies uthati hai
   (TheSpeedX/PROXY-List, ShiftyTR/Proxy-List, jetkai/proxy-list waghera)
2. Har proxy ko quickly test karti hai ke woh kaam kar rahi hai ya nahi
3. Jo proxies kaam kar rahi hain, unme se ek naya (pehle post na hui hui)
   proxy chunti hai
4. Usay aapke Telegram channel par post kar deti hai
5. Post ki hui proxy ko posted_proxies.json me save kar deti hai taake
   dobara wahi proxy repeat na ho

Yeh script GitHub Actions se har ~20-30 minute baad automatically chalti hai
(schedule .github/workflows/post_proxy.yml file me hai).

NOTE: GitHub Actions ka cron schedule "exact to the second" guarantee nahi
karta — kabhi kabhi 1-5 minute ka farq aa sakta hai. Yeh GitHub ki taraf se
hai, hamare control me nahi. Isliye "bilkul 20 min, phir bilkul 5 min" wala
precise timing GitHub Actions ke free tier par 100% guaranteed nahi hai —
lekin "roughly har X minute baad" wala automation bilkul reliable chalta hai.
"""

import os
import json
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- CONFIG ----------

# Public/open-source proxy list sources (raw text files, IP:PORT format)
PROXY_SOURCES = {
    "http": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ],
    "socks4": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    ],
}

# Kitni proxies parallel test karni hain (zyada = tez, lekin GitHub Actions
# runner par zyada CPU/network load)
MAX_WORKERS = 40

# Test karne ke liye kitni proxies max uthayen (poori list lakhon lines ki
# ho sakti hai, isliye har source se sirf itni hi randomly test karenge)
SAMPLE_PER_SOURCE = 60

# Ek run me kitni working proxies post karni hain
PROXIES_PER_RUN = 1

# Test URL — proxy ke through yeh URL khul jaye to proxy "working" maani jayegi
TEST_URL = "http://httpbin.org/ip"
TEST_TIMEOUT = 6  # seconds

POSTED_FILE = os.path.join(os.path.dirname(__file__), "posted_proxies.json")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # e.g. @yourchannel or -100xxxxxxxxxx


# ---------- STEP 1: Proxies fetch karna ----------

def fetch_proxy_list(url: str) -> list[str]:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
        return lines
    except Exception as e:
        print(f"[WARN] {url} se fetch nahi ho saka: {e}")
        return []


def collect_candidate_proxies() -> list[tuple[str, str]]:
    """Returns list of (ip:port, protocol) tuples, deduplicated."""
    all_candidates = []
    for protocol, urls in PROXY_SOURCES.items():
        combined = []
        for url in urls:
            combined.extend(fetch_proxy_list(url))
        combined = list(set(combined))
        random.shuffle(combined)
        sample = combined[:SAMPLE_PER_SOURCE]
        all_candidates.extend([(proxy, protocol) for proxy in sample])
    random.shuffle(all_candidates)
    return all_candidates


# ---------- STEP 2: Proxy check karna (working hai ya nahi) ----------

def check_proxy(proxy_and_protocol: tuple[str, str]) -> tuple[str, str] | None:
    proxy_str, protocol = proxy_and_protocol
    proxy_url = f"{protocol}://{proxy_str}"
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        resp = requests.get(TEST_URL, proxies=proxies, timeout=TEST_TIMEOUT)
        if resp.status_code == 200:
            return (proxy_str, protocol)
    except Exception:
        pass
    return None


def find_working_proxies(candidates: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    working = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_proxy, c): c for c in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                working.append(result)
                print(f"[OK] Working proxy mili: {result[0]} ({result[1]})")
            if len(working) >= limit:
                break
    return working


# ---------- STEP 3: Posted history load/save ----------

def load_posted() -> set[str]:
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("posted", []))
    return set()


def save_posted(posted: set[str]):
    # Sirf last 500 rakho taake file bohot badi na ho jaye
    trimmed = list(posted)[-500:]
    with open(POSTED_FILE, "w") as f:
        json.dump({"posted": trimmed}, f, indent=2)


# ---------- STEP 4: Telegram par post karna ----------

def post_to_telegram(proxy_str: str, protocol: str):
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN ya TELEGRAM_CHANNEL_ID set nahi hai. "
            "GitHub repo Secrets me yeh dono add karein."
        )

    text = (
        f"🔌 *New Free Proxy*\n\n"
        f"`{proxy_str}`\n"
        f"Type: `{protocol.upper()}`\n\n"
        f"⚠️ Free proxies kuch hi ghanton me expire ho jaati hain."
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, data=payload, timeout=15)
    resp.raise_for_status()
    print(f"[POSTED] {proxy_str} channel par post ho gayi.")


# ---------- MAIN ----------

def main():
    posted = load_posted()

    print("Proxies fetch ho rahi hain public sources se...")
    candidates = collect_candidate_proxies()

    # Jo proxies pehle post ho chuki hain unhe candidates se nikaal do
    candidates = [c for c in candidates if c[0] not in posted]
    print(f"Total {len(candidates)} naye candidates test ke liye.")

    working = find_working_proxies(candidates, limit=PROXIES_PER_RUN)

    if not working:
        print("Is run me koi working proxy nahi mili. Agla run try karega.")
        return

    for proxy_str, protocol in working:
        post_to_telegram(proxy_str, protocol)
        posted.add(proxy_str)

    save_posted(posted)
    print("Done.")


if __name__ == "__main__":
    main()
