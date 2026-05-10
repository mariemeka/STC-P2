"""Scheduler de chunks audio (architecture distribuee, single-pass).

Chaque sous-titreur recoit un chunk unique a transcrire. Quand il a fini,
il recoit le suivant (rotation si plus de chunks que de users connectes).
Les bornes de chaque slot N sont [N*step, N*step + duration] avec
step = duration - overlap. Slot 0 = [0, 30], slot 1 = [25, 55], etc.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Slot:
    """Un slot = une portion d'audio assignee a un sous-titreur.

    audio_start / audio_end : offsets en secondes DANS le fichier audio.
    Deux slots consecutifs se chevauchent de `overlap`s pour permettre
    la fusion d'overlap aux frontieres.
    """
    slot_index:  int
    audio_start: float
    audio_end:   float
    assigned_to: Optional[str] = None
    completed:   bool = False
    # Aliases pour retro-compat avec /admin/data et /export
    @property
    def start_time(self) -> float: return self.audio_start
    @property
    def end_time(self) -> float: return self.audio_end


class Scheduler:
    def __init__(self, slot_duration: int = 30, overlap: int = 5):
        self.slot_duration = slot_duration
        self.overlap = overlap
        self.slots: list[Slot] = []
        self._user_order: list[str] = []

    # ── Lookup ─────────────────────────────────────────────────────────────

    def slot_of(self, user_id: str) -> Optional[Slot]:
        """Slot actuellement assigne (non-complete) pour ce user, ou None."""
        for slot in self.slots:
            if slot.assigned_to == user_id and not slot.completed:
                return slot
        return None

    def connected_users(self) -> list[str]:
        return list(self._user_order)

    def get_active_user(self) -> Optional[str]:
        """Premier user ayant un slot non-complete (utilise par /admin/data)."""
        for slot in self.slots:
            if slot.assigned_to and not slot.completed:
                return slot.assigned_to
        return None

    # ── Mutations ──────────────────────────────────────────────────────────

    def assign(self, user_id: str) -> Slot:
        """Donne un slot au user. Reuse son slot actif s'il en a un, sinon
        prend un slot libere par une deconnexion, sinon en cree un nouveau."""
        if user_id not in self._user_order:
            self._user_order.append(user_id)

        existing = self.slot_of(user_id)
        if existing:
            return existing

        # Recyclage : un user precedent s'est deconnecte sans terminer
        for slot in self.slots:
            if slot.assigned_to is None and not slot.completed:
                slot.assigned_to = user_id
                return slot

        # Sinon : nouveau slot a la suite
        next_index = len(self.slots)
        step = self.slot_duration - self.overlap
        audio_start = next_index * step
        audio_end = audio_start + self.slot_duration
        slot = Slot(
            slot_index=next_index,
            audio_start=audio_start,
            audio_end=audio_end,
            assigned_to=user_id,
        )
        self.slots.append(slot)
        return slot

    def mark_completed(self, user_id: str, slot_index: int) -> Slot:
        """Marque le slot de user_id comme termine, puis assigne le suivant."""
        if 0 <= slot_index < len(self.slots):
            slot = self.slots[slot_index]
            if slot.assigned_to == user_id:
                slot.completed = True
        return self.assign(user_id)

    def release_user(self, user_id: str):
        """Le user se deconnecte : libere son slot pour reassignation."""
        if user_id in self._user_order:
            self._user_order.remove(user_id)
        for slot in self.slots:
            if slot.assigned_to == user_id and not slot.completed:
                slot.assigned_to = None

    def reset(self):
        """Vide tout l'etat (slots + ordre des users). Garde la config."""
        self.slots.clear()
        self._user_order.clear()

    # ── Retro-compat (alias des anciens noms utilises ailleurs) ────────────

    def release_slot(self, user_id: str):
        self.release_user(user_id)
