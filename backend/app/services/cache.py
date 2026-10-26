"""极简 TTL 缓存。

用途：同一个 partner API 结果在短时间内被反复请求时（前端多次渲染、列表页 +
详情页），避免把请求打爆。正常结果缓存较久，降级结果只缓存很短时间，
这样 partner API 恢复后能自动回到真实数据，而不需要重启服务。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    def __init__(self) -> None:
        self._data: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._data[key] = (time.monotonic() + max(int(ttl_seconds), 0), value)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
