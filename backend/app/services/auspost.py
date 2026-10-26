"""Australia Post / StarTrack testbed 客户端。

接口细节为本机实测结论（见 README §4）：
  * Base URL : <AUSPOST_BASE_URL>，默认值见 config.py
  * 认证     : HTTP Basic（username = API Key，password = Password）
  * 额外头部 : `account-number: <账号号>`（缺了会 400）
  * 轨迹     : GET  /track?tracking_ids=...
  * 运费     : POST /prices/shipments
  * 地址校验 : GET  /address?suburb=&state=&postcode=

**默认走真实调用**。只有在超时 / 非 2xx / 传输异常 / 凭证缺失时才进入降级态，
并且降级态会把 `http_status` + reason + partner 返回的 error 原样透出给前端，
UI 上必须明确显示"运费未计入""轨迹不可用"，而不是假装成 0。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.config import Settings
from app.services.cache import TTLCache

logger = logging.getLogger(__name__)

MODE_LIVE = "live"
MODE_DEGRADED = "degraded"

REASON_OK = "OK"
REASON_TIMEOUT = "TIMEOUT"
REASON_TRANSPORT = "TRANSPORT_ERROR"
REASON_HTTP_ERROR = "HTTP_ERROR"
REASON_API_ERRORS = "API_RETURNED_ERRORS"
REASON_BAD_JSON = "BAD_JSON_RESPONSE"
REASON_NO_CREDENTIALS = "CREDENTIALS_MISSING"
REASON_NOT_FOUND = "NO_RESULT_FOR_ID"


@dataclass(frozen=True)
class ApiResult:
    """partner API 调用结果。mode 只有 live / degraded 两种。"""

    mode: str
    payload: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    reason_code: Optional[str] = None
    message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.mode == MODE_LIVE

    @property
    def api_errors(self) -> List[Dict[str, str]]:
        """partner 自己的错误体（errors / error_code 两种风格都兼容）。"""
        if not isinstance(self.payload, dict):
            return []
        raw_errors = self.payload.get("errors")
        if not isinstance(raw_errors, list):
            return []
        parsed: List[Dict[str, str]] = []
        for item in raw_errors:
            if not isinstance(item, dict):
                continue
            parsed.append(
                {
                    "code": str(item.get("code") or item.get("error_code") or ""),
                    "name": str(item.get("name") or item.get("error_name") or ""),
                    "message": str(item.get("message") or ""),
                }
            )
        return parsed


def failure(reason_code: str, message: str, *, http_status: Optional[int] = None, payload: Optional[dict] = None) -> ApiResult:
    return ApiResult(
        mode=MODE_DEGRADED,
        payload=payload,
        http_status=http_status,
        reason_code=reason_code,
        message=message,
    )


# --------------------------------------------------------------------------- #
# 金额与轨迹解析
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TrackingEvent:
    date: str
    location: str
    description: str

    def as_dict(self) -> Dict[str, str]:
        return {"date": self.date, "location": self.location, "description": self.description}


@dataclass(frozen=True)
class TrackingInfo:
    tracking_id: str
    status: str
    events: List[TrackingEvent]
    articles: List[Dict[str, str]]
    statuses: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tracking_id": self.tracking_id,
            "status": self.status,
            "statuses": self.statuses,
            "event_count": len(self.events),
            "events": [event.as_dict() for event in self.events],
            "articles": self.articles,
        }


def _parse_iso(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_sort_key(event: TrackingEvent) -> float:
    parsed = _parse_iso(event.date)
    if parsed is None:
        return float("-inf")
    return parsed.timestamp()


def _collect_events(raw_events: Iterable[Any]) -> List[TrackingEvent]:
    events: List[TrackingEvent] = []
    seen = set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event = TrackingEvent(
            date=str(raw.get("date") or ""),
            location=str(raw.get("location") or ""),
            description=str(raw.get("description") or ""),
        )
        key = (event.date, event.location, event.description)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
    events.sort(key=_event_sort_key, reverse=True)
    return events


def parse_tracking(tracking_id: str, payload: Dict[str, Any]) -> TrackingInfo:
    """兼容实测到的两种响应结构：

    A. tracking_results[].consignment{status, events[]}（多件包裹）
    B. tracking_results[].{status, trackable_items[].{article_id, product_type,
       status, events[]}}（单件 / TNT 单号打到 AusPost 的情况）
    """
    results = payload.get("tracking_results")
    if not isinstance(results, list) or not results:
        raise ValueError("响应中没有 tracking_results")

    wanted = str(tracking_id).strip().upper()
    selected = None
    for item in results:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("tracking_id") or "").strip().upper()
        if candidate == wanted:
            selected = item
            break
    if selected is None:
        selected = results[0] if isinstance(results[0], dict) else {}

    consignment = selected.get("consignment")
    consignment = consignment if isinstance(consignment, dict) else {}
    trackable_items = selected.get("trackable_items")
    trackable_items = (
        [item for item in trackable_items if isinstance(item, dict)]
        if isinstance(trackable_items, list)
        else []
    )

    statuses: List[str] = []

    def remember(status: Any) -> Optional[str]:
        if isinstance(status, str) and status.strip():
            statuses.append(status.strip())
            return status.strip()
        return None

    raw_events: List[Any] = []
    if consignment:
        remember(consignment.get("status"))
        raw_events.extend(consignment.get("events") or [])
    for item in trackable_items:
        remember(item.get("status"))
        raw_events.extend(item.get("events") or [])
    top_level_status = remember(selected.get("status"))

    status = consignment.get("status") if consignment else None
    status = status or top_level_status or (statuses[0] if statuses else "")

    articles: List[Dict[str, str]] = [
        {
            "article_id": str(item.get("article_id") or ""),
            "product_type": str(item.get("product_type") or ""),
            "status": str(item.get("status") or ""),
        }
        for item in trackable_items
    ]

    return TrackingInfo(
        tracking_id=str(selected.get("tracking_id") or tracking_id),
        status=str(status or ""),
        events=_collect_events(raw_events),
        articles=articles,
        statuses=statuses,
    )


def _walk_dicts(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_dicts(value)


def parse_address_lookup(payload: Dict[str, Any]) -> Dict[str, Any]:
    """宽松解析 GET /address 的响应。

    实测只记录了"返回 found: true/false + 候选 suburb 列表"，没有固定的
    字段名契约，所以这里不做硬编码假设：递归找出 `found` 布尔值，并收集任何
    带 suburb / locality / name 的候选值。解析不出来就当作 unresolved，
    由上层回退到数据文件里的 origin，而不是臆造结果。
    """
    found: Optional[bool] = None
    candidates: List[str] = []
    for node in _walk_dicts(payload):
        value = node.get("found")
        if isinstance(value, bool):
            found = value
        for key, candidate in node.items():
            if key.lower() in {"suburb", "locality", "name"} and isinstance(candidate, str):
                if candidate.strip() and candidate.strip() not in candidates:
                    candidates.append(candidate.strip())
    return {"found": found, "candidates": candidates}


def parse_shipment_quote(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从 POST /prices/shipments 的响应里取 shipment_summary。"""
    node: Optional[Dict[str, Any]] = None
    for item in _walk_dicts(payload):
        if isinstance(item.get("shipment_summary"), dict):
            node = item["shipment_summary"]
            break
    if node is None:
        raise ValueError("响应缺少 shipment_summary")

    summary = node.get("shipments")
    first_shipment = summary[0] if isinstance(summary, list) and summary else None

    def decimal_of(key: str) -> Optional[Decimal]:
        value = node.get(key)
        if value is None and isinstance(first_shipment, dict):
            value = first_shipment.get(key)
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:  # noqa: BLE001 - partner 返回脏数据时不炸
            return None

    def pick_bool(key: str) -> Optional[bool]:
        value = node.get(key)
        return value if isinstance(value, bool) else None

    return {
        "total_cost": decimal_of("total_cost"),
        "total_cost_ex_gst": decimal_of("total_cost_ex_gst"),
        "total_gst": decimal_of("total_gst"),
        "shipping_cost": decimal_of("shipping_cost"),
        "fuel_surcharge": decimal_of("fuel_surcharge"),
        "includes_gst": pick_bool("includes_gst"),
    }


# --------------------------------------------------------------------------- #
# HTTP 客户端
# --------------------------------------------------------------------------- #
class AusPostClient:
    def __init__(self, settings: Settings, cache: Optional[TTLCache] = None) -> None:
        self.settings = settings
        self.cache = cache if cache is not None else TTLCache()
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AusPostClient":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.settings.http_timeout_seconds))
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> Dict[str, str]:
        return {
            "account-number": self.settings.auspost_account_number,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _auth(self) -> Optional[tuple]:
        key = self.settings.auspost_api_key
        password = self.settings.auspost_api_password
        if not key or not password:
            return None
        return (key, password)

    def _cache_key(self, method: str, url: str, params: Any, body: Any) -> str:
        raw = json.dumps(
            {"m": method, "u": url, "p": params, "b": body}, sort_keys=True, default=str
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        cache_ttl: Optional[int] = None,
    ) -> ApiResult:
        missing = self.settings.missing_credentials()
        if missing:
            return failure(
                REASON_NO_CREDENTIALS,
                f"未配置：{', '.join(missing)}（见 .env.example）",
            )

        auth = self._auth()
        if auth is None:  # pragma: no cover - 冗余保护
            return failure(REASON_NO_CREDENTIALS, "凭证为空")

        base = self.settings.auspost_base_url.rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        key = self._cache_key(method, url, params, body)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        client = self._client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(self.settings.http_timeout_seconds))
            owns_client = True

        try:
            response = await client.request(
                method, url, params=params, json=body, headers=self._headers(), auth=auth
            )
        except httpx.TimeoutException as exc:
            result = failure(REASON_TIMEOUT, f"partner API 超时（>{self.settings.http_timeout_seconds}s）：{exc}")
            self.cache.set(key, result, self.settings.degraded_cache_ttl_seconds)
            logger.warning("auspost timeout path=%s", path)
            return result
        except httpx.RequestError as exc:
            result = failure(REASON_TRANSPORT, f"partner API 网络异常：{exc}")
            self.cache.set(key, result, self.settings.degraded_cache_ttl_seconds)
            logger.warning("auspost transport error path=%s: %s", path, exc)
            return result
        finally:
            if owns_client:
                await client.aclose()

        try:
            payload = response.json() if response.content else {}
        except ValueError:
            result = failure(
                REASON_BAD_JSON,
                "partner API 返回了非 JSON 内容",
                http_status=response.status_code,
            )
            self.cache.set(key, result, self.settings.degraded_cache_ttl_seconds)
            return result
        if not isinstance(payload, dict):
            payload = {"raw": payload}

        if response.status_code >= 400:
            message = "partner API 返回 HTTP %s" % response.status_code
            errors = ApiResult(MODE_DEGRADED, payload=payload).api_errors
            if errors:
                message = f"{message}：{errors[0]['message'] or errors[0]['name']}"
            result = failure(REASON_HTTP_ERROR, message, http_status=response.status_code, payload=payload)
            self.cache.set(key, result, self.settings.degraded_cache_ttl_seconds)
            logger.warning("auspost http %s path=%s body=%s", response.status_code, path, payload)
            return result

        if isinstance(payload.get("errors"), list) and payload["errors"]:
            result = failure(REASON_API_ERRORS, "partner API 在响应体中返回了 errors", http_status=response.status_code, payload=payload)
            self.cache.set(key, result, self.settings.degraded_cache_ttl_seconds)
            return result

        result = ApiResult(MODE_LIVE, payload=payload, http_status=response.status_code, reason_code=REASON_OK)
        ttl = cache_ttl if cache_ttl is not None else self.settings.cache_ttl_seconds
        self.cache.set(key, result, ttl)
        return result

    # --- 具体端点 -------------------------------------------------------- #
    async def track(self, tracking_id: str, *, cache_ttl: Optional[int] = None) -> ApiResult:
        return await self._request(
            "GET", "track", params={"tracking_ids": str(tracking_id).strip()}, cache_ttl=cache_ttl
        )

    async def price_shipments(self, body: Dict[str, Any]) -> ApiResult:
        return await self._request("POST", "prices/shipments", body=body)

    async def lookup_address(self, suburb: str, state: str, postcode: str) -> ApiResult:
        return await self._request(
            "GET", "address", params={"suburb": suburb, "state": state, "postcode": postcode}
        )
