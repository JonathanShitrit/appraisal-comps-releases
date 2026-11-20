#!/usr/bin/env python3
"""
sites.py - the work sites the browser may open in the appraiser's logged-in
profile (Task 6: "Read-tier browser"). The list is the maintainer's and ships
with the plugin: every site on it is on, and nothing in the appraiser's
folder can add one or switch one off. To add a site, add it to SITES and
release; if its login goes through another address, add that origin to
the site, or the guard blocks the login page.

guard.py checks each address Claude opens against these origins, and
launch.py passes them to the browser's own --allowed-origins too. Neither
stops a listed site from redirecting elsewhere (Playwright's docs: not a
security boundary, and it doesn't affect redirects).
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Site:
    """One work site: its name and the origins it uses (its pages, and its
    login if that lives elsewhere). How to work a site (what to read, what
    to leave alone) belongs in that site's skill, not here."""
    name: str
    origins: tuple[str, ...]


# Each origin in origin_of()'s own shape: https, lowercase, no path or
# trailing slash (a test checks).
SITES = (
    Site("OneKey MLS", ("https://www.onekeymls.com",)),
    Site("Zillow", ("https://www.zillow.com",)),
    Site("AppraisalPort", ("https://www.appraisalport.com",)),
    Site("Reggora", ("https://app.reggora.com",)),
    Site("ValueConnect", ("https://app.valueconnect.ca",)),
    Site("DwellingBlocks", ("https://auth.dwellingblocks.com", "https://app.dwellingblocks.com")),
    Site("Aivre", ("https://www.aivre.com",)),
)


def site_origins() -> list[str]:
    """Every origin of every work site, in list order."""
    return [origin for site in SITES for origin in site.origins]


def origin_of(url: str) -> str:
    """The normalized origin of url: "https://<host>", or "https://<host>:<port>"
    when the port isn't the https default (443). Always lowercased.

    Returns "" for anything that isn't an unambiguous https URL: a
    backslash (the WHATWG URL parser a real browser uses treats "\\" as a
    path separator; Python's urlparse doesn't), userinfo before the host
    (an "x@host" prefix can smuggle a different real host past a parser
    that stops at the first "@"), or a scheme other than https.
    """
    if "\\" in url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return ""
    if parsed.scheme != "https":
        return ""
    host = parsed.hostname
    if not host:
        return ""
    host = host.lower()
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is None or port == 443:
        return f"https://{host}"
    return f"https://{host}:{port}"
