# PLACEHOLDER - sera remplacé par Dev B (feature/scheduler)
# Interface à respecter : assign_slot(user_id) et release_slot(user_id)

from dataclasses import dataclass
from typing import Optional

@dataclass
class Slot:
    slot_index:  int
    start_time:  float
    end_time:    float
    assigned_to: Optional[str] = None

class Scheduler:
    def __init__(self, slot_duration: int = 30, overlap: int = 5):
        self.slot_duration = slot_duration
        self.overlap = overlap
        self.slots = []
        self.current_time = 0.0

    def assign_slot(self, user_id: str) -> Slot:
        slot_index = len(self.slots)
        start = self.current_time
        end = start + self.slot_duration
        slot = Slot(slot_index=slot_index, start_time=start,
                    end_time=end, assigned_to=user_id)
        self.slots.append(slot)
        self.current_time = end - self.overlap
        return slot

    def release_slot(self, user_id: str):
        for slot in self.slots:
            if slot.assigned_to == user_id:
                slot.assigned_to = None
