import os
import re
import json
import time
import hashlib
import html
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = ".scanner_state.json"
MAX_PAGE_BYTES = 2_000_000
MAX_CAMPAIGN_PAGES = 35
REQUEST_TIMEOUT = 20

SOURCES = [
    {
        "name": "Stake",
        "listing": "https://stake.com/promotions/category/community",
        "domain": "stake.com",
        "campaign_path": "/promotions/promotion/",
    },
    {
        "name": "Solcasino.io",
        "listing": (
            "https://docs.solcasino.io/"
            "promotions-and-collaborations/promotions"
        ),
        "domain": "docs.solcasino.io",
        "campaign_path": "/promotions-and-collaborations/promotions/",
    },
]

CAMPAIGN_WORDS = re.compile(
    r"\b(giveaway|giveaways|contest|competition|raffle|"
    r"airdrop|bounty|reward|rewards|quest|prize|"
    r"learn and earn|challenge)\b",
    re.I,
)

# These indicate a financial or gambling requirement.
RISK_PATTERNS = [
    r"\bdeposit\s+required\b",
    r"\bminimum\s+deposit\b",
    r"\bfirst\s+deposit\b",
    r"\bmake\s+a\s+deposit\b",
    r"\bdeposit\s+to\s+(?:claim|qualify|participate|unlock)\b",
    r"\bmust\s+deposit\b",
    r"\b wager(?:ed|ing)?\s+(?:at least|of|to qualify|required)\b",
    r"\bmust\s+wager\b",
    r"\bminimum\s+wager\b",
    r"\bqualifying\s+wager\b",
    r"\bturnover\s+requirement\b",
    r"\bwagering\s+requirement\b",
    r"\bplaythrough\s+requirement\b",
    r"\bmust\s+bet\b",
    r"\bplace\s+(?:a|your)\s+bet\b",
    r"\bVIP\s+(?:level|status|rank)\s+(?:required|needed|only)\b",
    r"\bBronze\s+VIP\b",
    r"\bKYC\s+(?:level|verification)\s+(?:required|needed)\b",
    r"\bpay(?:ment)?\s+required\b",
    r"\bpurchase\s+required\b",
    r"\bbuy\s+(?:a|the|any)\s+(?:product|token|item)\b",
    r"\bentry\s+fee\b",
    r"\bclaim\s+fee\b",
    r"\bwithdrawal\s+fee\b",
    r"\bstake\s+(?:your\s+)?(?:tokens|coins|crypto)\b",
    r"\brakeback\b",
    r"\bcasino\s+games?\s+played\b",
    r"\bplay\s+through\b",
]

RISK_REGEX = [re.compile(p, re.I) for p in RISK_PATTERNS]

SCAM_PATTERNS = [
    "seed phrase",
    "recovery phrase",
    "private key",
    "send crypto to",
    "send funds to",
    "double your crypto",
    "guaranteed profit",
    "connect your wallet to claim",
    "wallet synchronization",
]

# A campaign must explicitly explain that participation is free.
FREE_PATTERNS = [
    r"\bno purchase necessary\b",
    r"\bno purchase or payment required\b",
    r"\bno deposit required\b",
    r"\bno wagering required\b",
    r"\bno wager required\b",
    r"\bwithout (?:making )?a deposit\b",
    r"\bfree to enter\b",
    r"\bfree entry\b",
    r"\bno entry fee\b",
    r"\bno payment required\b",
]

FREE_REGEX = [re.compile(p, re.I) for p in FREE_PATTERNS]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.links = []
        self._anchor_text = []
        self._anchor_href = None
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
            return

        if self._skip_depth:
            return

        if tag == "a":
            self._anchor_href = attrs.get("href")
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            if self._skip_depth:
                self._skip_depth -= 1
            return

        if self._skip_depth:
            return

        if tag == "a" and self._anchor_href:
            label = " ".join(self._anchor_text).strip()
            self.links.append((self._anchor_href, label))
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data):
        if self._skip_depth:
            return

        value = data.strip()
        if not value:
            return

        self.text_parts.append(value)

        if self._anchor_href:
            self._anchor_text.append(value)


def fetch_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; "
                "CryptoOpportunityHunter/4.0)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")

        if "text/html" not in content_type.lower():
            raise ValueError("Response is not HTML")

        body = response.read(MAX_PAGE_BYTES + 1)

        if len(body) > MAX_PAGE_BYTES:
            raise ValueError("Page exceeds size limit")

    parsed_url = urlparse(final_url)

    if parsed_url.scheme != "https":
        raise ValueError("Non-HTTPS destination")

    return final_url, body.decode("utf-8", errors="replace")


def extract_page(url):
    final_url, source = fetch_page(url)

    parser = PageParser()
    parser.feed(source)

    page_text = html.unescape(" ".join(parser.text_parts))
    page_text = re.sub(r"\s+", " ", page_text).strip()

    return {
        "url": final_url,
        "text": page_text,
        "links": parser.links,
    }


def allowed_domain(url, expected_domain):
    host = (urlparse(url).hostname or "").lower()

    return (
        host == expected_domain
        or host.endswith("." + expected_domain)
    )


def valid_campaign_link(url, source):
    parsed = urlparse(url)

    if parsed.scheme != "https":
        return False

    if not allowed_domain(url, source["domain"]):
        return False

    if source["campaign_path"] not in parsed.path:
        return False

    return True


def has_risk_requirement(text):
    for pattern in RISK_REGEX:
        if pattern.search(text):
            return True

    lowered = text.lower()

    for phrase in SCAM_PATTERNS:
        if phrase in lowered:
            return True

    return False


def explicitly_free(text):
    return any(pattern.search(text) for pattern in FREE_REGEX)


def campaign_rejection_reason(text):
    if has_risk_requirement(text):
        return "Financial, gambling, or unsafe requirement detected"

    if not explicitly_free(text):
        return "No explicit free-entry confirmation found"

    if len(text) < 250:
        return "Insufficient campaign information"

    return None


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            return {"seen": []}

        if not isinstance(state.get("seen"), list):
            state["seen"] = []

        return state

    except (OSError, json.JSONDecodeError):
        return {"seen": []}


def save_state(state):
    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

    os.replace(temporary, STATE_FILE)


def fingerprint(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def send_telegram(message):
    import urllib.parse

    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = Request(url, data=payload, method="POST")

    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")


def shorten(value, limit=500):
    value = re.sub(r"\s+", " ", value).strip()

    if len(value) <= limit:
        return value

    return value[:limit].rsplit(" ", 1)[0] + "..."


def scan_source(source):
    stats = {
        "pages_checked": 0,
        "pages_failed": 0,
        "candidates": 0,
        "rejected": 0,
        "leads": [],
    }

    try:
        listing = extract_page(source["listing"])
        stats["pages_checked"] += 1
    except Exception:
        stats["pages_failed"] += 1
        return stats

    if not allowed_domain(listing["url"], source["domain"]):
        stats["pages_failed"] += 1
        return stats

    campaign_links = {}

    for href, label in listing["links"]:
        absolute_url = urljoin(listing["url"], href)

        if not valid_campaign_link(absolute_url, source):
            continue

        if absolute_url not in campaign_links:
            campaign_links[absolute_url] = label

    for campaign_url, listing_title in list(
        campaign_links.items()
    )[:MAX_CAMPAIGN_PAGES]:

        time.sleep(0.5)

        try:
            page = extract_page(campaign_url)
            stats["pages_checked"] += 1

            if not allowed_domain(page["url"], source["domain"]):
                stats["rejected"] += 1
                continue

            text = page["text"]
            title = listing_title or campaign_url

            stats["candidates"] += 1

            if not CAMPAIGN_WORDS.search(title + " " + text):
                stats["rejected"] += 1
                continue

            reason = campaign_rejection_reason(text)

            if reason:
                stats["rejected"] += 1
                continue

            stats["leads"].append({
                "source": source["name"],
                "title": shorten(title, 180),
                "url": page["url"],
                "details": shorten(text, 450),
            })

        except Exception:
            stats["pages_failed"] += 1

    return stats


def main():
    started = datetime.now(timezone.utc)
    state = load_state()

    seen = set(state.get("seen", []))
    all_leads = []

    totals = {
        "pages_checked": 0,
        "pages_failed": 0,
        "candidates": 0,
        "rejected": 0,
    }

    for source in SOURCES:
        stats = scan_source(source)

        for key in totals:
            totals[key] += stats[key]

        all_leads.extend(stats["leads"])

    new_leads = []

    for lead in all_leads:
        lead_id = fingerprint(lead["url"])

        if lead_id in seen:
            continue

        seen.add(lead_id)
        new_leads.append(lead)

    state["seen"] = list(seen)[-3000:]
    state["last_scan"] = started.isoformat()
    save_state(state)

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    if new_leads:
        for lead in new_leads:
            message = (
                "🟡 CRYPTO OPPORTUNITY HUNTER v4\n\n"
                "POTENTIAL FREE CAMPAIGN\n"
                "MANUAL REVIEW REQUIRED\n\n"
                f"Source: {lead['source']}\n"
                f"Campaign: {lead['title']}\n\n"
                f"Details: {lead['details']}\n\n"
                f"Official page: {lead['url']}\n\n"
                "The scanner found explicit free-entry wording "
                "and no detected financial requirement. "
                "Check the complete terms before participating. "
                "Never pay to claim or share wallet secrets."
            )

            send_telegram(message)

    summary = (
        "🔎 CRYPTO OPPORTUNITY HUNTER v4\n\n"
        f"Official source pages checked: "
        f"{totals['pages_checked']}\n"
        f"Page errors: {totals['pages_failed']}\n"
        f"Campaign pages inspected: {totals['candidates']}\n"
        f"Rejected by safety filters: {totals['rejected']}\n"
        f"New potential campaigns: {len(new_leads)}\n\n"
        f"Time: {timestamp}\n\n"
        "No deposits, purchases, wagering, staking, "
        "or paid claims are permitted."
    )

    send_telegram(summary)


if __name__ == "__main__":
    main()
