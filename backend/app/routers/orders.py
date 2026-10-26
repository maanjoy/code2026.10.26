"""订单 / 运费 / 轨迹 API。

前端**只**调这些接口，永远拿不到 AusPost 凭证，也不会直连 partner API。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.deps import get_cache, get_store
from app.services import shipment as shipment_service
from app.services.auspost import AusPostClient
from app.services.orders import OrderRepository

router = APIRouter(prefix="/api", tags=["orders"])


def repository(settings: Settings = Depends(get_settings)) -> OrderRepository:
    return OrderRepository(settings, get_store(settings), get_cache())


@router.get("/orders", summary="订单列表（含每单金额、运费、轨迹）")
async def list_orders(repo: OrderRepository = Depends(repository)) -> Dict[str, Any]:
    """生产链路：只返回 `ORDER_DATASET_PATTERN` 命中的真实订单数据集。

    故意造坏的边界数据集（`TEST_DATASET_PATTERN`，默认 `testcases_edge.json`）不在生产范围内，
    只走下面的 `/api/testcases`，且该接口默认关闭。
    """
    try:
        return await repo.list_orders()
    except Exception as exc:  # noqa: BLE001 - 输入数据问题要给出可读错误
        raise HTTPException(status_code=500, detail=f"读取订单数据失败：{exc}") from exc


@router.get("/testcases", summary="边界用例（仅当 EXPOSE_TESTCASES=true 时可用）")
async def list_test_cases(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    """测试链路：用**同一套业务逻辑**跑边界数据集，供测试页 / 手工验证使用。

    生产交付默认关闭（404），需要时设置 `EXPOSE_TESTCASES=true` 打开。
    """
    if not settings.expose_testcases:
        raise HTTPException(
            status_code=404,
            detail="边界用例接口未开启：设置 EXPOSE_TESTCASES=true 后即可访问（生产默认关闭）",
        )
    test_settings = settings.with_test_dataset()
    store = get_store(test_settings)
    repo = OrderRepository(test_settings, store, get_cache())
    try:
        return await repo.list_orders()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取边界用例数据失败：{exc}") from exc


@router.get("/orders/{order_no}", summary="单个订单详情")
async def get_order(order_no: str, repo: OrderRepository = Depends(repository)) -> Dict[str, Any]:
    order = await repo.get_order(order_no)
    if order is None:
        raise HTTPException(status_code=404, detail=f"找不到订单 {order_no}")
    return order


@router.get("/trackings", summary="全部运单的轨迹与报价")
async def list_trackings(repo: OrderRepository = Depends(repository)) -> Dict[str, Any]:
    return await repo.list_trackings()


@router.get("/tracking/{tracking_id}", summary="按运单号查轨迹")
async def get_tracking(
    tracking_id: str,
    refresh: bool = Query(False, description="跳过缓存重新请求 partner API"),
    repo: OrderRepository = Depends(repository),
) -> Dict[str, Any]:
    """独立查询某个运单号：给前端用，也方便手工验证题目给出的三个运单号。"""
    settings = get_settings()
    group = shipment_service.ShipmentGroup(
        label="adhoc",
        dataset="adhoc",
        dataset_origin={},
        destination=shipment_service.Destination(suburb="", state="", postcode=""),
        tracking_id=tracking_id,
        courier="adhoc",
    )
    if refresh:
        get_cache().clear()
    async with AusPostClient(settings, get_cache()) as client:
        tracking = await shipment_service.fetch_tracking(client, group)
    return tracking
