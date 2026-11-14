import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .errors import FetchError
from .util import host_allowed

USER_AGENT = "ChinaBusinessDigest/0.1 (personal educational digest; low-volume)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FetchResponse:
    url: str
    status: int
    body: bytes
    content_type: str = ""
    from_cache: bool = False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts, robots_checker=None):
        self.allowed_hosts = allowed_hosts
        self.robots_checker = robots_checker

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        target = urljoin(request.full_url, new_url)
        if not host_allowed(target, self.allowed_hosts):
            raise FetchError(f"Redirect left the source allowlist: {target}")
        if self.robots_checker and not self.robots_checker(target):
            raise FetchError(f"robots.txt does not allow the redirect target: {target}")
        return super().redirect_request(request, file_pointer, code, message, headers, target)


class Fetcher:
    def __init__(self, state, timeout=20, max_bytes=2_000_000, sleep=time.sleep):
        self.state = state
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.sleep = sleep
        # Each origin stores a parsed rule set, an allow-all sentinel (None), or False.
        self.robots = {}

    def _opener(self, allowed_hosts, robots_checker=None):
        return urllib.request.build_opener(_SafeRedirect(allowed_hosts, robots_checker))

    def _read_limited(self, response):
        body = response.read(self.max_bytes + 1)
        if len(body) > self.max_bytes:
            raise FetchError(f"Response exceeded {self.max_bytes} bytes: {response.url}")
        return body

    def _request(self, url, allowed_hosts, use_cache=True, check_robots=True):
        cached = self.state.get_http(url) if use_cache else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html, application/rss+xml, application/xml;q=0.9, */*;q=0.5",
        }
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        request = urllib.request.Request(url, headers=headers)
        checker = (lambda target: self._robots_allowed(target, allowed_hosts)) if check_robots else None

        for attempt in range(2):
            try:
                with self._opener(allowed_hosts, checker).open(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    if not host_allowed(final_url, allowed_hosts):
                        raise FetchError("Response URL left the source allowlist")
                    if check_robots and not self._robots_allowed(final_url, allowed_hosts):
                        raise FetchError(f"robots.txt does not allow the final URL: {final_url}")
                    body = self._read_limited(response)
                    content_type = response.headers.get("Content-Type", "")
                    if use_cache:
                        self.state.set_http(
                            url, body, response.headers.get("ETag"),
                            response.headers.get("Last-Modified"), content_type,
                        )
                    return FetchResponse(final_url, response.status, body, content_type)
            except urllib.error.HTTPError as error:
                if error.code == 304 and cached:
                    return FetchResponse(
                        url, 200, bytes(cached["body"]), cached.get("content_type") or "", True,
                    )
                if error.code not in RETRYABLE_STATUS or attempt == 1:
                    raise FetchError(f"HTTP {error.code} for {url}", error.code) from error
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = min(5.0, max(1.0, float(retry_after)))
                except (TypeError, ValueError):
                    delay = 1.0
                self.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 1:
                    raise FetchError(f"Network error for {url}: {error}") from error
                self.sleep(1.0)
        raise FetchError(f"Unable to fetch {url}")

    def _robots_rules(self, url, allowed_hosts):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        robots_url = origin + "/robots.txt"
        try:
            response = self._request(robots_url, allowed_hosts, use_cache=False, check_robots=False)
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
            rules = parser
        except FetchError as error:
            if error.status in (401, 403):
                rules = False
            elif error.status in (404, 410):
                rules = None
            else:
                raise FetchError(f"Could not verify robots.txt for {origin}: {error}") from error
        self.robots[origin] = rules
        return rules

    def _robots_allowed(self, url, allowed_hosts):
        rules = self._robots_rules(url, allowed_hosts)
        if rules is False:
            return False
        if rules is None:
            return True
        return rules.can_fetch(USER_AGENT, url)

    def fetch(self, url, allowed_hosts):
        if not host_allowed(url, allowed_hosts):
            raise FetchError(f"URL is not in the source allowlist: {url}")
        if not self._robots_allowed(url, allowed_hosts):
            raise FetchError(f"robots.txt does not allow this collection: {url}")
        return self._request(url, allowed_hosts, use_cache=True, check_robots=True)
