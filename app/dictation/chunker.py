"""Decide where to cut live audio into chunks, so dictation is transcribed while you talk.

Chunks end at a natural pause (a breath between sentences), not at a fixed time, so
words aren't split. Silence is judged relative to the recording's own noise floor.
"""

from __future__ import annotations

import numpy as np

FRAME_S = 0.03  # 30 ms analysis frames
MIN_S = 8.0  # never cut before this much audio
TARGET_S = 12.0  # after this, take the best pause available
MAX_S = 25.0  # hard limit: cut at the quietest moment even without a clear pause
PAUSE_S = 0.3  # a pause must be at least this long
LOOKAHEAD_S = 0.3  # don't cut in the last moment; the pause may just be starting


def _frame_rms(samples: np.ndarray, rate: int) -> np.ndarray:
    n = max(1, int(FRAME_S * rate))
    usable = len(samples) // n * n
    if usable == 0:
        return np.zeros(0)
    frames = samples[:usable].astype(np.float32).reshape(-1, n)
    return np.sqrt((frames ** 2).mean(axis=1))


def find_cut(samples: np.ndarray, rate: int) -> int | None:
    """Index (into `samples`) to cut at, or None to keep listening."""
    total_s = len(samples) / rate
    if total_s < MIN_S:
        return None
    rms = _frame_rms(samples, rate)
    if len(rms) == 0:
        return None
    frame = int(FRAME_S * rate)
    # Quiet = well above the background noise but well below typical speech. Pauses can
    # be under 10% of fast speech, so the floor uses a low percentile, and the median
    # (speech level) caps the threshold so it can never land inside words.
    floor = float(np.percentile(rms, 5))
    speech = float(np.median(rms))
    threshold = max(150.0, min(floor * 3.0, speech * 0.3))  # int16 units; 150 ~ -47 dBFS
    first = int(MIN_S / FRAME_S)
    last = len(rms) - int(LOOKAHEAD_S / FRAME_S)
    need = max(1, int(PAUSE_S / FRAME_S))

    if total_s >= TARGET_S:
        best_start, best_len, run_start = -1, 0, None
        for i in range(first, max(first, last)):
            if rms[i] < threshold:
                if run_start is None:
                    run_start = i
                length = i - run_start + 1
                if length >= need and length > best_len:
                    best_start, best_len = run_start, length
            else:
                run_start = None
        if best_len:
            return (best_start + best_len // 2) * frame

    if total_s >= MAX_S:
        window = rms[max(first, len(rms) - int(5 / FRAME_S)):last] if last > first else rms[first:]
        offset = max(first, len(rms) - int(5 / FRAME_S))
        return (offset + int(np.argmin(window))) * frame
    return None
