from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

Market = Literal["CN", "HK", "US"]


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    market: Market
    exchange: Optional[str] = None
    currency: Optional[str] = None
    category: Optional[str] = None
