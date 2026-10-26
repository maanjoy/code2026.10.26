"""纯金额计算：不认识 SKU、不认识订单号，只认 Decimal。

规则（见 README §3 金额口径与舍入）：
    Ex-GST 单价   = RRP / (1 + GST 税率)
    行小计        = Ex-GST 单价 × 数量          （单价先舍入到分，再乘数量）
    Subtotal      = Σ 行小计
    GST           = Subtotal × 税率
    Order Total   = Subtotal + GST + 运费

税率作为 `PricingRules` 传入，不写死在函数里。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, List, Tuple

from app.services.money import ZERO, money, to_decimal

DEFAULT_GST_RATE = Decimal("0.10")


@dataclass(frozen=True)
class PricingRules:
    gst_rate: Decimal = DEFAULT_GST_RATE

    @property
    def gst_divisor(self) -> Decimal:
        return Decimal("1") + self.gst_rate


@dataclass(frozen=True)
class LinePricing:
    qty: int
    unit_price_ex_gst: Decimal
    line_total_ex_gst: Decimal


@dataclass(frozen=True)
class MoneyBreakdown:
    subtotal_ex_gst: Decimal
    gst: Decimal
    shipment_fee: Decimal

    @property
    def total(self) -> Decimal:
        return money(self.subtotal_ex_gst + self.gst + self.shipment_fee)


def unit_price_ex_gst(rrp_incl_gst: Any, rules: PricingRules) -> Decimal:
    """含 GST 单价 -> Ex-GST 单价，**先舍入到分**（这是与"先乘后舍入"差钱的关键）。"""
    rrp = to_decimal(rrp_incl_gst)
    if rrp is None:
        raise ValueError("rrp_incl_gst 不是合法数字")
    return money(rrp / rules.gst_divisor)


def line_total_ex_gst(unit_ex_gst: Decimal, qty: int) -> Decimal:
    return money(money(unit_ex_gst) * Decimal(int(qty)))


def price_line(rrp_incl_gst: Any, qty: int, rules: PricingRules) -> LinePricing:
    unit = unit_price_ex_gst(rrp_incl_gst, rules)
    return LinePricing(qty=int(qty), unit_price_ex_gst=unit, line_total_ex_gst=line_total_ex_gst(unit, qty))


def subtotal_ex_gst(lines: Iterable[LinePricing]) -> Decimal:
    return money(sum((line.line_total_ex_gst for line in lines), ZERO))


def gst_amount(subtotal: Decimal, rules: PricingRules) -> Decimal:
    return money(money(subtotal) * rules.gst_rate)


def breakdown(lines: Iterable[LinePricing], rules: PricingRules, shipment_fee: Any = ZERO) -> MoneyBreakdown:
    lines = list(lines)
    subtotal = subtotal_ex_gst(lines)
    return MoneyBreakdown(
        subtotal_ex_gst=subtotal,
        gst=gst_amount(subtotal, rules),
        shipment_fee=money(shipment_fee),
    )


#: 另一种（不采用的）舍入顺序，仅用于测试/文档演示两者确实会差钱。
def line_total_round_after_multiply(rrp_incl_gst: Any, qty: int, rules: PricingRules) -> Decimal:
    rrp = to_decimal(rrp_incl_gst)
    if rrp is None:
        raise ValueError("rrp_incl_gst 不是合法数字")
    return money((rrp / rules.gst_divisor) * Decimal(int(qty)))


__all__: List[str] = [
    "LinePricing",
    "MoneyBreakdown",
    "PricingRules",
    "breakdown",
    "gst_amount",
    "line_total_ex_gst",
    "line_total_round_after_multiply",
    "price_line",
    "subtotal_ex_gst",
    "unit_price_ex_gst",
]
