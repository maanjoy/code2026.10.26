"""运费估算 + 轨迹查询。

运费**不使用任何写死的公式**，一律 POST /prices/shipments 拿 partner 报价；
轨迹一律 GET /track。两者超时 / 非 2xx 时进入明确降级态，并把原因透给前端。

分组规则：运费和轨迹按 **Track** 分组，不是按订单。
订单 2 的 4 个 SKU 分属 Track 2 和 Track 3，所以它是两票货。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.config import Settings
from app.data_loader import Dataset, SkuCatalog, SkuRecord
from app.services import auspost
from app.services.auspost import ApiResult, AusPostClient, MODE_DEGRADED, MODE_LIVE, parse_address_lookup, parse_shipment_quote, parse_tracking
from app.services.money import ZERO, money, money_str, to_decimal

logger = logging.getLogger(__name__)

MAX_ORIGIN_ATTEMPTS = 3

# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Origin:
    suburb: str
    state: str
    postcode: str
    country: str = "AU"

    def label(self) -> str:
        return f"{self.suburb} {self.state} {self.postcode}"

    def as_api_dict(self) -> Dict[str, str]:
        return {"suburb": self.suburb, "state": self.state, "postcode": self.postcode}

    def as_dict(self) -> Dict[str, str]:
        return {**self.as_api_dict(), "country": self.country}


@dataclass(frozen=True)
class Destination:
    suburb: str
    state: str
    postcode: str
    country: str = "AU"

    @property
    def fingerprint(self) -> Tuple[str, str, str]:
        return (self.suburb.upper(), self.state.upper(), self.postcode.strip())

    @property
    def is_complete(self) -> bool:
        return bool(self.suburb and self.state and self.postcode)

    def label(self) -> str:
        return f"{self.suburb} {self.state} {self.postcode}"

    def as_api_dict(self) -> Dict[str, str]:
        return {"suburb": self.suburb, "state": self.state, "postcode": self.postcode}

    def as_dict(self) -> Dict[str, str]:
        return {**self.as_api_dict(), "country": self.country}


@dataclass
class ParcelLine:
    sku: str
    name: str
    qty: int
    weight_g: Optional[Decimal]
    length_mm: Optional[Decimal]
    width_mm: Optional[Decimal]
    height_mm: Optional[Decimal]
    volume_mm3: Optional[Decimal] = None
    issues: List[str] = field(default_factory=list)

    @property
    def has_dimensions(self) -> bool:
        return None not in (self.weight_g, self.length_mm, self.width_mm, self.height_mm)

    @property
    def item_volume_mm3(self) -> Decimal:
        """单件体积：优先用目录里的 volume，缺失时用 L×W×H 补算。"""
        if self.volume_mm3:
            return to_decimal(self.volume_mm3) or ZERO
        dims = [to_decimal(v) for v in (self.length_mm, self.width_mm, self.height_mm)]
        if any(value is None for value in dims):
            return ZERO
        return dims[0] * dims[1] * dims[2]

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"sku": self.sku, "name": self.name, "qty": self.qty}
        payload["issues"] = list(self.issues)
        for attr, key in (
            ("weight_g", "weight_g"),
            ("length_mm", "length_mm"),
            ("width_mm", "width_mm"),
            ("height_mm", "height_mm"),
            ("volume_mm3", "volume_mm3"),
        ):
            value = getattr(self, attr)
            payload[key] = None if value is None else str(value)
        return payload


@dataclass
class ShipmentGroup:
    """一票货：一个 Track + 一个目的地。"""

    label: str
    dataset: str
    destination: Destination
    dataset_origin: Dict[str, Any] = field(default_factory=dict)
    tracking_id: Optional[str] = None
    courier: str = ""
    lines: List[ParcelLine] = field(default_factory=list)
    order_numbers: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, Tuple[str, str, str]]:
        return (self.dataset, self.label, self.destination.fingerprint)

    @property
    def item_count(self) -> int:
        return sum(line.qty for line in self.lines)


# 装箱假设（写进 README「运费估算假设」）：
#   仓库按标准纸箱底面 300×200mm 发货，高度由「整票货总体积 ÷ 底面积」推算，
#   并保证至少装得下最高的那一件、至少 3cm，单边封顶 100cm（AusPost 单边上限 105cm）。
# 为什么不用「高度按件数线性累加」：5 个 SKU、11 件货会算出 165cm 高的药箱，
# 物理上不成立，报价也就不可信。体积法算出来更接近真实出货箱型。
CARTON_BASE_LENGTH_MM = Decimal("300")
CARTON_BASE_WIDTH_MM = Decimal("200")
CARTON_MIN_HEIGHT_MM = Decimal("30")
CARTON_MAX_SIDE_MM = Decimal("1000")


def _cm(value: Decimal) -> str:
    """mm -> cm（API 用 cm），向上取整到整数 cm。"""
    value = to_decimal(value) or ZERO
    return str(int((value / Decimal("10")).to_integral_value(rounding="ROUND_CEILING")))


def _kg(grams: Optional[Decimal]) -> str:
    grams = to_decimal(grams) or ZERO
    kilograms = (grams / Decimal("1000")).quantize(Decimal("0.001"))
    return f"{kilograms.normalize():f}"


def build_single_carton(lines: Sequence[ParcelLine]) -> Dict[str, str]:
    """策略 A：整票货按一个标准纸箱算（体积法，见上方常量注释）。"""
    total_volume = sum(
        [(line.item_volume_mm3 * Decimal(line.qty)) for line in lines], ZERO
    )
    actual_weight = sum(
        [((to_decimal(line.weight_g) or ZERO) * Decimal(line.qty)) for line in lines], ZERO
    )

    max_length = max([to_decimal(line.length_mm) or ZERO for line in lines], default=ZERO)
    max_width = max([to_decimal(line.width_mm) or ZERO for line in lines], default=ZERO)
    max_height = max([to_decimal(line.height_mm) or ZERO for line in lines], default=ZERO)

    length = max(CARTON_BASE_LENGTH_MM, max_length)
    width = max(CARTON_BASE_WIDTH_MM, max_width)

    base_area = length * width
    height = (total_volume / base_area) if base_area > 0 else ZERO
    height = max(height, max_height, CARTON_MIN_HEIGHT_MM)
    height = min(height, CARTON_MAX_SIDE_MM)
    length = min(length, CARTON_MAX_SIDE_MM)
    width = min(width, CARTON_MAX_SIDE_MM)

    # 重量传实际重量；立方重量由 AusPost 自己的计费规则处理，避免重复折算
    return {"length": _cm(length), "width": _cm(width), "height": _cm(height), "weight": _kg(actual_weight)}


def build_per_item_entries(lines: Sequence[ParcelLine]) -> List[Dict[str, str]]:
    """策略 B：每个 SKU 单独报价再求和（整箱报价失败时由调用方降级到此策略）。"""
    entries: List[Dict[str, str]] = []
    for line in lines:
        entries.append(
            {
                "length": _cm(line.length_mm or ZERO),
                "width": _cm(line.width_mm or ZERO),
                "height": _cm(line.height_mm or ZERO),
                "weight": _kg((to_decimal(line.weight_g) or ZERO) * Decimal(line.qty)),
            }
        )
    return entries


def _price_body(origin: Origin, destination: Destination, items: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "shipments": [
            {
                "from": origin.as_api_dict(),
                "to": destination.as_api_dict(),
                "items": items,
            }
        ]
    }


# --------------------------------------------------------------------------- #
# 起运地解析
# --------------------------------------------------------------------------- #
def base_origin(dataset_origin: Dict[str, Any], settings: Settings) -> Origin:
    """起运地：优先数据文件（数据驱动），环境变量只作为纠偏覆盖。

    背景见 README §7：题目给的 2111(Ryde) 在 AusPost testbed 里配不上，
    `suburb=RYDE&postcode=2111` 会 400，但 `GLADESVILLE`/`2111` 可以。
    """
    return Origin(
        suburb=(settings.origin_suburb or str(dataset_origin.get("suburb") or "")).strip().upper(),
        state=(settings.origin_state or str(dataset_origin.get("state") or "")).strip().upper(),
        postcode=(settings.origin_postcode or str(dataset_origin.get("postcode") or "")).strip(),
        country=str(dataset_origin.get("country") or "AU").strip().upper(),
    )


async def origin_candidates(client: AusPostClient, dataset_origin: Dict[str, Any], settings: Settings) -> Tuple[List[Origin], Optional[str]]:
    """返回待尝试的 origin 列表 + 地址校验说明。

    1) 环境变量 / 数据文件给出的 suburb 先排最前；
    2) 用 GET /address 校验，若 `found:false`，把 partner 给的候选 suburb
       （GLADESVILLE / HENLEY / …）插到前面，逐个重试，最多 MAX_ORIGIN_ATTEMPTS 个。
    """
    primary = base_origin(dataset_origin, settings)
    suburbs: List[str] = []
    for candidate in (
        primary.suburb,
        str(dataset_origin.get("suburb") or "").strip().upper(),
    ):
        if candidate and candidate not in suburbs:
            suburbs.append(candidate)

    note: Optional[str] = None
    if suburbs and primary.postcode:
        result = await client.lookup_address(suburbs[0], primary.state, primary.postcode)
        if result.ok:
            parsed = parse_address_lookup(result.payload or {})
            if parsed["found"] is False:
                extra = [name for name in parsed["candidates"] if name and name.upper() not in suburbs]
                suburbs = extra[: MAX_ORIGIN_ATTEMPTS - 1] + suburbs
                note = (
                    f"起运地 {suburbs[-1]} / {primary.postcode} 未通过 AusPost 地址校验，"
                    f"改用 partner 返回的候选 suburb 重试"
                )
            elif parsed["found"] is True:
                note = f"起运地 {suburbs[0]} / {primary.postcode} 已通过 AusPost 地址校验"
        else:
            note = f"地址校验接口不可用（{result.reason_code}），按配置的 suburb 直接报价"

    origins = [
        Origin(suburb=name.upper(), state=primary.state, postcode=primary.postcode, country=primary.country)
        for name in suburbs[:MAX_ORIGIN_ATTEMPTS]
    ]
    return (origins or [primary]), note


# --------------------------------------------------------------------------- #
# 报价
# --------------------------------------------------------------------------- #
def _empty_quote() -> Dict[str, Any]:
    return {
        "mode": MODE_DEGRADED,
        "origin_used": None,
        "strategy": None,
        "carton": None,
        "request_count": 0,
        "total_cost": None,
        "total_cost_ex_gst": None,
        "total_gst": None,
        "shipping_cost": None,
        "fuel_surcharge": None,
        "cost_ex_gst": None,
        "cost_gst": None,
        "gst_treatment": None,
        "http_status": None,
        "reason_code": None,
        "message": None,
        "errors": [],
        "attempts": [],
        "note": None,
    }


def _interpret_quote(summary: Dict[str, Any], rules_gst_rate: Decimal) -> Dict[str, Any]:
    """把 partner 的 shipment_summary 翻译成面向订单面单的字段。

    我们的 four-line 结构是 Subtotal(ex GST) + GST + Shipment Fee(ex GST) = Total，
    所以 shipment fee 取 **ex GST**，其中的 GST 部分并入 GST 行，避免重复计税。
    """
    total_cost = summary.get("total_cost")
    total_ex = summary.get("total_cost_ex_gst")
    total_gst = summary.get("total_gst")
    includes_gst = summary.get("includes_gst")

    gst_treatment = "unknown"
    if total_ex is not None:
        fee_ex = money(total_ex)
        fee_gst = money((total_cost - total_ex) if total_cost is not None else total_ex * rules_gst_rate)
        gst_treatment = "ex_gst_field"
    elif includes_gst is False and total_cost is not None:
        fee_ex = money(total_cost)
        fee_gst = ZERO
        gst_treatment = "already_ex_gst"
    elif total_cost is not None:
        # AusPost 报价通常含 GST；没有 ex GST 字段时按含税反算，并在 note 里说明
        divisor = Decimal("1") + rules_gst_rate
        fee_ex = money(Decimal(str(total_cost)) / divisor)
        fee_gst = money(Decimal(str(total_cost)) - fee_ex)
        gst_treatment = "assumed_gst_inclusive"
    else:  # pragma: no cover - partner 没给任何金额
        raise ValueError("shipment_summary 里没有可用金额字段")

    return {
        "cost_ex_gst": money_str(fee_ex),
        "cost_gst": money_str(fee_gst),
        "total_cost": None if total_cost is None else money_str(total_cost),
        "total_cost_ex_gst": None if total_ex is None else money_str(total_ex),
        "total_gst": None if total_gst is None else money_str(total_gst),
        "shipping_cost": None if summary.get("shipping_cost") is None else money_str(summary["shipping_cost"]),
        "fuel_surcharge": None if summary.get("fuel_surcharge") is None else money_str(summary["fuel_surcharge"]),
        "gst_treatment": gst_treatment,
    }


async def quote_group(
    client: AusPostClient,
    group: ShipmentGroup,
    settings: Settings,
    gst_rate: Decimal,
) -> Dict[str, Any]:
    quote = _empty_quote()

    # 没匹配到 SKU（因此没有尺寸/重量）的行不能进 partner 请求；
    # 但只要还剩下可报价的行，整票货照样报价，并把被跳过的 SKU 记到 issues 里。
    priceable = [line for line in group.lines if line.has_dimensions and line.qty > 0]
    skipped = [line.sku or "<空 SKU>" for line in group.lines if line not in priceable]
    if skipped:
        group.issues.append(f"SKIPPED_WITHOUT_DIMENSIONS:{','.join(skipped)}")
    if not priceable:
        quote["reason_code"] = "NO_PRICEABLE_ITEMS"
        quote["message"] = "该分组没有可用于报价的行项目" + (
            f"（缺少尺寸/重量的 SKU：{', '.join(skipped)}）" if skipped else ""
        )
        return quote

    if not group.destination.is_complete:
        quote["reason_code"] = "DESTINATION_INCOMPLETE"
        quote["message"] = "订单缺少 suburb/state/postcode，无法向 AusPost 报价"
        return quote

    origins, note = await origin_candidates(client, group.dataset_origin, settings)
    quote["note"] = note

    attempts: List[Dict[str, Any]] = []
    last_result: Optional[ApiResult] = None

    for origin in origins:
        # 策略 A：整箱（一次真实调用）
        carton = build_single_carton(priceable)
        body = _price_body(origin, group.destination, [carton])
        result = await client.price_shipments(body)
        attempts.append(
            {
                "suburb": origin.suburb,
                "strategy": "single_carton",
                "carton_cm": {k: carton[k] for k in ("length", "width", "height")},
                "carton_kg": carton["weight"],
                "http_status": result.http_status,
                "reason_code": result.reason_code,
            }
        )
        quote["request_count"] += 1
        if result.ok:
            try:
                summary = parse_shipment_quote(result.payload or {})
                interpreted = _interpret_quote(summary, gst_rate)
            except ValueError as exc:
                quote.update(
                    mode=MODE_DEGRADED,
                    reason_code=auspost.REASON_BAD_JSON,
                    message=str(exc),
                    http_status=result.http_status,
                    attempts=attempts,
                    origin_used=origin.as_dict(),
                )
                return quote
            quote.update(
                mode=MODE_LIVE,
                reason_code=auspost.REASON_OK,
                http_status=result.http_status,
                strategy="single_carton",
                carton={k: carton[k] for k in ("length", "width", "height", "weight")},
                request_count=quote["request_count"],
                origin_used=origin.as_dict(),
                attempts=attempts,
                **interpreted,
            )
            return quote
        last_result = result

        # 策略 B：逐件报价再求和（依然是真实调用，不是本地公式）
        entries = build_per_item_entries(priceable)
        total_ex = ZERO
        total_gst_part = ZERO
        total_cost = ZERO
        per_item_ok = True
        attempt: Dict[str, Any] = {
            "suburb": origin.suburb,
            "strategy": "per_item_sum",
            "http_status": None,
            "reason_code": None,
        }
        attempts.append(attempt)
        last_http: Optional[int] = None
        for entry in entries:
            body = _price_body(origin, group.destination, [entry])
            result = await client.price_shipments(body)
            quote["request_count"] += 1
            last_http = result.http_status
            if not result.ok:
                per_item_ok = False
                attempt.update(http_status=result.http_status, reason_code=result.reason_code)
                last_result = result
                break
            try:
                summary = parse_shipment_quote(result.payload or {})
            except ValueError:
                per_item_ok = False
                attempt.update(http_status=result.http_status, reason_code=auspost.REASON_BAD_JSON)
                break
            interpreted = _interpret_quote(summary, gst_rate)
            total_ex += to_decimal(interpreted["cost_ex_gst"]) or ZERO
            total_gst_part += to_decimal(interpreted["cost_gst"]) or ZERO
            total_cost += to_decimal(interpreted["total_cost"]) or ZERO
        if per_item_ok:
            attempt.update(http_status=last_http, reason_code=auspost.REASON_OK)
            quote.update(
                mode=MODE_LIVE,
                reason_code=auspost.REASON_OK,
                http_status=last_http,
                strategy="per_item_sum",
                origin_used=origin.as_dict(),
                attempts=attempts,
                cost_ex_gst=money_str(total_ex),
                cost_gst=money_str(total_gst_part),
                total_cost=money_str(total_cost),
                total_cost_ex_gst=money_str(total_ex),
                total_gst=money_str(total_gst_part),
                gst_treatment="ex_gst_field",
            )
            return quote

    last = last_result
    quote.update(
        mode=MODE_DEGRADED,
        http_status=getattr(last, "http_status", None),
        reason_code=getattr(last, "reason_code", None) or "QUOTE_FAILED",
        message=getattr(last, "message", None) or "partner API 未返回可用报价",
        errors=last.api_errors if last is not None else [],
        attempts=attempts,
        note=(quote["note"] or "")
        or "已尝试两种方式（整箱 / 逐件求和）向 AusPost 报价，均未成功，运费未计入订单总额",
    )
    logger.warning("shipment quote degraded group=%s attempts=%s", group.label, attempts)
    return quote


async def quote_groups(client: AusPostClient, groups: Sequence[ShipmentGroup], settings: Settings, gst_rate: Decimal) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    """并发报价；返回 {group.key: quote}。"""
    tasks = [quote_group(client, group, settings, gst_rate) for group in groups]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for group, result in zip(groups, results):
        if isinstance(result, BaseException):
            quote = _empty_quote()
            quote.update(reason_code="UNEXPECTED_ERROR", message=f"{type(result).__name__}: {result}")
            output[group.key] = quote
        else:
            output[group.key] = result
    return output


# --------------------------------------------------------------------------- #
# 轨迹
# --------------------------------------------------------------------------- #
async def fetch_tracking(client: AusPostClient, group: ShipmentGroup) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "tracking_id": group.tracking_id,
        "courier": group.courier,
        "mode": MODE_DEGRADED,
        "status": "",
        "statuses": [],
        "event_count": 0,
        "events": [],
        "articles": [],
        "reason_code": None,
        "message": None,
        "http_status": None,
        "errors": [],
    }
    if not group.tracking_id:
        payload.update(reason_code="NO_TRACKING_NUMBER", message="该分组没有运单号")
        return payload

    result = await client.track(group.tracking_id)
    if not result.ok:
        payload.update(
            http_status=result.http_status,
            reason_code=result.reason_code,
            message=result.message,
            errors=result.api_errors,
        )
        return payload

    try:
        info = parse_tracking(group.tracking_id, result.payload or {})
    except ValueError as exc:
        payload.update(reason_code=auspost.REASON_BAD_JSON, message=str(exc), http_status=result.http_status)
        return payload

    payload.update(
        mode=MODE_LIVE,
        status=info.status,
        statuses=info.statuses,
        event_count=len(info.events),
        events=[event.as_dict() for event in info.events],
        articles=info.articles,
        http_status=result.http_status,
        reason_code=auspost.REASON_OK,
    )
    if not info.events:
        payload["message"] = "partner API 未返回任何轨迹事件"
    return payload


async def fetch_trackings(client: AusPostClient, groups: Sequence[ShipmentGroup]) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    tasks = [fetch_tracking(client, group) for group in groups]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for group, result in zip(groups, results):
        if isinstance(result, BaseException):
            output[(group.dataset, group.label)] = {
                "tracking_id": group.tracking_id,
                "courier": group.courier,
                "mode": MODE_DEGRADED,
                "status": "",
                "statuses": [],
                "event_count": 0,
                "events": [],
                "articles": [],
                "reason_code": "UNEXPECTED_ERROR",
                "message": f"{type(result).__name__}: {result}",
                "http_status": None,
                "errors": [],
            }
        else:
            output[group.key] = result
    return output


# --------------------------------------------------------------------------- #
# 由数据集构建分组
# --------------------------------------------------------------------------- #
UNASSIGNED_LABEL = "UNASSIGNED"


def parse_qty(raw: Any) -> Tuple[Optional[int], Optional[str]]:
    """数量校验：必须是正整数。不合法返回 (None, 原因)。"""
    if raw is None:
        return None, "QTY_MISSING"
    if isinstance(raw, bool):
        return None, "QTY_INVALID"
    if isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip()
        if not text:
            return None, "QTY_MISSING"
        try:
            value = int(text)
        except ValueError:
            try:
                as_decimal = Decimal(text)
            except Exception:  # noqa: BLE001
                return None, "QTY_INVALID"
            if as_decimal != as_decimal.to_integral_value():
                return None, "QTY_INVALID"
            value = int(as_decimal)
    if value <= 0:
        return None, "QTY_INVALID"
    return value, None


def build_groups(
    datasets: Iterable[Dataset],
    catalog: SkuCatalog,
    settings: Settings,
) -> Tuple[List[ShipmentGroup], Dict[Tuple[str, str], List[Tuple[str, str, Tuple[str, str, str]]]]]:
    """按 (数据集, Track 标签, 目的地) 聚合行项目 -> 一票货。

    同一个 Track 若出现在不同收货地址上会被拆成多票（真实业务里也是如此），
    这样"按 Track 分组"不会因为一票多址而丢件。
    """
    groups: Dict[Tuple[str, str, Tuple[str, str, str]], ShipmentGroup] = {}
    order_group_keys: Dict[Tuple[str, str], List[Tuple[str, str, Tuple[str, str, str]]]] = {}
    for dataset in datasets:
        tracking_map = dataset.tracking_map
        for order in dataset.orders:
            order_no = str(order.get(settings.order_no_field) or "").strip()
            destination = Destination(
                suburb=str(order.get("suburb") or "").strip().upper(),
                state=str(order.get("state") or "").strip().upper(),
                postcode=str(order.get("postcode") or "").strip(),
                country=str(order.get("country") or "AU").strip().upper(),
            )
            raw_items = order.get(settings.items_field)
            if not isinstance(raw_items, list):
                continue
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                sku_code = str(raw_item.get(settings.item_sku_field) or "").strip()
                qty, qty_issue = parse_qty(raw_item.get(settings.item_qty_field))
                label = str(raw_item.get(settings.item_tracking_field) or "").strip() or UNASSIGNED_LABEL
                tracking_meta = tracking_map.get(label, {})

                record: Optional[SkuRecord] = catalog.get(sku_code)
                issues: List[str] = []
                if record is None:
                    issues.append("SKU_NOT_FOUND")
                if qty_issue:
                    issues.append(qty_issue)
                if not destination.is_complete:
                    issues.append("DESTINATION_INCOMPLETE")

                quantity = qty or 0
                line = ParcelLine(
                    sku=sku_code,
                    name=record.product_name if record else "",
                    qty=quantity,
                    weight_g=record.weight_g if record else None,
                    length_mm=record.length_mm if record else None,
                    width_mm=record.width_mm if record else None,
                    height_mm=record.height_mm if record else None,
                    volume_mm3=record.volume_mm3 if record else None,
                    issues=issues,
                )

                key = (dataset.name, label, destination.fingerprint)
                group = groups.get(key)
                if group is None:
                    group = ShipmentGroup(
                        label=label,
                        dataset=dataset.name,
                        dataset_origin=dataset.origin,
                        destination=destination,
                        tracking_id=str(tracking_meta.get("number") or "").strip() or None,
                        courier=str(tracking_meta.get("courier") or "").strip(),
                        issues=[],
                    )
                    groups[key] = group
                group.lines.append(line)
                if order_no and order_no not in group.order_numbers:
                    group.order_numbers.append(order_no)

                bucket = order_group_keys.setdefault((dataset.name, order_no), [])
                if key not in bucket:
                    bucket.append(key)
    return list(groups.values()), order_group_keys
