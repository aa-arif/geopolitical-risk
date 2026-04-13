"""
Article relevance filter. Determines whether an article is relevant
to geopolitical risk assessment for a target country.

Uses a lightweight keyword + heuristic approach first, with optional
LLM fallback for ambiguous cases.
"""

import re

# Keywords that indicate geopolitical risk relevance
RISK_KEYWORDS = [
    "protest", "protests", "demonstration", "riot", "unrest",
    "coup", "military takeover", "martial law", "state of emergency",
    "election", "electoral", "opposition", "crackdown", "arrested",
    "killed", "bombing", "attack", "insurgent", "insurgency",
    "separatist", "militant", "terrorism", "terrorist",
    "sanctions", "embargo", "conflict", "war", "invasion",
    "regime change", "impeachment", "assassination", "resign",
    "strike", "general strike", "blockade", "curfew",
    "ethnic violence", "sectarian", "clashes", "armed",
    "refugees", "displacement", "humanitarian crisis",
    "economic crisis", "inflation", "default", "IMF",
    "military deployment", "border", "territorial",
]

# Keywords that indicate low relevance (sports, entertainment, etc.)
NOISE_KEYWORDS = [
    # Sports
    "cricket", "football match", "soccer match", "basketball",
    "super lig", "premier league", "world cup qualifier",
    # Entertainment
    "bollywood", "nollywood", "turkish drama", "celebrity",
    "entertainment", "reality show", "music award",
    # Lifestyle
    "recipe", "tourism", "travel guide", "hotel review",
    "weather forecast", "horoscope", "fashion week",
]


def is_relevant(title: str, text: str, country_config: dict) -> bool:
    """
    Determine if an article is relevant to geopolitical risk assessment.

    Uses keyword matching with country-specific context.
    Returns True if the article should be processed further.
    """
    combined = (title + " " + text).lower()

    # Quick reject: noise keywords dominate
    noise_count = sum(1 for kw in NOISE_KEYWORDS if kw in combined)
    if noise_count >= 2:
        return False

    # Check for risk keywords
    risk_count = sum(1 for kw in RISK_KEYWORDS if kw in combined)
    if risk_count >= 2:
        return True

    # Check for country-specific key actors
    for actor in country_config.get("key_actors", []):
        # Match on last name or organization name
        actor_parts = actor.lower().split()
        for part in actor_parts:
            if len(part) > 3 and part in combined:
                if risk_count >= 1:
                    return True

    # Single risk keyword + country name mentioned
    country_name = country_config["name"].lower()
    if risk_count >= 1 and country_name in combined:
        return True

    return False


TIER_1_DOMAINS = [
    "crisisgroup.org", "csis.org", "brookings.edu", "rand.org",
    "chathamhouse.org", "iiss.org", "sipri.org", "acleddata.com",
    "journals.sagepub.com", "academic.oup.com", "jstor.org",
]
TIER_2_DOMAINS = [
    "reuters.com", "bbc.com", "bbc.co.uk", "aljazeera.com",
    "apnews.com", "france24.com", "dw.com", "theguardian.com",
    "nytimes.com", "washingtonpost.com", "economist.com",
    "foreignaffairs.com", "foreignpolicy.com",
    "dawn.com", "geo.tv", "thedailystar.net", "rappler.com",
    "punchng.com", "premiumtimesng.com", "hurriyetdailynews.com",
    "inquirer.net", "dhakatribune.com", "tribune.com.pk",
]


def compute_reliability_tier(source_url: str) -> int:
    """
    Estimate source reliability tier from URL.

    Tier 1: Institutional analysis (ICG, CSIS, academic)
    Tier 2: Major news outlets
    Tier 3: Opinion, social media, blogs
    """
    url_lower = source_url.lower() if source_url else ""

    for domain in TIER_1_DOMAINS:
        if domain in url_lower:
            return 1

    for domain in TIER_2_DOMAINS:
        if domain in url_lower:
            return 2

    return 3
