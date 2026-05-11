import csv
import os
from typing import List
from .models import Caption

DATA_DIR = "data"


def _file_path(session_id: str, pool_id: int) -> str:
    return os.path.join(DATA_DIR, f"session_{session_id}_pool_{pool_id}.csv")


def save_caption_to_csv(session_id: str, caption: Caption):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    file_path = _file_path(session_id, caption.pool_id)
    file_exists = os.path.isfile(file_path)

    with open(file_path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "user_id", "slot_index", "text"])
        writer.writerow([
            caption.timestamp,
            caption.user_id,
            caption.slot_index,
            caption.text,
        ])


def read_session_csvs(session_id: str, pool_id: int) -> List[dict]:
    file_path = _file_path(session_id, pool_id)
    if not os.path.isfile(file_path):
        return []
    rows = []
    with open(file_path, mode="r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    "timestamp": float(r["timestamp"]),
                    "user_id": r["user_id"],
                    "slot_index": int(r["slot_index"]),
                    "text": r["text"],
                })
            except (KeyError, ValueError):
                continue
    return rows
