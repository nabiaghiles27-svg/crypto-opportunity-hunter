import os
import re
import json
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
MAX_ALERTS = 5
MAX_HISTORY = 1500
MAX_ITEMS_PER_FEED = 40

# Search for campaign-style announcements, not general crypto news.
QUERIES = [
    '"airdrop is live" crypto',
    '"claim your airdrop" crypto',
    '"join our giveaway" crypto',
    '"official crypto giveaway" enter',
    '"win bitcoin" giveaway enter',
    '"win ethereum" giveaway enter',
    '"learn and earn" crypto campaign rewards',
    '"testnet incentive program" crypto join',
    '"web3 bug bounty program" reward submit',
    '"Stake" official giveaway enter',
    '"Solcasino" official giveaway enter',
    '"casino social giveaway" crypto enter',
]

FEEDS = [
    "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    for query in QUERIES
]

# These phrases strongly suggest editorial/news content, not a live campaign.
EDITORIAL_PHRASES = [
    "best crypto",
    "best learn",
    "best platforms",
    "top crypto",
    "top 10",
    "top 5",
    "how to",
    "what is",
    "explained",
    "beginner guide",
    "ultimate guide",
    "review",
    "price prediction",
    "market analysis",
    "market update",
    "price analysis",
    "raises $",
    "raised $",
    "funding round",
    "secures funding",
    "investment round",
    "company announces",
    "launches feature",
    "partnership announced",
    "acquisition",
    "interview",
    "opinion",
    "report",
    "weekly roundup",
    "monthly roundup",
    "year in review",
    "in 2024",
    "in 2025",
]

BLOCKED_PHRASES = [
    "seed phrase required",
    "share your private key",
    "send crypto to receive",
    "send us your bitcoin",
    "wallet verification fee",
    "claim fee",
    "activation fee",
    "pay to withdraw",
    "withdrawal fee to claim",
    "double your crypto",
    "guaranteed profit",
]

RISK_PHRASES = [
    "deposit required",
    "first deposit",
    "minimum deposit",
    "wagering requirement",
    "wager requirement",
    "playthrough requirement",
    "turnover requirement",
    "bet to qualify",
    "purchase required",
    "buy tokens to qualify",
    "stake tokens to qualify",
]

# A result must have a concrete campaign action in its TITLE.
CAMPAIGN_TITLE_PATTERNS = [
    r"\bairdrop is live\b",
    r"\bclaim your\b.*\bairdrop\b",
    r"\bclaim .* tokens\b",
    r"\bjoin our\b.*\bgiveaway\b",
    r"\benter our\b.*\bgiveaway\b",
    r"\bwin\b.*\b(giveaway|contest|prize|bitcoin|ethereum|crypto)\b",
    r"\bgiveaway\b.*\b(enter|join|ends|win)\b",
    r"\b(enter|join)\b.*\b(competition|contest|giveaway)\b",
    r"\blearn and earn\b.*\b(rewards|campaign|complete|earn)\b",
    r"\btestnet\b.*\b(rewards|incentives|points|campaign)\b",
    r"\b(bug bounty|security bounty)\b.*\b(program|reward|submit)\b",
    r"\bpaid contributor\b",
]

CASINO_TERMS = [
    "stake",
    "solcasino",
    "casino",
]

CATEGORY_RULES = [
    (
        "Casino social giveaways",
        ["stake", "solcasino", "casino"],
    ),
    (
        "Airdrops & token rewards",
        ["airdrop", "claim your", "claim tokens"],
    ),
    (
        "Giveaways & contests",
        ["giveaway", "contest", "competition", "prize"],
    ),
    (
        "Learn & Earn",
        ["learn and earn", "learning rewards"],
    ),
    (
        "Testnets & quests",
        ["testnet", "quest", "incentive program"],
    ),
    (
        "Bug bounties",
        ["bug bounty", "security bounty"],
    ),
    (
        "Paid Web3 tasks",
        ["paid contributor"],
    ),
]


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 CryptoOpportunityHunter/2.0"
        },
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(
        r"\s+",
        " ",
        html.unescape(value),
    ).strip()


def read_feed(url):
    root = ET.fromstring(fetch(url))

    entries = root.findall(".//item")

    if not entries:
        entries = root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

    results = []

    for entry in entries[:MAX_ITEMS_PER_FEED]:
        title = clean(entry.findtext("title"))

        link = entry.findtext("link") or ""

        if not link:
            atom_link = entry.find(
                "{http://www.w3.org/2005/Atom}link"
            )
            if atom_link is not None:
                link = atom_link.get("href", "")

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
    title = item["title"].lower()
    description = item["description"].lower()
    combined = title + " " + description

    # Reject editorial articles and generic news announcements.
    if any(phrase in title for phrase in EDITORIAL_PHRASES):
        return None, None

    # Reject obvious scams and offers with explicit financial risks.
    if any(phrase in combined for phrase in BLOCKED_PHRASES):
        return None, None

    if any(phrase in combined for phrase in RISK_PHRASES):
        return None, None

    # Require a specific campaign action in the headline.
    if not any(
        re.search(pattern, title, re.IGNORECASE)
        for pattern in CAMPAIGN_TITLE_PATTERNS
    ):
        return None, None

    for category, keywords in CATEGORY_RULES:
        if any(keyword in combined for keyword in keywords):
            return category, "NEEDS MANUAL REVIEW"

    return None, None


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state.get("reported"), list):
            return {"reported": []}

        return state

    except (FileNotFoundError, json.JSONDecodeError):
        return {"reported": []}


def save_state(state):
    state["reported"] = state.get(
        "reported", []
    )[-MAX_HISTORY:]

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def send_telegram(message):
    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()

    request = urllib.request.Request(
        url,
        data=payload,
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read())

    if not result.get("ok"):
        raise RuntimeError("Telegram delivery failed")


def main():
    print("Starting Crypto Opportunity Hunter v2")

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

                host = (
                    urlparse(item["link"]).hostname or ""
                ).lower()

                # X/Twitter remains disabled until a dependable
                # free discovery source is available.
                if host in (
                    "x.com",
                    "www.x.com",
                    "twitter.com",
                    "www.twitter.com",
                ):
                    continue

                category, status = classify(item)

                if not category:
                    continue

                fingerprint = hashlib.sha256(
                    item["link"].encode("utf-8")
                ).hexdigest()

                if fingerprint in reported:
                    continue

                item["category"] = category
                item["status"] = status
                item["fingerprint"] = fingerprint

                discovered.append(item)

        except Exception as error:
            failed_feeds += 1
            print("Feed error:", error)

    if successful_feeds == 0:
        raise RuntimeError("All discovery feeds failed")

    # Remove duplicate links before sending.
    unique = {}
    for item in discovered:
        unique[item["fingerprint"]] = item

    discovered = list(unique.values())[:MAX_ALERTS]

    now = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    if discovered:
        message = (
            "🚀 CRYPTO OPPORTUNITY HUNTER v2\n"
            "Possible campaign announcements\n\n"
            "⚠️ NOT VERIFIED OR ENDORSED.\n"
            "These are campaign-style search results, "
            "not confirmed rewards.\n"
            "Check the official website and full terms.\n\n"
        )

        for number, item in enumerate(discovered, 1):
            message += (
                f"{number}. {item['category']}\n"
                f"Status: {item['status']}\n"
                f"Title: {item['title'][:180]}\n"
                f"Source link: {item['link']}\n\n"
            )

            reported.add(item["fingerprint"])

        save_state({
            "reported": list(reported)
        })

    else:
        message = (
            "🟢 CRYPTO OPPORTUNITY HUNTER v2\n\n"
            "Scanner ran successfully.\n"
            "No campaign-style results passed the filters.\n\n"
        )

    message += (
        f"Feeds successful: {successful_feeds}\n"
        f"Feeds failed: {failed_feeds}\n"
        f"New leads: {len(discovered)}\n"
        f"Time: {now}\n\n"
        "🔐 Never share seed phrases or private keys.\n"
        "Never pay to unlock a free reward.\n"
        "No deposits, purchases or wagering.\n"
        "Manual approval only."
    )

    send_telegram(message)
    print("Telegram message delivered successfully.")


if __name__ == "__main__":
    main()
