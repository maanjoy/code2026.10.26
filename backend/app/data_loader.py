"""本地 JSON 数据层（按题目要求：不使用数据库）。

数据一律从 `data/` 下的 JSON 文件读取，**没有任何订单号 / SKU / 价格写死在代码里**：
换一份同结构的 orders JSON 进来，业务逻辑一行都不用改。

读取带 mtime 感知缓存：开发时改完 JSON 立刻生效，不需要重启后端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.services.money import to_decimal


class DataError(RuntimeError):
    """输入数据结构不合法。"""


class JsonStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._cache: Dict[str, tuple] = {}

    def load(self, filename: str) -> Dict[str, Any]:
        path = self.data_dir / filename
        if not path.is_file():
            raise DataError(f"找不到数据文件：{path}")
        mtime = path.stat().st_mtime_ns
        cached = self._cache.get(str(path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            import json

            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except ValueError as exc:
            raise DataError(f"{path.name} 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise DataError(f"{path.name} 顶层必须是 JSON 对象")
        self._cache[str(path)] = (mtime, payload)
        return payload

    def list_files(self, pattern: str) -> List[Path]:
        return sorted(self.data_dir.glob(pattern), key=lambda item: item.name)


@dataclass(frozen=True)
class SkuRecord:
    sku: str
    product_name: str
    rrp_incl_gst: Optional[Decimal]
    weight_g: Optional[Decimal]
    length_mm: Optional[Decimal]
    width_mm: Optional[Decimal]
    height_mm: Optional[Decimal]
    volume_mm3: Optional[Decimal] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def missing_fields(self) -> List[str]:
        missing = []
        if self.rrp_incl_gst is None:
            missing.append("rrp")
        if self.weight_g is None:
            missing.append("weight_g")
        for name in ("length_mm", "width_mm", "height_mm"):
            if getattr(self, name) is None:
                missing.append(name)
        return missing


class SkuCatalog:
    """SKU -> 产品记录。按大写 SKU 建索引，匹配时忽略大小写与首尾空格。"""

    def __init__(self, records: Dict[str, SkuRecord]) -> None:
        self._records = records

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any]) -> "SkuCatalog":
        records: Dict[str, SkuRecord] = {}
        for key, value in (mapping or {}).items():
            if not isinstance(value, dict):
                continue
            sku = str(value.get("sku") or key).strip()
            records[sku.upper()] = SkuRecord(
                sku=sku,
                product_name=str(value.get("product_name") or value.get("ProductName") or "").strip(),
                rrp_incl_gst=to_decimal(value.get("rrp")),
                weight_g=to_decimal(value.get("weight_g")),
                length_mm=to_decimal(value.get("length_mm")),
                width_mm=to_decimal(value.get("width_mm")),
                height_mm=to_decimal(value.get("height_mm")),
                volume_mm3=to_decimal(value.get("volume_mm3")),
                raw=value,
            )
        return cls(records)

    def __len__(self) -> int:
        return len(self._records)

    def get(self, sku: Any) -> Optional[SkuRecord]:
        if sku is None:
            return None
        return self._records.get(str(sku).strip().upper())


@dataclass(frozen=True)
class Dataset:
    """一份订单数据源文件。"""

    path: Path
    raw: Dict[str, Any]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def origin(self) -> Dict[str, Any]:
        origin = self.raw.get("origin")
        return origin if isinstance(origin, dict) else {}

    @property
    def tracking_map(self) -> Dict[str, Dict[str, Any]]:
        tracking = self.raw.get("tracking")
        if not isinstance(tracking, dict):
            return {}
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in tracking.items()}

    @property
    def orders(self) -> List[Dict[str, Any]]:
        orders = self.raw.get("orders")
        if not isinstance(orders, list):
            raise DataError(f"{self.name} 缺少合法的 orders 数组")
        return [order for order in orders if isinstance(order, dict)]


def load_datasets(store: JsonStore, settings: Settings) -> List[Dataset]:
    """按 `ORDER_DATASET_PATTERN` 载入订单数据集（生产默认只匹配 `orders.json`）。

    边界用例数据集由 `TEST_DATASET_PATTERN` 单独指定，只在测试链路里加载。
    """
    paths = store.list_files(settings.order_dataset_pattern)
    if not paths:
        raise DataError(f"{store.data_dir} 下没有匹配 {settings.order_dataset_pattern!r} 的文件")
    return [Dataset(path=path, raw=store.load(path.name)) for path in paths]


def load_catalog(store: JsonStore, settings: Settings) -> SkuCatalog:
    return SkuCatalog.from_mapping(store.load(settings.sku_catalog_file))
