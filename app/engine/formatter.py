"""Export des slots fusionnés vers SRT / TXT."""
from typing import List


def _srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(slots: List[dict], slot_duration: int) -> str:
    """slots: liste de {slot_index, text, timestamp}. timestamps absolus en epoch.

    Le SRT utilise le temps RELATIF au premier slot.
    """
    if not slots:
        return ""
    base = min(s["timestamp"] for s in slots)
    lines = []
    for i, s in enumerate(slots, start=1):
        start = s["timestamp"] - base
        end = start + slot_duration
        lines.append(str(i))
        lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        lines.append(s["text"])
        lines.append("")
    return "\n".join(lines)


def to_txt(slots: List[dict]) -> str:
    return "\n".join(s["text"] for s in slots if s.get("text"))
