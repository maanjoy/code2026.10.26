"""HTTP 层测试：接口契约 + 凭证绝不外泄 + 降级态可见。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import get_cache
from app.main import app
from app.services import orders as orders_service
from app.services.auspost import MODE_DEGRADED, MODE_LIVE, ApiResult, failure

from .conftest import FakeAusPostClient, install_fake_client, live_price_payload

TEST_SECRET = "SHOULD-NEVER-LEAK-0001"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUSPOST_API_KEY", TEST_SECRET)
    monkeypatch.setenv("AUSPOST_API_PASSWORD", TEST_SECRET)
    monkeypatch.setenv("AUSPOST_ACCOUNT_NUMBER", "1234567890")
    get_settings.cache_clear()
    get_cache().clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_orders_endpoint_returns_required_fields(client, monkeypatch):
    monkeypatch.setattr(orders_service, "AusPostClient", lambda *a, **k: FakeAusPostClient(
        *a, price_result=ApiResult(MODE_LIVE, payload=live_price_payload(), http_status=200, reason_code="OK"), **k
    ))
    response = client.get("/api/orders")
    assert response.status_code == 200
    body = response.json()

    assert body["meta"]["sku_count"] > 600
    # 生产链路 = 题目给的 2 单 + 生成的 3 单；故意造坏的边界用例不在此接口里
    assert body["meta"]["order_count"] == 5
    assert all(not order["order_no"].startswith("PO-EDGE") for order in body["orders"])

    first = next(order for order in body["orders"] if order["order_no"] == "PO-20251130-00072")
    # 题目第 9 节要求的最小前端内容：SKU 编码/名称/数量/RRP/Ex-GST 单价/Ex-GST 行小计 + 4 个金额
    line = first["items"][0]
    for key in ("sku", "name", "qty", "rrp_incl_gst", "unit_price_ex_gst", "line_total_ex_gst"):
        assert line[key] not in (None, ""), key
    for key in ("subtotal_ex_gst", "gst", "shipment_fee", "total"):
        assert key in first["money"]
    assert first["shipments"][0]["quote"]["mode"] == MODE_LIVE


def test_credentials_never_appear_in_api_responses(client, monkeypatch):
    monkeypatch.setattr(orders_service, "AusPostClient", lambda *a, **k: FakeAusPostClient(
        *a, price_result=ApiResult(MODE_LIVE, payload=live_price_payload(), http_status=200, reason_code="OK"), **k
    ))
    for path in ("/api/health", "/api/orders"):
        body = client.get(path).text
        assert TEST_SECRET not in body

    health = client.get("/api/health").json()
    assert health["credentials_configured"] is True
    assert health["missing_credentials"] == []


def test_missing_credentials_is_a_visible_degraded_state(client, monkeypatch):
    """没配 .env 时：接口照常返回数据，但运费是明确降级态而不是 A$0.00 假装成功。"""
    monkeypatch.delenv("AUSPOST_API_KEY", raising=False)
    monkeypatch.delenv("AUSPOST_API_PASSWORD", raising=False)
    monkeypatch.delenv("AUSPOST_ACCOUNT_NUMBER", raising=False)
    get_settings.cache_clear()
    get_cache().clear()

    body = client.get("/api/orders").json()
    order = next(order for order in body["orders"] if order["order_no"] == "PO-20251130-00072")
    assert order["shipment_mode"] == MODE_DEGRADED
    assert "CREDENTIALS_MISSING" in "".join(order["shipment_issues"])
    assert order["money"]["shipment_fee"] == "0.00"
    get_settings.cache_clear()


def test_order_detail_and_404(client, monkeypatch):
    monkeypatch.setattr(orders_service, "AusPostClient", lambda *a, **k: FakeAusPostClient(
        *a, price_result=failure("HTTP_ERROR", "partner API 返回 HTTP 500", http_status=500), **k
    ))
    assert client.get("/api/orders/PO-20251203-00046").status_code == 200
    missing = client.get("/api/orders/PO-DOES-NOT-EXIST")
    assert missing.status_code == 404


def test_testcases_endpoint_is_off_by_default(client, monkeypatch):
    """生产交付：边界用例接口默认关闭，请求直接 404（不是返回空列表假装通过）。"""
    monkeypatch.setattr(orders_service, "AusPostClient", lambda *a, **k: FakeAusPostClient(*a, **k))
    response = client.get("/api/testcases")
    assert response.status_code == 404
    assert "EXPOSE_TESTCASES" in response.json()["detail"]


def test_testcases_endpoint_when_enabled(client, monkeypatch):
    """显式打开后：用同一套业务逻辑跑边界数据集，3 单都在，且降级原因可见。"""
    monkeypatch.setattr(orders_service, "AusPostClient", lambda *a, **k: FakeAusPostClient(
        *a, price_result=ApiResult(MODE_LIVE, payload=live_price_payload(), http_status=200, reason_code="OK"), **k
    ))
    monkeypatch.setenv("EXPOSE_TESTCASES", "true")
    get_settings.cache_clear()

    body = client.get("/api/testcases").json()
    assert sorted(order["order_no"] for order in body["orders"]) == [
        "PO-EDGE-0001",
        "PO-EDGE-0002",
        "PO-EDGE-0003",
    ]
    incomplete = next(order for order in body["orders"] if order["order_no"] == "PO-EDGE-0003")
    assert incomplete["shipment_mode"] == MODE_DEGRADED
    assert any("DESTINATION_INCOMPLETE" in text for text in incomplete["shipment_issues"])

    monkeypatch.delenv("EXPOSE_TESTCASES", raising=False)
    get_settings.cache_clear()


def test_tracking_endpoint_uses_real_response_shape(client, monkeypatch):
    install_fake_client(monkeypatch, lambda *a, **k: FakeAusPostClient(*a, **k))
    body = client.get("/api/tracking/2FWZ50008569").json()
    assert body["mode"] == MODE_LIVE
    assert body["status"] == "Delivered in Full"
    assert body["events"][0]["description"] == "Delivered"
    assert len(body["articles"]) == 2
