import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Slot:
    slot_index:  int
    start_time:  float        # timestamp Unix réel
    end_time:    float        # timestamp Unix réel
    assigned_to: Optional[str] = None

    @property
    def is_active(self) -> bool:
        """Vrai si ce slot est en cours maintenant"""
        now = time.time()
        return self.assigned_to is not None and self.start_time <= now <= self.end_time


class Scheduler:
    """Gère la rotation des sous-titreurs avec des slots de temps réel"""

    def __init__(self, slot_duration: int = 30, overlap: int = 5):
        self.slot_duration = slot_duration
        self.overlap = overlap
        self.slots: list[Slot] = []
        self._user_order: list[str] = []
        self._next_slot_start: float = time.time()

    def assign_slot(self, user_id: str) -> Slot:
        """Assigne le prochain slot disponible à user_id"""
        if user_id not in self._user_order:
            self._user_order.append(user_id)

        # Jamais dans le passé
        start = max(self._next_slot_start, time.time())
        end = start + self.slot_duration

        slot = Slot(
            slot_index=len(self.slots),
            start_time=start,
            end_time=end,
            assigned_to=user_id,
        )
        self.slots.append(slot)
        self._next_slot_start = end - self.overlap
        return slot

    def release_slot(self, user_id: str):
        """Libère le slot de user_id et le retire de la rotation"""
        if user_id in self._user_order:
            self._user_order.remove(user_id)
        for slot in self.slots:
            if slot.assigned_to == user_id:
                slot.assigned_to = None

    def get_active_user(self) -> Optional[str]:
        """Retourne l'utilisateur dont c'est le tour en ce moment"""
        now = time.time()
        for slot in reversed(self.slots):
            if slot.assigned_to and slot.start_time <= now <= slot.end_time:
                return slot.assigned_to
        return None

    def next_in_rotation(self, after_user_id: str) -> Optional[str]:
        """Retourne le prochain utilisateur dans la rotation circulaire"""
        if not self._user_order:
            return None
        if after_user_id not in self._user_order:
            return self._user_order[0]
        idx = self._user_order.index(after_user_id)
        return self._user_order[(idx + 1) % len(self._user_order)]

    def connected_users(self) -> list[str]:
        """Retourne la liste des utilisateurs connectés"""
        return list(self._user_order)
