"""金额计算测试：对齐 data/expected_totals.json（该文件只作为测试基准，不参与业务计算）。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

import pytest

from app.config import Settings, get_settings
from app.services.money import money, to_decimal
from app.services.pricing import (
    PricingRules,
    gst_amount,
    line_total_round_after_multiply,
    price_line,
    subtotal_ex_gst,
    unit_price_ex_gst,
)

EXPECTED_PATH = get_settings().data_dir / "expected_totals.json"


def load_expected() -> Dict[str, dict]:
    raw = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    return {order["order_no"]: order for order in raw["orders"]}


@pytest.fixture(scope="module")
def expected() -> Dict[str, dict]:
    return load_expected()


def order_lines(dataset, order_no: str, settings: Settings) -> List[dict]:
    for order in dataset.orders:
        if str(order.get(settings.order_no_field)) == order_no:
            return [item for item in order[settings.items_field] if isinstance(item, dict)]
    raise AssertionError(f"orders.json 里没有 {order_no}")


@pytest.mark.parametrize("order_no", ["PO-20251130-00072", "PO-20251203-00046"])
def test_matches_expected_totals(order_no, main_dataset, catalog, settings, expected):
    """真实的 DB fixture 数据 + 业务计算出的数据必须等于 `data/expected_totals.json` 里的参考值。"""
    rules = PricingRules(gst_rate=settings.gst_rate)
    baseline = expected[order_no]

    priced = []
    for raw_item in order_lines(main_dataset, order_no, settings):
        record = catalog.get(raw_item[settings.item_sku_field])
        assert record is not None, f"SKU {raw_item} 在目录里不存在"
        priced.append((record, price_line(record.rrp_incl_gst, int(raw_item[settings.item_qty_field]), rules)))

    subtotal = subtotal_ex_gst([line for _, line in priced])
    gst = gst_amount(subtotal, rules)

    assert money(subtotal) == money(baseline["subtotal_ex_gst"])
    assert gst == money(baseline["gst"])
    # expected_totals 里的 total 是按 Shipment Fee = 0 计的
    assert money(subtotal + gst + Decimal("0.00")) == money(baseline["total"])

    by_sku = {raw[settings.item_sku_field]: raw for raw in order_lines(main_dataset, order_no, settings)}
    for record, line in priced:
        baseline_line = next(
            item for item in baseline["lines"] if item["sku"] == record.sku
        )
        assert line.unit_price_ex_gst == money(baseline_line["unit_price_ex_gst"])
        assert line.line_total_ex_gst == money(baseline_line["line_subtotal_ex_gst"])
        assert int(by_sku[record.sku][settings.item_qty_field]) == line.qty


def test_unit_price_is_rounded_to_cents_first(settings):
    """199.00/1.1 = 180.9090… -> 180.91（先舍入到分，不是先乘后舍入）。"""
    rules = PricingRules(gst_rate=settings.gst_rate)
    assert unit_price_ex_gst("199.00", rules) == Decimal("180.91")
    assert unit_price_ex_gst("99.00", rules) == Decimal("90.00")
    assert unit_price_ex_gst("140.00", rules) == Decimal("127.27")

    line = price_line("140.00", 6, rules)
    assert line.unit_price_ex_gst == Decimal("127.27")
    assert line.line_total_ex_gst == Decimal("763.62")


@pytest.mark.parametrize(
    "order_no,expected_diff",
    [("PO-20251130-00072", "0.04"), ("PO-20251203-00046", "0.01")],
)
def test_rounding_order_really_changes_money(order_no, expected_diff, main_dataset, catalog, settings):
    """文档化的坑：单价先舍入 vs 行合计后舍入，两笔订单分别差 0.04 / 0.01。"""
    rules = PricingRules(gst_rate=settings.gst_rate)

    subtotal_first = Decimal("0.00")
    subtotal_after = Decimal("0.00")
    for raw_item in order_lines(main_dataset, order_no, settings):
        record = catalog.get(raw_item[settings.item_sku_field])
        qty = int(raw_item[settings.item_qty_field])
        subtotal_first += price_line(record.rrp_incl_gst, qty, rules).line_total_ex_gst
        subtotal_after += line_total_round_after_multiply(record.rrp_incl_gst, qty, rules)

    assert subtotal_first != subtotal_after
    assert money(subtotal_after - subtotal_first) == money(expected_diff)


def test_money_uses_half_up_not_bankers_rounding():
    assert money("2.345") == Decimal("2.35")
    assert money(Decimal("0.005")) == Decimal("0.01")
    assert money("0.00") == Decimal("0.00")
    assert to_decimal("abc") is None


def test_business_logic_contains_no_python_round():
    """守卫：业务逻辑里不允许调用内置 round()（银行家舍入）。用 AST 精确检查调用，排除注释/文档字符串。"""
    import ast

    offenders = []
    app_dir = Path(__file__).resolve().parents[1] / "app"
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "round":
                offenders.append(f"{path.relative_to(app_dir.parent)}:{node.lineno}")
    assert offenders == [], f"以下位置调用了 Python round()：{offenders}"
