"""数据驱动与边界场景测试（用假 partner 客户端，离线可跑）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import Settings
from app.deps import build_repository
from app.services import orders as orders_service
from app.services import shipment as shipment_service
from app.services.auspost import MODE_DEGRADED, MODE_LIVE, ApiResult, failure

from .conftest import FakeAusPostClient, live_price_payload


def fake_live_factory(*args, **kwargs):
    address_ok = ApiResult(MODE_LIVE, payload={"address": {"found": True}}, http_status=200, reason_code="OK")
    return FakeAusPostClient(
        *args,
        price_result=ApiResult(MODE_LIVE, payload=live_price_payload(), http_status=200, reason_code="OK"),
        address_result=address_ok,
        **kwargs,
    )


def fake_down_factory(*args, **kwargs):
    address_down = failure("HTTP_ERROR", "partner API 返回 HTTP 503", http_status=503)
    return FakeAusPostClient(
        *args,
        price_result=failure("HTTP_ERROR", "partner API 返回 HTTP 503", http_status=503),
        address_result=address_down,
        **kwargs,
    )


def order_by_no(payload, order_no: str) -> dict:
    return next(order for order in payload["orders"] if order["order_no"] == order_no)


def test_groups_are_defined_by_track_not_order(main_dataset, datasets, catalog, settings: Settings):
    """订单 2 的 SKU 分属 Track 2 / Track 3 -> 两票货。"""
    groups, order_keys = shipment_service.build_groups([main_dataset], catalog, settings)

    first = order_keys[("orders.json", "PO-20251130-00072")]
    second = order_keys[("orders.json", "PO-20251203-00046")]
    assert len(first) == 1
    assert len(second) == 2
    assert sorted(label for _, label, _ in second) == ["Track 2", "Track 3"]
    assert len(groups) == 3


def test_orders_json_has_two_orders_only(main_dataset):
    assert [str(order["order_no"]) for order in main_dataset.orders] == [
        "PO-20251130-00072",
        "PO-20251203-00046",
    ]


@pytest.mark.asyncio
async def test_production_scope_excludes_edge_dataset(monkeypatch, settings: Settings):
    """生产接口只出「真实订单 + 生成的测试订单」，故意造坏的边界数据不混进来。"""
    monkeypatch.setattr(orders_service, "AusPostClient", fake_live_factory)
    payload = await build_repository(settings).list_orders()
    assert [order["order_no"] for order in payload["orders"]] == [
        "PO-20251130-00072",
        "PO-20251203-00046",
        "PO-20251208-00118",
        "PO-20251210-00127",
        "PO-20251212-00134",
    ]
    assert {order["source_file"] for order in payload["orders"]} == {
        "orders.json",
        "orders_generated.json",
    }
    assert not any(order["order_no"].startswith("PO-EDGE") for order in payload["orders"])


@pytest.mark.asyncio
async def test_missing_sku_and_invalid_qty_are_reported_not_crashing(monkeypatch, edge_settings: Settings):
    monkeypatch.setattr(orders_service, "AusPostClient", fake_live_factory)
    payload = await build_repository(edge_settings).list_orders()
    edge = order_by_no(payload, "PO-EDGE-0001")

    by_sku = {item["sku"]: item for item in edge["items"]}
    assert "SKU_NOT_FOUND" in by_sku["SKU-NOT-IN-CATALOG"]["issues"]
    assert "SKU_NOT_FOUND" in by_sku[""]["issues"]
    assert "QTY_INVALID" in by_sku["TBAMET28"]["issues"]  # qty = 0
    assert "QTY_INVALID" in by_sku["TBOPAL28"]["issues"]  # qty = -3
    assert "QTY_INVALID" in by_sku["MCMW10"]["issues"]  # qty = "two"
    assert by_sku["HARNIG"]["issues"] == []  # qty = "7" 合法
    assert by_sku["HARNIG"]["qty"] == 7
    assert by_sku["SKU-NOT-IN-CATALOG"]["unit_price_ex_gst"] is None
    # 未匹配 / 非法数量的行不计入金额，但订单照样能算出来
    assert edge["money"]["subtotal_ex_gst"] == "1128.15"  # 2×90.00 + 7×135.45


@pytest.mark.asyncio
async def test_live_shipping_changes_total(monkeypatch, settings: Settings):
    """运费来自 POST /prices/shipments（这里用真实响应样例），不是写死的 0。"""
    monkeypatch.setattr(orders_service, "AusPostClient", fake_live_factory)
    payload = await build_repository(settings).list_orders()
    order = order_by_no(payload, "PO-20251130-00072")
    money = order["money"]

    assert order["shipment_mode"] == MODE_LIVE                     # 只有 Track 1 一票货
    assert money["subtotal_ex_gst"] == "1937.24"
    assert money["gst_on_goods"] == "193.72"
    # 口径（题目第 4 节）：GST 只对商品小计计征，运费按承运商报价总额单列
    assert money["shipment_fee"] == "71.04"                        # total_cost（含 GST）
    assert money["shipment_fee_ex_gst"] == "64.58"                 # 同一笔运费的 ex-GST 口径
    assert money["shipment_gst"] == "6.46"                         # 运费内含的 GST（仅备查）
    assert money["gst"] == "193.72"                                # 严格等于 Subtotal × 10%
    assert money["total"] == "2202.00"                             # 1937.24 + 193.72 + 71.04
    assert money["shipment_total_cost_incl_gst"] == "71.04"
    assert order["shipments"][0]["quote"]["strategy"] == "single_carton"


@pytest.mark.asyncio
async def test_degraded_shipping_is_explicit_and_excluded(monkeypatch, settings: Settings):
    """partner API 挂掉 -> 明确降级态，运费不计入 Total，前端要能看到原因。"""
    monkeypatch.setattr(orders_service, "AusPostClient", fake_down_factory)
    payload = await build_repository(settings).list_orders()
    order = order_by_no(payload, "PO-20251130-00072")

    assert order["shipment_mode"] == MODE_DEGRADED
    assert order["money"]["shipment_fee"] == "0.00"
    assert order["money"]["gst"] == "193.72"
    assert order["money"]["total"] == "2130.96"  # 退化到 Shipment Fee = 0 的降级口径（见 README §4）
    assert order["shipment_issues"], "降级必须给出可读原因"
    assert "HTTP_ERROR" in order["shipment_issues"][0]
    assert order["shipments"][0]["quote"]["http_status"] == 503


@pytest.mark.asyncio
async def test_tracking_parses_both_response_shapes(monkeypatch, settings: Settings):
    """两种响应结构都要兼容，TNT 单号在 AusPost 侧也能查到。"""
    monkeypatch.setattr(orders_service, "AusPostClient", fake_live_factory)
    payload = await build_repository(settings).list_orders()

    order1 = order_by_no(payload, "PO-20251130-00072")
    consignment = order1["shipments"][0]["tracking"]
    assert consignment["mode"] == MODE_LIVE
    assert consignment["status"] == "Delivered in Full"
    assert consignment["event_count"] > 0
    assert {article["product_type"] for article in consignment["articles"]} == {"EXP"}

    order2 = order_by_no(payload, "PO-20251203-00046")
    track3 = next(
        shipment["tracking"]
        for shipment in order2["shipments"]
        if shipment["tracking"]["tracking_id"] == "305506914"
    )
    assert track3["mode"] == MODE_LIVE        # 不是 unavailable
    assert track3["status"] == "Delivered"
    assert track3["articles"][0]["product_type"] == "Parcel Post"


@pytest.mark.asyncio
async def test_incomplete_destination_blocks_quote(monkeypatch, edge_settings: Settings):
    monkeypatch.setattr(orders_service, "AusPostClient", fake_live_factory)
    payload = await build_repository(edge_settings).list_orders()
    edge = order_by_no(payload, "PO-EDGE-0003")

    assert edge["shipment_mode"] == MODE_DEGRADED
    assert any("DESTINATION_INCOMPLETE" in text for text in edge["shipment_issues"])


@pytest.mark.asyncio
async def test_unknown_tracking_number_is_a_visible_state(monkeypatch, edge_settings: Settings):
    monkeypatch.setattr(orders_service, "AusPostClient", fake_live_factory)
    payload = await build_repository(edge_settings).list_orders()
    edge = order_by_no(payload, "PO-EDGE-0002")
    tracking = edge["shipments"][0]["tracking"]

    assert tracking["mode"] == MODE_LIVE
    assert tracking["event_count"] == 0
    assert tracking["message"] == "partner API 未返回任何轨迹事件"
