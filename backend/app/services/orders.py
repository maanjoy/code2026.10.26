"""把「数据 + 金额 + 运费 + 轨迹」组装成前端要的视图模型。

这里没有任何常量订单号 / SKU / 价格：所有字段都来自 JSON 数据与 partner API 的真实返回。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import Settings
from app.data_loader import DataError, Dataset, JsonStore, SkuCatalog, load_catalog, load_datasets
from app.services import shipment as shipment_service
from app.services.auspost import AusPostClient, MODE_DEGRADED, MODE_LIVE
from app.services.cache import TTLCache
from app.services.money import ZERO, money, money_str, to_decimal
from app.services.pricing import PricingRules, gst_amount, price_line

logger = logging.getLogger(__name__)

# 订单头参与结构化展示的字段（是字段名，不是字段值）
PARTY_KEYS = ("company", "customer", "contact", "phone", "email")
ADDRESS_KEYS = ("address", "suburb", "state", "postcode", "country")

GroupKey = Tuple[Any, ...]


@dataclass
class World:
    """一次组装所需的全部输入。"""

    datasets: List[Dataset]
    catalog: SkuCatalog
    groups: List[shipment_service.ShipmentGroup]
    order_group_keys: Dict[Tuple[str, str], List[GroupKey]]
    quotes: Dict[GroupKey, Dict[str, Any]]
    trackings: Dict[GroupKey, Dict[str, Any]]
    gst_rate: Decimal


async def build_world(
    settings: Settings,
    store: JsonStore,
    cache: TTLCache,
    *,
    with_tracking: bool = True,
) -> World:
    datasets = load_datasets(store, settings)
    catalog = load_catalog(store, settings)
    groups, order_group_keys = shipment_service.build_groups(datasets, catalog, settings)

    async with AusPostClient(settings, cache) as client:
        quotes = await shipment_service.quote_groups(client, groups, settings, settings.gst_rate)
        trackings = (
            await shipment_service.fetch_trackings(client, groups) if with_tracking else {}
        )

    return World(
        datasets=datasets,
        catalog=catalog,
        groups=groups,
        order_group_keys=order_group_keys,
        quotes=quotes,
        trackings=trackings,
        gst_rate=settings.gst_rate,
    )


def _line_view(
    raw_item: Dict[str, Any],
    settings: Settings,
    catalog: SkuCatalog,
    rules: PricingRules,
) -> Dict[str, Any]:
    sku_code = str(raw_item.get(settings.item_sku_field) or "").strip()
    qty, qty_issue = shipment_service.parse_qty(raw_item.get(settings.item_qty_field))
    record = catalog.get(sku_code)

    issues: List[str] = []
    if record is None:
        issues.append("SKU_NOT_FOUND")
    elif record.rrp_incl_gst is None:
        issues.append("RRP_MISSING")
    if qty_issue:
        issues.append(qty_issue)

    unit_ex: Optional[Decimal] = None
    line_total: Optional[Decimal] = None
    if record is not None and record.rrp_incl_gst is not None and qty:
        priced = price_line(record.rrp_incl_gst, qty, rules)
        unit_ex = priced.unit_price_ex_gst
        line_total = priced.line_total_ex_gst

    return {
        "sku": sku_code,
        "name": record.product_name if record else "",
        "qty_raw": raw_item.get(settings.item_qty_field),
        "qty": qty,
        "rrp_incl_gst": None if record is None or record.rrp_incl_gst is None else money_str(record.rrp_incl_gst),
        "unit_price_ex_gst": None if unit_ex is None else money_str(unit_ex),
        "line_total_ex_gst": None if line_total is None else money_str(line_total),
        # SKU 数据里没有图片字段，占位图由前端渲染（见 README「占位图说明」）
        "image_url": None,
        "issues": issues,
        "priced": line_total is not None,
    }


def _shipment_view(
    group: shipment_service.ShipmentGroup,
    quote: Dict[str, Any],
    tracking: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "track_label": group.label,
        "tracking_id": group.tracking_id,
        "courier": group.courier,
        "dataset": group.dataset,
        "destination": group.destination.as_dict(),
        "parcels": [line.as_dict() for line in group.lines],
        "item_count": group.item_count,
        "order_numbers": list(group.order_numbers),
        "issues": list(group.issues),
        "quote": quote,
        "tracking": tracking,
    }


def _groups_for_order(world: World, dataset_name: str, order_no: str) -> List[shipment_service.ShipmentGroup]:
    by_key = {group.key: group for group in world.groups}
    return [by_key[key] for key in world.order_group_keys.get((dataset_name, order_no), []) if key in by_key]


def compose_order(dataset: Dataset, order: Dict[str, Any], world: World, settings: Settings) -> Dict[str, Any]:
    rules = PricingRules(gst_rate=world.gst_rate)
    order_no = str(order.get(settings.order_no_field) or "").strip()

    lines = [
        _line_view(raw_item, settings, world.catalog, rules)
        for raw_item in (order.get(settings.items_field) or [])
        if isinstance(raw_item, dict)
    ]

    priced_lines = [line for line in lines if line["priced"]]
    goods_subtotal = money(
        sum([to_decimal(line["line_total_ex_gst"]) or ZERO for line in priced_lines], ZERO)
    )
    goods_gst = gst_amount(goods_subtotal, rules)

    shipment_fee = ZERO
    shipment_gst = ZERO
    shipment_total_incl_gst = ZERO
    shipment_modes: List[str] = []
    shipment_issues: List[str] = []

    for group in _groups_for_order(world, dataset.name, order_no):
        quote = world.quotes.get(group.key, {})
        mode = quote.get("mode", MODE_DEGRADED)
        shipment_modes.append(mode)
        if mode == MODE_LIVE:
            shipment_fee += to_decimal(quote.get("cost_ex_gst")) or ZERO
            shipment_gst += to_decimal(quote.get("cost_gst")) or ZERO
            shipment_total_incl_gst += to_decimal(quote.get("total_cost")) or ZERO
        else:
            reason = quote.get("reason_code") or "UNKNOWN"
            detail = quote.get("message") or ""
            shipment_issues.append(f"{group.label}：{reason}{(' - ' + detail) if detail else ''}")

    if shipment_modes and all(mode == MODE_LIVE for mode in shipment_modes):
        shipment_mode = MODE_LIVE
    elif any(mode == MODE_LIVE for mode in shipment_modes):
        shipment_mode = "partial"
    else:
        shipment_mode = MODE_DEGRADED

    # 金额四行的口径严格照题目第 4 节：
    #   GST        = Order Subtotal × 10%     （只对商品计税，不含运费）
    #   Shipment   = 承运商报价总额（含 GST），作为一条独立费用列入
    #   Total      = Subtotal + GST + Shipment Fee
    # 运费的 GST 已包含在 Shipment Fee 金额里，不再并入 GST 行（避免重复计税、
    # 也避免 GST 行的数值偏离 "Subtotal × 10%" 的既定公式）。
    gst_total = goods_gst
    shipment_fee_incl_gst = money(shipment_total_incl_gst)
    total = money(goods_subtotal + gst_total + shipment_fee_incl_gst)

    data_issues: List[str] = []
    for line in lines:
        for issue in line["issues"]:
            data_issues.append(f"{line['sku'] or '<空 SKU>'}：{issue}")
    if not order_no:
        data_issues.append("ORDER_NO_MISSING")

    header = {
        key: value
        for key, value in order.items()
        if key != settings.items_field and isinstance(value, (str, int, float))
    }

    return {
        "order_no": order_no,
        "source_file": dataset.name,
        # 是否属于「故意造坏」的边界数据集（由 TEST_DATASET_PATTERN 决定，不写死文件名）。
        # 自造的合法测试订单（orders_generated.json）不算，它和真实订单一样进首页。
        "is_test_case": dataset.name == settings.test_dataset_pattern,
        "header": header,
        "status": str(order.get("status") or ""),
        "order_date": str(order.get("order_date") or ""),
        "party": {key: str(order.get(key) or "") for key in PARTY_KEYS if key in order},
        "delivery": {key: str(order.get(key) or "") for key in ADDRESS_KEYS if key in order},
        "money": {
            "subtotal_ex_gst": money_str(goods_subtotal),
            "gst": money_str(gst_total),
            "gst_on_goods": money_str(goods_gst),
            "shipment_gst": money_str(shipment_gst),
            # 计入 Total 的运费 = 承运商报价总额（含 GST）
            "shipment_fee": money_str(shipment_fee_incl_gst),
            # 备查：承运商报价的 ex-GST 拆分，供对账用（不参与 Total 计算，UI 不展示）
            "shipment_fee_ex_gst": money_str(shipment_fee),
            "shipment_total_cost_incl_gst": money_str(shipment_total_incl_gst),
            "total": money_str(total),
            "currency": "AUD",
            "gst_rate": str(world.gst_rate),
        },
        "item_count": len(lines),
        "priced_item_count": len(priced_lines),
        "items": lines,
        "shipments": [
            _shipment_view(group, world.quotes.get(group.key, {}), world.trackings.get(group.key))
            for group in _groups_for_order(world, dataset.name, order_no)
        ],
        "shipment_mode": shipment_mode,
        "shipment_issues": shipment_issues,
        "data_issues": data_issues,
    }


def meta_of(world: World) -> Dict[str, Any]:
    return {
        "datasets": [dataset.name for dataset in world.datasets],
        "sku_count": len(world.catalog),
        "order_count": sum(len(dataset.orders) for dataset in world.datasets),
        "shipment_group_count": len(world.groups),
        "gst_rate": str(world.gst_rate),
        "currency": "AUD",
    }


class OrderRepository:
    def __init__(self, settings: Settings, store: JsonStore, cache: TTLCache) -> None:
        self.settings = settings
        self.store = store
        self.cache = cache

    async def list_orders(self) -> Dict[str, Any]:
        world = await build_world(self.settings, self.store, self.cache, with_tracking=True)
        meta = meta_of(world)
        # 测试页是否可用由后端决定：生产（默认关闭）时前端连入口都不显示
        meta["testcases_enabled"] = self.settings.expose_testcases
        return {
            "meta": meta,
            "orders": [
                compose_order(dataset, order, world, self.settings)
                for dataset in world.datasets
                for order in dataset.orders
            ],
        }

    async def get_order(self, order_no: str) -> Optional[Dict[str, Any]]:
        world = await build_world(self.settings, self.store, self.cache, with_tracking=True)
        wanted = str(order_no).strip().upper()
        for dataset in world.datasets:
            for order in dataset.orders:
                candidate = str(order.get(self.settings.order_no_field) or "").strip().upper()
                if candidate == wanted:
                    return compose_order(dataset, order, world, self.settings)
        return None

    async def list_trackings(self) -> Dict[str, Any]:
        world = await build_world(self.settings, self.store, self.cache, with_tracking=True)
        return {
            "trackings": [
                _shipment_view(group, world.quotes.get(group.key, {}), world.trackings.get(group.key))
                for group in world.groups
            ]
        }

    async def health(self) -> Dict[str, Any]:
        """只返回「凭证是否已配置」，绝不返回凭证内容。"""
        try:
            datasets = load_datasets(self.store, self.settings)
            catalog = load_catalog(self.store, self.settings)
        except DataError as exc:
            return {"status": "error", "detail": str(exc)}
        return {
            "status": "ok",
            "data_dir": str(self.store.data_dir),
            "datasets": [dataset.name for dataset in datasets],
            "sku_count": len(catalog),
            "order_count": sum(len(dataset.orders) for dataset in datasets),
            "credentials_configured": self.settings.credentials_ready,
            "missing_credentials": self.settings.missing_credentials(),
            "auspost_base_url": self.settings.auspost_base_url,
            "http_timeout_seconds": self.settings.http_timeout_seconds,
            "gst_rate": str(self.settings.gst_rate),
            "testcases_enabled": self.settings.expose_testcases,
        }
