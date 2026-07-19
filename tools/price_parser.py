"""
Price parsing and normalization utilities.
Extracts structured price data from raw text/HTML snippets.
"""
from __future__ import annotations
import re


UNIT_PATTERNS = {
    "per_lb": [r"per\s*lb", r"/\s*lb", r"per\s*pound", r"by\s*the\s*pound"],
    "per_oz": [r"per\s*oz", r"/\s*oz", r"per\s*ounce"],
    "bunch": [r"bunch", r"bundle", r"bouquet"],
    "pint": [r"pint", r"pt\.?"],
    "package": [r"package", r"pkg\.?", r"bag", r"pack", r"container"],
    "each": [r"each", r"ea\.?", r"per\s*item"],
}

DEAL_PATTERNS = {
    "percent_off": [r"(\d+)\s*%\s*off", r"save\s+(\d+)\s*%"],
    "buy_x_get_y": [r"buy\s+(\d+)\s+get\s+(\d+)", r"b\d+g\d+"],
    "weekly_sale": [r"weekly\s+sale", r"this\s+week", r"weekly\s+ad", r"week\s+only"],
    "membership": [r"member\s+price", r"with\s+card", r"club\s+price", r"prime\s+member",
                   r"target\s+circle", r"costco", r"sam.?s\s+club"],
    "digital_coupon": [r"digital\s+coupon", r"clip\s+coupon", r"app\s+(only|offer)"],
    "clearance": [r"clearance", r"marked\s+down", r"final\s+sale"],
}


def extract_prices(text: str) -> list[float]:
    """Extract all dollar prices from text."""
    patterns = [
        r"\$\s*(\d+\.\d{1,2})",   # $1.99
        r"\$\s*(\d+)(?!\.?\d)",    # $2 — but not the "$3" prefix of "$3.99"
        r"(\d+\.\d{1,2})\s*(?:dollars?|USD)",  # 1.99 dollars
        r"(\d+)\s*for\s*\$\s*(\d+\.\d{1,2})",  # 3 for $5.00
    ]
    prices = []
    for p in patterns:
        matches = re.findall(p, text, re.IGNORECASE)
        for m in matches:
            try:
                if isinstance(m, tuple):
                    prices.append(float(m[-1]))
                else:
                    prices.append(float(m))
            except ValueError:
                continue
    return sorted(set(prices))


def detect_unit(text: str) -> str:
    """Detect the unit type from text."""
    text_lower = text.lower()
    for unit, patterns in UNIT_PATTERNS.items():
        for p in patterns:
            if re.search(p, text_lower):
                return unit
    return "each"


def detect_deal_type(text: str) -> tuple[str, float | None]:
    """
    Returns (deal_type, percent_off_value).
    percent_off_value is None for non-percentage deals.
    """
    text_lower = text.lower()
    for deal_type, patterns in DEAL_PATTERNS.items():
        for p in patterns:
            m = re.search(p, text_lower)
            if m:
                pct = None
                if deal_type == "percent_off" and m.groups():
                    try:
                        pct = float(m.group(1))
                    except (ValueError, IndexError):
                        pass
                return deal_type, pct
    return "regular", None


def detect_membership(text: str) -> str | None:
    """Return the membership name if text mentions a membership deal."""
    membership_map = {
        r"amazon\s*prime|prime\s*member": "Amazon Prime",
        r"target\s*circle": "Target Circle",
        r"kroger\s*plus|kroger\s*card": "Kroger Plus Card",
        r"safeway\s*club|just\s*for\s*u": "Safeway Club Card",
        r"costco": "Costco Membership",
        r"sam.?s\s*club": "Sam's Club Membership",
        r"vons\s*club": "Vons Club Card",
        r"ralph.?s\s*card": "Ralphs Rewards Card",
        r"publix\s*digital": "Publix Digital Coupons",
        r"heb|central\s*market": "H-E-B Card",
        r"instacart\+": "Instacart+",
    }
    text_lower = text.lower()
    for pattern, name in membership_map.items():
        if re.search(pattern, text_lower):
            return name
    return None


def parse_price_from_snippets(snippets: list[dict], item_name: str, store_name: str) -> dict | None:
    """
    Parse price information from a list of search result snippets.
    Returns a structured price dict or None.
    """
    combined_text = " ".join(
        f"{s.get('title', '')} {s.get('snippet', '')}" for s in snippets
    )

    prices = extract_prices(combined_text)
    if not prices:
        return None

    # Heuristic: pick the most likely "real" price (not a zip code, not $0)
    valid_prices = [p for p in prices if 0.1 <= p <= 50.0]
    if not valid_prices:
        return None

    # Prices are sorted ascending — highest is likely the regular/shelf price,
    # lowest is likely the sale price (if multiple prices found)
    regular_price = valid_prices[-1]
    sale_price = valid_prices[0] if len(valid_prices) > 1 and valid_prices[0] < regular_price else None

    unit = detect_unit(combined_text)
    deal_type, pct_off = detect_deal_type(combined_text)
    membership = detect_membership(combined_text)

    if membership:
        deal_type = "membership"

    return {
        "regular_price": regular_price,
        "sale_price": sale_price,
        "unit": unit,
        "deal_type": deal_type,
        "percent_off": pct_off,
        "membership_required": membership,
        "source_url": snippets[0].get("url") if snippets else None,
    }
