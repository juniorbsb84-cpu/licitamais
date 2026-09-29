"""Espera educada entre chamadas de rede: min_delay + jitter."""

from __future__ import annotations

import random
import time


def sleep_rate_limited(min_delay_ms: int, jitter_ms: int) -> None:
    atraso_ms = float(min_delay_ms)
    if jitter_ms > 0:
        atraso_ms += random.uniform(0.0, float(jitter_ms))
    if atraso_ms <= 0.0:
        return
    time.sleep(atraso_ms / 1000.0)
