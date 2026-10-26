"""测试脚手架：离线 fixture + 可插拔的假 AusPost 客户端。

业务测试的 partner 返回用 data/auspost/*.json 里的**真实响应样例**驱动，
保证解析器跑的是真数据结构，而不是自己臆想的结构。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from app.config import Settings, get_settings
from app.data_loader import JsonStore, load_catalog, load_datasets
from app.deps import get_cache, get_store
from app.services.auspost import MODE_DEGRADED, ApiResult, failure
from app.services import orders as orders_service

DATA_DIR = get_settings().data_dir
AUSPOST_SAMPLES = DATA_DIR / "auspost"

LIVE_PRICE_TOTAL = "71.04"
LIVE_PRICE_EX_GST = "64.58"
LIVE_PRICE_GST = "6.46"


def live_price_payload(total: str = LIVE_PRICE_TOTAL, ex_gst: str = LIVE_PRICE_EX_GST, gst: str = LIVE_PRICE_GST) -> Dict[str, Any]:
    """实测记录的真实 shipment_summary 数值。"""
    return {
        "shipments": [
            {
                "shipment_summary": {
                    "total_cost": total,
                    "total_cost_ex_gst": ex_gst,
                    "total_gst": gst,
                    "shipping_cost": "54.04",
                    "fuel_surcharge": "10.54",
                }
            }
        ]
    }


def sample_track_payload(tracking_id: str) -> Dict[str, Any]:
    path = AUSPOST_SAMPLES / f"track_{tracking_id}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"tracking_results": [{"tracking_id": str(tracking_id), "status": "", "trackable_items": []}]}


class FakeAusPostClient:
    """替代真实 HTTP 客户端：同接口、同样返回 ApiResult。"""

    def __init__(
        self,
        settings: Settings,
        cache: Any = None,
        *,
        price_result: Optional[ApiResult] = None,
        track_result: Optional[ApiResult] = None,
        address_result: Optional[ApiResult] = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.price_result = price_result or ApiResult(MODE_DEGRADED, reason_code="NOT_CONFIGURED", message="test")
        self.track_result = track_result
        self.address_result = address_result
        self.price_bodies: list = []

    async def __aenter__(self) -> "FakeAusPostClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def price_shipments(self, body: Dict[str, Any]) -> ApiResult:
        self.price_bodies.append(body)
        return self.price_result

    async def track(self, tracking_id: str) -> ApiResult:
        if self.track_result is not None:
            return self.track_result
        return ApiResult("live", payload=sample_track_payload(tracking_id), http_status=200, reason_code="OK")

    async def lookup_address(self, suburb: str, state: str, postcode: str) -> ApiResult:
        if self.address_result is not None:
            return self.address_result
        # 默认：地址校验通过（真实环境由 /address 接口决定）
        return ApiResult("live", payload={"address": {"found": True, "suburb": suburb}}, http_status=200, reason_code="OK")


def install_fake_client(monkeypatch, factory) -> None:
    """把假客户端装到所有会用到它的位置（仓库层 + 路由层）。"""
    from app.routers import orders as orders_router  # 局部导入避免循环依赖

    monkeypatch.setattr(orders_service, "AusPostClient", factory)
    monkeypatch.setattr(orders_router, "AusPostClient", factory)


@pytest.fixture(autouse=True)
def hermetic_credentials(monkeypatch):
    """测试不依赖本机 .env / 环境变量：默认把凭证置空，避免机器环境串味。"""
    from app import config

    monkeypatch.setattr(config, "_DOTENV_VALUES", {})
    for name in ("AUSPOST_API_KEY", "AUSPOST_API_PASSWORD", "AUSPOST_ACCOUNT_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_api_cache():
    get_cache().clear()
    yield
    get_cache().clear()


@pytest.fixture()
def settings() -> Settings:
    return get_settings()


@pytest.fixture()
def store(settings: Settings) -> JsonStore:
    return get_store(settings)


@pytest.fixture()
def datasets(settings: Settings, store: JsonStore):
    return load_datasets(store, settings)


@pytest.fixture()
def catalog(settings: Settings, store: JsonStore):
    return load_catalog(store, settings)


@pytest.fixture()
def main_dataset(datasets):
    return next(dataset for dataset in datasets if dataset.name == "orders.json")


@pytest.fixture()
def edge_settings(settings: Settings) -> Settings:
    """把数据范围切到边界用例数据集（生产默认只加载 orders.json，边界数据不进生产链路）。"""
    return settings.with_test_dataset()
