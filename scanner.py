
import os
import json
import re
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Trusted editorial discovery feeds.
# These discover leads; they do NOT certify individual promotions.
SOURCES = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
}

CATEGORIES = {
    "Airdrops & token rewards": [
        "airdrop", "token distribution", "token rewards",
        "claim rewards"
    ],
    "Giveaways & contests": [
        "giveaway", "prize pool", "crypto contest",
        "competition", "win crypto"
    ],
    "Learn & Earn": [
        "learn and earn", "learning rewards",
        "educational rewards"
    ],
    "Testnets & quests": [
        "testnet rewards", "testnet incentive",
        "ecosystem quest", "community quest"
    ],
    "Bug bounties": [
        "bug bounty", "security bounty",
        "vulnerability reward"
    ],
    "Freelancing & paid tasks": [
        "crypto freelance", "web3 jobs",
        "paid contributor", "developer bounty",
        "community contributor"
    ],
    "Exchange promotions": [
        "exchange rewards", "trading competition",
        "exchange campaign", "welcome reward"
    ],
}

BLOCKED_PHRASES = [
    "guaranteed profit",
    "double your crypto",
    "send crypto to receive",
    "send us your bitcoin",
    "seed phrase required",
    "share your private key",
    "wallet verification fee",
    "claim fee",
    "activation fee",
    "withdrawal fee to claim",
    "100% guaranteed",
    "risk free profit",
    "recover lost crypto",
]

MAX_ITEMS_PER_SOURCE = 35
MAX_ALERTS = 8


def fetch_feed(source, feed_url):
    request = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "CryptoOpportunityHunter/1.0"}
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        root = ET.fromstring(response.read())

    entries = root.findall(".//item")

    if not entries:
        entries = root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

    results = []

    for entry in entries[:MAX_ITEMS_PER_SOURCE]:
        title = entry.findtext("title") or ""
        link = entry.findtext("link") or ""

        if not link:
            element = entry.find(
                "{http://www.w3.org/2005/Atom}link"
            )
            if element is not None:
                link = element.get("href", "")

        description = (
            entry.findtext("description")
            or entry.findtext(
                "{http://www.w3.org/2005/Atom}summary"
            )
            or ""
        )

        if title and link:
            results.append({
                "source": source,
                "title": html.unescape(
                    re.sub("<[^>]+>", " ", title)
                ).strip(),
                "description": html.unescape(
                    re.sub("<[^>]+>", " ", description)
                ).strip(),
                "link": link.strip()
            })

    return results


def safe_link(url):
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
    text = (
        item["title"] + " " + item["description"]
    ).lower()

    if any(word in text for word in BLOCKED_PHRASES):
        return None

    for category, keywords in CATEGORIES.items():
        if any(keyword in text for keyword in keywords):
            return category

    return None


def discover():
    opportunities = []

    for source, url in SOURCES.items():
        try:
            items = fetch_feed(source, url)

            for item in items:
                if not safe_link(item["link"]):
                    continue

                category = classify(item)

                if category:
                    item["category"] = category
                    opportunities.append(item)

        except Exception as error:
            print(f"Feed error ({source}): {error}")

    return opportunities


def send_telegram(message):
    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(url, data=payload)

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read())

    if not result.get("ok"):
        raise RuntimeError("Telegram delivery failed")


def main():
    print("Starting Crypto Opportunity Hunter...")

    opportunities = discover()

    print(f"Discovered {len(opportunities)} matching leads.")

    if not opportunities:
        print("No matching opportunities found.")
        return

    opportunities = opportunities[:MAX_ALERTS]

    message = (
        "🚀 CRYPTO OPPORTUNITY HUNTER\n"
        "New opportunity leads discovered\n\n"
        "⚠️ DISCOVERY IS NOT VERIFICATION.\n"
        "Check the official project website and "
        "full reward conditions before participating.\n\n"
    )

    for number, item in enumerate(opportunities, 1):
        message += (
            f"{number}. {item['category']}\n"
            f"📌 {item['title'][:180]}\n"
            f"📰 Discovery source: {item['source']}\n"
            f"🔗 {item['link']}\n\n"
        )

    message += (
        "🔐 Never share seed phrases or private keys.\n"
        "Never pay to unlock a supposedly free reward.\n"
        "No opportunity is approved automatically.\n\n"
        f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    send_telegram(message)

    print("Telegram alerts sent successfully.")


if __name__ == "__main__":
    main()
