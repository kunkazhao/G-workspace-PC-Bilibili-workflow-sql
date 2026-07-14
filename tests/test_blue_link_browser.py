from __future__ import annotations

from typing import Any

import pytest

from bworkflow_sql import blue_link_browser
from bworkflow_sql.blue_link_browser import (
    BlueLinkBrowserError,
    BrowserResolution,
    CdpProxyClient,
    TaobaoCouponBrowserResolver,
)


class FakeProxy:
    def __init__(self, *, cards: int = 1, navigation: str = "same") -> None:
        self.cards = cards
        self.navigation = navigation
        self.closed: list[str] = []
        self.clicked: list[tuple[str, str]] = []
        self.current_url = "https://uland.taobao.com/ccoupon/edetail?e=encrypted"
        self.child_created = False

    def targets(self) -> list[dict[str, Any]]:
        targets = [{"targetId": "root", "url": self.current_url}]
        if self.child_created:
            targets.append({
                "targetId": "child",
                "openerId": "root",
                "url": "https://detail.tmall.com/item.htm?id=968283356929",
            })
        return targets

    def new_tab(self, _url: str) -> str:
        return "root"

    def info(self, target_id: str) -> dict[str, Any]:
        if target_id == "child":
            return {"url": "https://detail.tmall.com/item.htm?id=968283356929"}
        return {"url": self.current_url}

    def evaluate(self, _target_id: str, _expression: str) -> dict[str, int]:
        return {"cards": self.cards, "titles": 1, "images": 1}

    def click(self, target_id: str, selector: str) -> None:
        self.clicked.append((target_id, selector))
        if self.navigation == "same":
            self.current_url = "https://detail.tmall.com/item.htm?id=968283356929"
        elif self.navigation == "child":
            self.child_created = True

    def close(self, target_id: str) -> None:
        self.closed.append(target_id)


def test_cdp_proxy_uses_short_metadata_timeout_and_longer_new_tab_timeout() -> None:
    class FakeResponse:
        def __init__(self, payload: Any) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return self.payload

    class RecordingSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, float]] = []

        def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            self.calls.append((url, float(kwargs["timeout"])))
            if url.endswith("/new"):
                return FakeResponse({"targetId": "owned-tab"})
            return FakeResponse([])

    session = RecordingSession()
    client = CdpProxyClient(
        "http://127.0.0.1:3456",
        session=session,  # type: ignore[arg-type]
        request_timeout=2.5,
        new_tab_timeout=12.0,
    )

    assert client.targets() == []
    assert client.new_tab("https://example.com") == "owned-tab"
    assert session.calls == [
        ("http://127.0.0.1:3456/targets", 2.5),
        ("http://127.0.0.1:3456/new", 12.0),
    ]


def test_coupon_resolver_clicks_only_unique_top_title_and_returns_pair() -> None:
    proxy = FakeProxy()
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    result = resolver.resolve("https://b23.tv/mall-example")

    assert result.as_dict() == {
        "source_link": "https://b23.tv/mall-example",
        "resolved_url": "https://detail.tmall.com/item.htm?id=968283356929",
    }
    assert proxy.clicked == [("root", ".item-info-con > a .title")]
    assert proxy.closed == ["root"]


def test_coupon_resolver_tracks_and_closes_only_child_opened_by_own_tab() -> None:
    proxy = FakeProxy(navigation="child")
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    result = resolver.resolve("https://b23.tv/mall-example")

    assert "id=968283356929" in result.resolved_url
    assert set(proxy.closed) == {"root", "child"}


@pytest.mark.parametrize("cards", [0, 2])
def test_coupon_resolver_suspends_when_main_product_is_not_unique(cards: int) -> None:
    proxy = FakeProxy(cards=cards)
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    with pytest.raises(BlueLinkBrowserError) as exc_info:
        resolver.resolve("https://b23.tv/mall-example")

    assert exc_info.value.code == "ambiguous_main_product"
    assert proxy.clicked == []
    assert proxy.closed == ["root"]


def test_coupon_resolver_rejects_non_product_landing_without_clicking() -> None:
    proxy = FakeProxy()
    proxy.current_url = "https://market.m.taobao.com/app/activity/index.html"
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    with pytest.raises(BlueLinkBrowserError) as exc_info:
        resolver.resolve("https://b23.tv/mall-example")

    assert exc_info.value.code == "standard_product_not_reached"
    assert proxy.clicked == []
    assert proxy.closed == ["root"]


def test_resolver_extracts_unique_jd_main_sku_without_clicking() -> None:
    proxy = FakeProxy()
    proxy.current_url = (
        "https://pro.m.jd.com/mall/active/example/index.html"
        "?sku=encrypted&mainSku=100081966294"
    )
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    result = resolver.resolve("https://b23.tv/mall-example")

    assert result.resolved_url == "https://item.jd.com/100081966294.html"
    assert proxy.clicked == []
    assert proxy.closed == ["root"]


def test_resolver_accepts_standard_jd_product_page_without_clicking() -> None:
    proxy = FakeProxy()
    proxy.current_url = "https://item.jd.com/10173626669745.html?cu=true"
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    result = resolver.resolve("https://b23.tv/mall-example")

    assert result.resolved_url == proxy.current_url
    assert proxy.clicked == []
    assert proxy.closed == ["root"]


def test_resolver_accepts_unique_standard_item_return_url_from_jd_risk_page() -> None:
    proxy = FakeProxy()
    proxy.current_url = (
        "https://cfe.m.jd.com/privatedomain/risk_handler/03101900/"
        "?returnurl=https%3A%2F%2Fitem.jd.com%2F100137104166.html"
    )
    resolver = TaobaoCouponBrowserResolver(proxy, navigation_timeout=0.1, poll_interval=0)

    result = resolver.resolve("https://b23.tv/mall-example")

    assert result.resolved_url == "https://item.jd.com/100137104166.html"
    assert proxy.clicked == []
    assert proxy.closed == ["root"]


def test_batch_keeps_successes_and_suspends_only_failed_links(monkeypatch) -> None:
    class FakeBatchResolver:
        def __init__(self, _client, *, navigation_timeout: float) -> None:
            assert navigation_timeout == 3

        def resolve(self, source_link: str) -> BrowserResolution:
            if source_link.endswith("bad"):
                raise BlueLinkBrowserError("ambiguous_main_product", "主商品不唯一")
            return BrowserResolution(source_link, "https://detail.tmall.com/item.htm?id=123456")

    monkeypatch.setattr(blue_link_browser, "CdpProxyClient", lambda _url: object())
    monkeypatch.setattr(blue_link_browser, "TaobaoCouponBrowserResolver", FakeBatchResolver)

    result = blue_link_browser.resolve_blue_links(
        ["https://b23.tv/good", "https://b23.tv/bad"],
        timeout=3,
        retry_delay=0,
    )

    assert result["status"] == "partial"
    assert result["resolutions"] == [{
        "source_link": "https://b23.tv/good",
        "resolved_url": "https://detail.tmall.com/item.htm?id=123456",
    }]
    assert result["unresolved"][0]["code"] == "ambiguous_main_product"
    assert result["unresolved"][0]["attempts"] == 2


def test_batch_retries_transient_browser_failure(monkeypatch) -> None:
    class FakeRetryResolver:
        calls = 0

        def __init__(self, _client, *, navigation_timeout: float) -> None:
            assert navigation_timeout == 3

        def resolve(self, source_link: str) -> BrowserResolution:
            self.__class__.calls += 1
            if self.__class__.calls == 1:
                raise BlueLinkBrowserError("standard_product_not_reached", "京东风控")
            return BrowserResolution(source_link, "https://item.jd.com/123456.html")

    monkeypatch.setattr(blue_link_browser, "CdpProxyClient", lambda _url: object())
    monkeypatch.setattr(blue_link_browser, "TaobaoCouponBrowserResolver", FakeRetryResolver)

    result = blue_link_browser.resolve_blue_links(
        ["https://b23.tv/retry"], timeout=3, attempts=2, retry_delay=0
    )

    assert result["status"] == "complete"
    assert result["resolutions"][0]["resolved_url"] == "https://item.jd.com/123456.html"
    assert FakeRetryResolver.calls == 2
