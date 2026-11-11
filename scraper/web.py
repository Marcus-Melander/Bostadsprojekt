"""Everything the finder fetches from the web goes through Scrapling (https://github.com/d4vinci/Scrapling).

    get(url)        a file or page without a browser (annual reports, sitemaps, robots.txt, floor plans), with
                    Scrapling's Fetcher: Chrome's TLS fingerprint and a browser's headers, three tries
    Browser(show)   a Chrome window for the agents' pages, from Scrapling's StealthySession on your installed
                    Chrome; new_page() gives the page the finder drives, as before
    ERRORS          what a page that fails to load raises, from either browser library

Scrapling's Cloudflare solving and proxies are left off: a site that blocks us stays unread for the day.
"""
import logging

from playwright.sync_api import Error as _PlaywrightError
from patchright.sync_api import Error as _PatchrightError
from scrapling.fetchers import Fetcher, StealthySession

ERRORS = (_PlaywrightError, _PatchrightError)
logging.getLogger("scrapling").setLevel(logging.WARNING)  # it logs every fetch; the finder logs what matters
LOCALE, TZ = "sv-SE", "Europe/Stockholm"


class HTTPError(Exception):
    """The site answered, with an error status. Never retried: a 403 or 429 means no for today."""
    def __init__(self, url, status):
        super().__init__(f"HTTP Error {status} for {url}")
        self.url, self.status, self.code = url, status, status


def get(url, timeout=40, headers=None):
    """The body of `url` as bytes; raises HTTPError for a 4xx or 5xx answer."""
    r = Fetcher.get(url, timeout=timeout, headers=headers or {}, follow_redirects=True)
    if r.status >= 400:
        raise HTTPError(url, r.status)
    return r.body


def text(url, timeout=40):
    return get(url, timeout).decode("utf-8", "replace")


class Browser:
    """One Chrome for one agent's run (or the follow-ups), with the finder's own page handling."""
    def __init__(self, show=False):
        try:  # your installed Google Chrome; Scrapling's own Chromium as a fallback
            self.session = StealthySession(real_chrome=True, headless=not show, locale=LOCALE, timezone_id=TZ)
            self.session.start()
        except Exception:
            self.session = StealthySession(headless=not show, locale=LOCALE, timezone_id=TZ)
            self.session.start()

    def new_page(self, **_):
        return self.session.context.new_page()

    def close(self):  # also when the finder closed it already
        if self.session:
            self.session.close()
            self.session = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
