"""依赖注入：settings / JsonStore / TTL cache 都是进程级单例。"""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.data_loader import JsonStore
from app.services.cache import TTLCache
from app.services.orders import OrderRepository

_API_CACHE = TTLCache()


@lru_cache(maxsize=1)
def get_store(settings: Settings) -> JsonStore:
    return JsonStore(settings.data_dir)


def get_cache() -> TTLCache:
    return _API_CACHE


def build_repository(settings: Settings | None = None) -> OrderRepository:
    settings = settings or get_settings()
    return OrderRepository(settings, get_store(settings), get_cache())
