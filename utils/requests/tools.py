import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from utils.config import config

_PLAYLIST_EXTENSIONS = (".m3u", ".m3u8", ".txt")

headers = {
    "Accept": "*/*",
    "Connection": "keep-alive",
    "Accept-Language": "zh-CN,zh;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}


def _merge_headers(custom: dict | None) -> dict:
    """Return a new headers dict merging default headers with custom headers (custom wins)."""
    result = headers.copy()
    if custom:
        for k, v in custom.items():
            if v is None:
                continue
            result[k] = v
    return result


def get_requests(url, data=None, proxy=None, timeout=30, headers_override: dict | None = None):
    """
    Get the response by requests. Accepts headers_override to set request headers.
    """
    if proxy is None:
        proxy = config.http_proxy
    proxies = {"http": proxy, "https": proxy} if proxy else None
    response = None
    try:
        with requests.Session() as session:
            req_headers = _merge_headers(headers_override)
            if data:
                response = session.post(
                    url, headers=req_headers, data=data, proxies=proxies, timeout=timeout
                )
            else:
                response = session.get(url, headers=req_headers, proxies=proxies, timeout=timeout)
    except requests.RequestException as e:
        raise e

    if response is None:
        raise requests.RequestException(f"No response from {url}")

    text = re.sub(r"<!--.*?-->", "", response.text or "", flags=re.DOTALL)
    if not text.strip():
        raise requests.RequestException(f"Empty response from {url}")

    return response


def get_soup_requests(url, data=None, proxy=None, timeout=30, headers_override: dict | None = None):
    """
    Get the soup by requests, pass headers_override to underlying call.
    """
    response = get_requests(url, data, proxy, timeout, headers_override)
    source = re.sub(r"<!--.*?-->", "", response.text or "", flags=re.DOTALL)
    soup = BeautifulSoup(source, "html.parser")
    return soup


def get_redirect_chain_content(url, proxy=None, timeout=30, headers_override: dict | None = None):
    """
    Fetch `url` following redirects, but expose the full redirect chain so callers can
    inspect the targets instead of silently landing on the final resource.

    Returns (chain, content):
    - chain: list of urls visited in order -- the original url plus every redirect
      target (final included). A server that 302s a dead channel to a fixed placeholder
      (e.g. .../backup.m3u8) reveals that target here.
    - content: the final response body text, but ONLY when the final resource looks like
      a playlist (.m3u/.m3u8/.txt path or an mpegurl content-type). Otherwise "" -- the
      body is streamed and never downloaded, so large media is not pulled.
    On any error returns ([], "").
    """
    if proxy is None:
        proxy = config.http_proxy
    proxies = {"http": proxy, "https": proxy} if proxy else None
    req_headers = _merge_headers(headers_override)
    try:
        with requests.Session() as session:
            resp = session.get(
                url, headers=req_headers, proxies=proxies, timeout=timeout, stream=True
            )
            try:
                chain = [r.url for r in resp.history] + [resp.url]
                content = ""
                path = urlsplit(resp.url).path.lower()
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if path.endswith(_PLAYLIST_EXTENSIONS) or "mpegurl" in ctype or "x-mpegurl" in ctype:
                    resp.encoding = "utf-8"
                    content = resp.text or ""
                return chain, content
            finally:
                resp.close()
    except Exception:
        return [], ""
