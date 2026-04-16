"""Async token bucket + per-day budget counter for OpenRouter."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class Budget:
    rpm: int
    rpd: int


class RateLimiter:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self._lock = asyncio.Lock()
        self._minute_window: list[float] = []
        self._day_window: list[float] = []

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._minute_window = [t for t in self._minute_window if now - t < 60]
                self._day_window = [t for t in self._day_window if now - t < 86400]
                if (len(self._minute_window) < self.budget.rpm
                        and len(self._day_window) < self.budget.rpd):
                    self._minute_window.append(now)
                    self._day_window.append(now)
                    return
                wait_minute = 60 - (now - self._minute_window[0]) if len(self._minute_window) >= self.budget.rpm else 0
                wait_day = 86400 - (now - self._day_window[0]) if len(self._day_window) >= self.budget.rpd else 0
                sleep_for = max(0.2, min(wait_minute or 60, wait_day or 60))
            await asyncio.sleep(sleep_for)

    def stats(self) -> dict[str, int]:
        now = time.monotonic()
        return {
            "used_minute": sum(1 for t in self._minute_window if now - t < 60),
            "used_day": sum(1 for t in self._day_window if now - t < 86400),
            "rpm": self.budget.rpm,
            "rpd": self.budget.rpd,
        }
