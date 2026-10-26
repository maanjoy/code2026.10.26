"""金额工具。

全局约定：
  * 一切金额运算用 `decimal.Decimal`，**禁止使用 Python 内置 round()**
    （它是银行家舍入，`round(2.5) == 2`，且浮点二进制表示不精确）。
  * 统一 `ROUND_HALF_UP` 舍入到分。
  * `Decimal(str(x))` 而不是 `Decimal(x)`，避免把 float 的误差带进来。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """把任意输入（str/int/float/Decimal）安全地转成 Decimal。"""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return default
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return default


def money(value: Any) -> Decimal:
    """舍入到分，ROUND_HALF_UP。"""
    amount = to_decimal(value, ZERO)
    if amount is None:
        amount = ZERO
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def money_str(value: Any) -> str:
    """对外序列化的金额字符串，保留两位小数（避免 JSON float 精度丢失）。"""
    return f"{money(value):.2f}"
