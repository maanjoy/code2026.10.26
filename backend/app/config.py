"""应用配置。

所有外部凭证只从环境变量 / `.env` 文件读取，且**只能在后端进程内出现**，
绝不通过 API 响应或前端代码泄漏（对外只暴露"是否已配置"的布尔值）。

优先级：进程环境变量 > 仓库根目录 `.env` > `backend/.env` > 代码默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

_TRUTHY = {"1", "true", "yes", "on"}


def _parse_env_file(path: Path) -> Dict[str, str]:
    """极简 .env 解析器（不引入第三方依赖，支持 export / 引号 / 注释）。"""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        key = key.strip()
        if key:
            values[key] = value
    return values


_DOTENV_FILES = (PROJECT_ROOT / ".env", BACKEND_DIR / ".env")

# 值容器保持模块级可变 dict：测试会用 monkeypatch 把它换成 {} 来模拟"没有 .env"
_DOTENV_VALUES: Dict[str, str] = {}
_DOTENV_SIGNATURE: list = [None]


def _dotenv_signature() -> tuple:
    """(路径, mtime_ns, size) 作为指纹——文件被改写时指纹必定变化。"""
    stamps = []
    for candidate in _DOTENV_FILES:
        try:
            stat = candidate.stat()
            stamps.append((str(candidate), stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append((str(candidate), None, None))
    return tuple(stamps)


def _refresh_dotenv() -> None:
    """`.env` 改动后自动重载。

    这样改完配置**不必重启服务**：把 `.env` 改名 → 下一次请求立刻走"缺凭证"降级路径，
    改回来 → 立刻恢复。降级与恢复都能现场演示，而不是只能嘴上说。
    """
    signature = _dotenv_signature()
    if _DOTENV_SIGNATURE[0] == signature:
        return
    _DOTENV_SIGNATURE[0] = signature
    _DOTENV_VALUES.clear()
    for candidate in _DOTENV_FILES:
        _DOTENV_VALUES.update(_parse_env_file(candidate))


_refresh_dotenv()


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    _refresh_dotenv()
    value = os.environ.get(name)
    if value is None:
        value = _DOTENV_VALUES.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in _TRUTHY


def env_decimal(name: str, default: str) -> Decimal:
    raw = env(name, default)
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(env(name, str(default))))
    except ValueError:
        return default


CREDENTIAL_ENV_NAMES = (
    "AUSPOST_API_KEY",
    "AUSPOST_API_PASSWORD",
    "AUSPOST_ACCOUNT_NUMBER",
)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path

    # AusPost / StarTrack testbed
    auspost_base_url: str
    auspost_api_key: str
    auspost_api_password: str
    auspost_account_number: str

    # 起运地（origin）。留空时回退到 data/orders.json 里的 origin 节点。
    origin_postcode: str
    origin_suburb: Optional[str]
    origin_state: str

    http_timeout_seconds: float

    # 计算规则（税率来自配置，不写死在计算函数里）
    gst_rate: Decimal

    # 输入数据结构 <> 业务字段名映射（字段名而非值，换一套同结构订单也能跑）
    # 生产只加载真实订单数据集；边界用例数据集单独存放，默认不进生产链路
    order_dataset_pattern: str
    test_dataset_pattern: str
    # 边界用例是否对外暴露（生产交付默认关闭，只有显式打开才有 /api/testcases 与测试页）
    expose_testcases: bool
    sku_catalog_file: str
    order_no_field: str
    items_field: str
    item_sku_field: str
    item_qty_field: str
    item_tracking_field: str

    cache_ttl_seconds: int
    degraded_cache_ttl_seconds: int

    def with_test_dataset(self) -> "Settings":
        """把数据范围切到边界用例数据集（只用于测试链路，不改动生产配置）。"""
        return replace(self, order_dataset_pattern=self.test_dataset_pattern)

    def missing_credentials(self) -> list:
        configured = {
            "AUSPOST_API_KEY": self.auspost_api_key,
            "AUSPOST_API_PASSWORD": self.auspost_api_password,
            "AUSPOST_ACCOUNT_NUMBER": self.auspost_account_number,
        }
        return [name for name, value in configured.items() if not value]

    @property
    def credentials_ready(self) -> bool:
        return not self.missing_credentials()


@lru_cache(maxsize=8)
def _build_settings(dotenv_signature: tuple) -> Settings:
    """按 `.env` 指纹缓存——文件没变就复用，变了就自动重建。"""
    data_dir = Path(env("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()
    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        auspost_base_url=env(
            "AUSPOST_BASE_URL", "https://digitalapi.auspost.com.au/test/shipping/v1"
        ),
        auspost_api_key=env("AUSPOST_API_KEY", ""),
        auspost_api_password=env("AUSPOST_API_PASSWORD", ""),
        auspost_account_number=env("AUSPOST_ACCOUNT_NUMBER", ""),
        origin_postcode=env("ORIGIN_POSTCODE", ""),
        origin_suburb=env("ORIGIN_SUBURB", ""),
        origin_state=env("ORIGIN_STATE", ""),
        http_timeout_seconds=env_float("HTTP_TIMEOUT_SECONDS", 8.0),
        gst_rate=env_decimal("GST_RATE", "0.10"),
        # 生产：orders*.json（题目给的真实订单 + 自己生成的测试订单）
        # 故意造坏的边界数据集改名成 testcases_edge.json，不再被这个通配符命中
        order_dataset_pattern=env("ORDER_DATASET_PATTERN", "orders*.json"),
        test_dataset_pattern=env("TEST_DATASET_PATTERN", "testcases_edge.json"),
        expose_testcases=env_bool("EXPOSE_TESTCASES", False),
        sku_catalog_file=env("SKU_CATALOG_FILE", "sku_catalog.json"),
        order_no_field=env("ORDER_NO_FIELD", "order_no"),
        items_field=env("ITEMS_FIELD", "items"),
        item_sku_field=env("ITEM_SKU_FIELD", "sku"),
        item_qty_field=env("ITEM_QTY_FIELD", "qty"),
        item_tracking_field=env("ITEM_TRACKING_FIELD", "tracking"),
        cache_ttl_seconds=env_int("AUSPOST_CACHE_TTL_SECONDS", 900),
        degraded_cache_ttl_seconds=env_int("AUSPOST_DEGRADED_CACHE_TTL_SECONDS", 30),
    )


def get_settings() -> Settings:
    """取当前配置。`.env` 一变，自动重建（无需重启服务）。"""
    _refresh_dotenv()
    return _build_settings(_dotenv_signature())


def _clear_settings_cache() -> None:
    # 注意：只清 Settings，不动 dotenv 指纹。
    # 测试会 monkeypatch `_DOTENV_VALUES` 为空来模拟"没有 .env"，
    # 这里若重置指纹，下一次 env() 会把文件内容重新灌回去，测试就失效了。
    _build_settings.cache_clear()


# 保持 `get_settings.cache_clear()` 这个既有调用方式（测试里在用）
get_settings.cache_clear = _clear_settings_cache  # type: ignore[attr-defined]


def reload_settings() -> Settings:
    """测试 / 改完 .env 后强制重新加载。"""
    get_settings.cache_clear()
    return get_settings()
