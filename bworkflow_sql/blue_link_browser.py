from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

import requests


DEFAULT_CDP_PROXY_URL = "http://127.0.0.1:3456"
STANDARD_TAOBAO_HOSTS = {"detail.tmall.com", "item.taobao.com"}
STANDARD_JD_HOSTS = {"item.jd.com", "item.m.jd.com"}
COUPON_HOST = "uland.taobao.com"
COUPON_PATH = "/ccoupon/edetail"
JD_ACTIVITY_HOST = "pro.m.jd.com"
JD_RISK_HOST = "cfe.m.jd.com"
TOP_TITLE_SELECTOR = ".item-info-con > a .title"
TOP_IMAGE_SELECTOR = ".item-info-con > a .item-img"


class BlueLinkBrowserError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _taobao_item_id(raw_url: str) -> str:
    parsed = urlparse(_clean(raw_url))
    if (parsed.hostname or "").lower() not in STANDARD_TAOBAO_HOSTS:
        return ""
    values = [value for value in parse_qs(parsed.query).get("id", []) if value.isdigit()]
    return values[0] if len(set(values)) == 1 else ""


def _jd_item_id(raw_url: str) -> str:
    parsed = urlparse(_clean(raw_url))
    if (parsed.hostname or "").lower() not in STANDARD_JD_HOSTS:
        return ""
    path_match = re.search(r"/(\d{5,})\.html", parsed.path)
    if path_match:
        return path_match.group(1)
    values = [
        value
        for key in ("sku", "skuId", "wareId", "productId")
        for value in parse_qs(parsed.query).get(key, [])
        if value.isdigit()
    ]
    return values[0] if len(set(values)) == 1 else ""


def _jd_activity_main_sku(raw_url: str) -> str:
    parsed = urlparse(_clean(raw_url))
    if (parsed.hostname or "").lower() != JD_ACTIVITY_HOST or "/mall/active/" not in parsed.path:
        return ""
    values = [value for value in parse_qs(parsed.query).get("mainSku", []) if value.isdigit()]
    return values[0] if len(set(values)) == 1 else ""


def _canonical_product_url(raw_url: str) -> str:
    if _taobao_item_id(raw_url) or _jd_item_id(raw_url):
        return _clean(raw_url)
    activity_sku = _jd_activity_main_sku(raw_url)
    if activity_sku:
        return f"https://item.jd.com/{activity_sku}.html"
    parsed = urlparse(_clean(raw_url))
    if (parsed.hostname or "").lower() == JD_RISK_HOST and parsed.path.startswith(
        "/privatedomain/risk_handler/"
    ):
        candidates = set()
        for value in parse_qs(parsed.query).get("returnurl", []):
            decoded = unquote(value)
            item_id = _jd_item_id(decoded)
            if item_id:
                candidates.add(f"https://item.jd.com/{item_id}.html")
        if len(candidates) == 1:
            return next(iter(candidates))
    return ""


def _is_coupon_page(raw_url: str) -> bool:
    parsed = urlparse(_clean(raw_url))
    return (parsed.hostname or "").lower() == COUPON_HOST and parsed.path == COUPON_PATH


@dataclass(frozen=True)
class BrowserResolution:
    source_link: str
    resolved_url: str

    def as_dict(self) -> dict[str, str]:
        return {"source_link": self.source_link, "resolved_url": self.resolved_url}


class CdpProxyClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        session: requests.Session | None = None,
        request_timeout: float = 15.0,
    ) -> None:
        configured = base_url or os.getenv("BWORKFLOW_CDP_PROXY_URL") or DEFAULT_CDP_PROXY_URL
        self.base_url = _clean(configured).rstrip("/")
        self.session = session or requests.Session()
        self.request_timeout = request_timeout

    def _request(self, method: str, path: str, *, body: str | None = None) -> Any:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                data=body.encode("utf-8") if body is not None else None,
                headers={"Content-Type": "text/plain; charset=utf-8"} if body is not None else None,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BlueLinkBrowserError("cdp_proxy_unavailable", f"CDP 代理请求失败：{exc}") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise BlueLinkBrowserError("cdp_proxy_error", _clean(payload.get("error")))
        return payload

    def targets(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/targets")
        return payload if isinstance(payload, list) else []

    def new_tab(self, url: str) -> str:
        payload = self._request("POST", "/new", body=url)
        target_id = _clean(payload.get("targetId") if isinstance(payload, dict) else "")
        if not target_id:
            raise BlueLinkBrowserError("cdp_target_missing", "CDP 代理没有返回新标签页 ID")
        return target_id

    def info(self, target_id: str) -> dict[str, Any]:
        payload = self._request("GET", f"/info?target={target_id}")
        return payload if isinstance(payload, dict) else {}

    def evaluate(self, target_id: str, expression: str) -> Any:
        payload = self._request("POST", f"/eval?target={target_id}", body=expression)
        return payload.get("value") if isinstance(payload, dict) else None

    def click(self, target_id: str, selector: str) -> None:
        payload = self._request("POST", f"/click?target={target_id}", body=selector)
        if not isinstance(payload, dict) or not payload.get("clicked"):
            raise BlueLinkBrowserError("main_product_click_failed", "顶部主商品卡点击失败")

    def close(self, target_id: str) -> None:
        try:
            self._request("GET", f"/close?target={target_id}")
        except BlueLinkBrowserError:
            pass


class TaobaoCouponBrowserResolver:
    def __init__(
        self,
        client: CdpProxyClient,
        *,
        navigation_timeout: float = 20.0,
        poll_interval: float = 0.25,
    ) -> None:
        self.client = client
        self.navigation_timeout = navigation_timeout
        self.poll_interval = poll_interval

    def _owned_children(self, owned: set[str]) -> set[str]:
        return {
            _clean(target.get("targetId"))
            for target in self.client.targets()
            if _clean(target.get("targetId")) and _clean(target.get("openerId")) in owned
        }

    def _wait_for_landing(self, owned: set[str]) -> str:
        deadline = time.monotonic() + self.navigation_timeout
        last_urls: list[str] = []
        while time.monotonic() < deadline:
            owned.update(self._owned_children(owned))
            candidates: list[str] = []
            last_urls = []
            for target_id in sorted(owned):
                url = _clean(self.client.info(target_id).get("url"))
                if url:
                    last_urls.append(url)
                candidate = _canonical_product_url(url)
                if candidate:
                    candidates.append(candidate)
            unique_urls = list(dict.fromkeys(candidates))
            if len(unique_urls) == 1:
                return unique_urls[0]
            if len(unique_urls) > 1:
                raise BlueLinkBrowserError("ambiguous_product_landing", "点击顶部主商品后出现多个标准商品页")
            time.sleep(self.poll_interval)
        suffix = f"；最后页面：{' | '.join(last_urls)}" if last_urls else ""
        raise BlueLinkBrowserError("standard_product_not_reached", f"未进入含唯一商品 ID 的标准京东/淘宝/天猫商品页{suffix}")

    def _main_card_click_selector(self, target_id: str) -> str:
        expression = """(() => {
          const cards = Array.from(document.querySelectorAll('.item-info-con > a'));
          if (cards.length !== 1) return {cards: cards.length, titles: 0, images: 0};
          return {
            cards: 1,
            titles: cards[0].querySelectorAll('.title').length,
            images: cards[0].querySelectorAll('.item-img').length
          };
        })()"""
        state = self.client.evaluate(target_id, expression)
        if not isinstance(state, dict) or int(state.get("cards") or 0) != 1:
            count = state.get("cards") if isinstance(state, dict) else "unknown"
            raise BlueLinkBrowserError("ambiguous_main_product", f"优惠券页顶部主商品卡数量不是 1：{count}")
        if int(state.get("titles") or 0) == 1:
            return TOP_TITLE_SELECTOR
        if int(state.get("images") or 0) == 1:
            return TOP_IMAGE_SELECTOR
        raise BlueLinkBrowserError("main_product_target_missing", "唯一主商品卡没有唯一可点击标题或图片")

    def resolve(self, source_link: str) -> BrowserResolution:
        source = _clean(source_link)
        parsed = urlparse(source)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BlueLinkBrowserError("invalid_source_link", "source_link 必须是 HTTP(S) URL")
        canonical_source = _canonical_product_url(source)
        if canonical_source:
            return BrowserResolution(source, canonical_source)

        owned: set[str] = set()
        target_id = self.client.new_tab(source)
        owned.add(target_id)
        try:
            landing_url = _clean(self.client.info(target_id).get("url"))
            canonical_landing = _canonical_product_url(landing_url)
            if canonical_landing:
                return BrowserResolution(source, canonical_landing)
            if not _is_coupon_page(landing_url):
                resolved_url = self._wait_for_landing(owned)
                return BrowserResolution(source, resolved_url)

            selector = self._main_card_click_selector(target_id)
            self.client.click(target_id, selector)
            resolved_url = self._wait_for_landing(owned)
            if not _canonical_product_url(resolved_url):
                raise BlueLinkBrowserError("product_id_missing", "最终标准商品页没有唯一商品 ID")
            return BrowserResolution(source, resolved_url)
        finally:
            owned.update(self._owned_children(owned))
            for owned_target in sorted(owned, reverse=True):
                self.client.close(owned_target)


def resolve_blue_links(
    source_links: Iterable[str],
    *,
    proxy_url: str | None = None,
    timeout: float = 20.0,
    attempts: int = 2,
    retry_delay: float = 1.0,
) -> dict[str, Any]:
    resolver = TaobaoCouponBrowserResolver(
        CdpProxyClient(proxy_url),
        navigation_timeout=timeout,
    )
    resolutions: list[dict[str, str]] = []
    unresolved: list[dict[str, Any]] = []
    for source_link in source_links:
        last_error: BlueLinkBrowserError | None = None
        for attempt in range(max(1, attempts)):
            try:
                resolutions.append(resolver.resolve(source_link).as_dict())
                last_error = None
                break
            except BlueLinkBrowserError as exc:
                last_error = exc
                if attempt + 1 < max(1, attempts) and retry_delay > 0:
                    time.sleep(retry_delay)
        if last_error is not None:
            unresolved.append(
                {
                    "source_link": _clean(source_link),
                    "status": "suspended",
                    "code": last_error.code,
                    "reason": str(last_error),
                    "attempts": max(1, attempts),
                }
            )
    return {
        "ok": not unresolved,
        "status": "complete" if not unresolved else "partial",
        "resolutions": resolutions,
        "unresolved": unresolved,
    }
