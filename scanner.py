
import os
import json
import re
import html
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = ".scanner_state.json"
MAX_ALERTS = 8
MAX_HISTORY = 1000

# Free public RSS discovery. These are discovery sources,
# NOT proof that an individual offer is genuine.
QUERIES = [
    "crypto airdrop OR token giveaway",
    "crypto contest OR crypto prize giveaway",
    "crypto learn and earn rewards",
    "crypto testnet rewards OR web3 quests",
    "web3 bug bounty OR crypto security bounty",
    "web3 paid contributor jobs",
    "Stake casino giveaway OR Stake social competition",
    "Solcasino giveaway OR Solcasino competition",
    "online casino crypto giveaway no deposit",
]

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

def google_news_feed(query):
    params = urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    return "https://news.google.com/rss/search?" + params

FEEDS = NEWS_FEEDS + [google_news_feed(q) for q in QUERIES]

CATEGORIES = {
    "Airdrops & token rewards": [
        "airdrop", "token giveaway", "token distribution",
        "claim rewards"
    ],
    "Giveaways & contests": [
        "crypto giveaway", "crypto contest", "prize pool",
        "win crypto", "giveaway"
    ],
    "Learn & Earn": [
        "learn and earn", "learning rewards"
    ],
    "Testnets & quests": [
        "testnet rewards", "testnet incentive",
        "ecosystem quest", "web3 quest"
    ],
    "Bug bounties": [
        "bug bounty", "security bounty",
        "vulnerability reward"
    ],
    "Paid Web3 tasks": [
        "web3 jobs", "paid contributor",
        "developer bounty", "crypto freelance"
    ],
    "Casino social giveaways": [
        "stake giveaway", "stake social competition",
        "solcasino giveaway", "solcasino competition",
        "casino crypto giveaway"
    ],
}

BLOCKED_PHRASES = [
    "send crypto to receive",
    "send us your bitcoin",
    "seed phrase required",
    "share your private key",
    "wallet verification fee",
    "claim fee",
    "activation fee",
    "pay to withdraw",
    "withdrawal fee to claim",
    "double your crypto",
    "guaranteed profit",
]

RISK_PHRASES = [
    "deposit required", "first deposit",
    "wagering requirement", "wager requirement",
    "playthrough requirement", "turnover requirement",
    "bet to qualify", "purchase required",
]

def fetch(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CryptoOpportunityHunter/1.0"}
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()

def clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()

def read_feed(url):
    root = ET.fromstring(fetch(url))
    entries = root.findall(".//item")

    if not entries:
        entries = root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

    results = []

    for entry in entries[:40]:
        title = clean(entry.findtext("title"))
        link = entry.findtext("link") or ""

        if not link:
            element = entry.find(
                "{http://www.w3.org/2005/Atom}link"
            )
            if element is not None:
                link = element.get("href", "")

        description = clean(
            entry.findtext("description")
            or entry.findtext(
                "{http://www.w3.org/2005/Atom}summary"
            )
        )

        if title and link:
            results.append({
                "title": title,
                "description": description,
                "link": link.strip(),
            })

    return results

def valid_link(url):
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
        )
    except Exception:
        return False

def classify(item):
    text = (item["title"] + " " + item["description"]).lower()

    if any(term in text for term in BLOCKED_PHRASES):
        return None, "REJECTED"

    for category, keywords in CATEGORIES.items():
        if any(keyword in text for keyword in keywords):
            if any(term in text for term in RISK_PHRASES):
                return category, "HIGH RISK - CONDITIONS MENTIONED"

            return category, "NEEDS MANUAL REVIEW"

    return None, None

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"reported": []}

def save_state(state):
    state["reported"] = state["reported"][-MAX_HISTORY:]
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()

    request = urllib.request.Request(url, data=payload)

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read())

    if not result.get("ok"):
        raise RuntimeError("Telegram delivery failed")

def main():
    print("Starting Crypto Opportunity Hunter")

    state = load_state()
    reported = set(state.get("reported", []))
    discovered = []
    successful_feeds = 0
    failed_feeds = 0

    for feed_url in FEEDS:
        try:
            entries = read_feed(feed_url)
            successful_feeds += 1

            for item in entries:
                if not valid_link(item["link"]):
                    continue

                host = (urlparse(item["link"]).hostname or "").lower()

                # X is deliberately excluded until a dependable
                # free discovery source is available.
                if host in ("x.com", "www.x.com",
                            "twitter.com", "www.twitter.com"):
                    continue

                category, status = classify(item)

                if not category:
                    continue

                fingerprint = hashlib.sha256(
                    item["link"].encode()
                ).hexdigest()

                if fingerprint in reported:
                    continue

                item["category"] = category
                item["status"] = status
                item["fingerprint"] = fingerprint
                discovered.append(item)

        except Exception as error:
            failed_feeds += 1
            print(f"Feed error: {error}")

    if successful_feeds == 0:
        raise RuntimeError("All discovery feeds failed")

    discovered = discovered[:MAX_ALERTS]

    now = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    if discovered:
        message = (
            "🚀 CRYPTO OPPORTUNITY HUNTER\n"
            "New discovery leads\n\n"
            "⚠️ NONE OF THESE OFFERS ARE AUTOMATICALLY VERIFIED.\n"
            "Review the official source and full conditions.\n\n"
        )

        for number, item in enumerate(discovered, 1):
            message += (
                f"{number}. {item['category']}\n"
                f"Status: {item['status']}\n"
                f"Title: {item['title'][:180]}\n"
                f"Link: {item['link']}\n\n"
            )
            reported.add(item["fingerprint"])

        save_state({"reported": list(reported)})

    else:
        message = (
            "🟢 CRYPTO OPPORTUNITY HUNTER HEARTBEAT\n\n"
            "Scanner ran successfully.\n"
            "No new matching leads were found.\n\n"
        )

    message += (
        f"Feeds successful: {successful_feeds}\n"
        f"Feeds failed: {failed_feeds}\n"
        f"New leads: {len(discovered)}\n"
        f"Time: {now}\n\n"
        "🔐 Never share wallet seed phrases or private keys.\n"
        "Never pay to unlock a free reward.\n"
        "Manual approval only."
    )

    send_telegram(message)
    print("Telegram message delivered.")

if __name__ == "__main__":
    main()
