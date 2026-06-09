import time
from typing import Dict, Optional
from pydantic import BaseModel


class SessionConfig(BaseModel):
    slot_duration: int = 20
    overlap_duration: int = 5
    num_pools: int = 1
    countdown: int = 3
    start_time: Optional[float] = None
    paused_at: Optional[float] = None
    total_paused_time: float = 0.0
    is_active: bool = False
    is_paused: bool = False


class User(BaseModel):
    user_id: str
    username: str
    pool_id: int
    order_in_pool: int
    typing_speed: float = 0.0       # mots/seconde mesuré en temps réel
    slot_duration_personal: int = 0  # 0 = utiliser la valeur globale


ADMIN_ID = "admin_master"


class Scheduler:
    def __init__(self):
        self.config = SessionConfig()
        self.users: Dict[str, User] = {}

    # ── Config ────────────────────────────────────────────────────────────
    def set_config(self, slot_dur: int, overlap: int, pools: int, countdown: int = 3):
        self.config.slot_duration = max(1, slot_dur)
        self.config.overlap_duration = max(0, min(overlap, self.config.slot_duration - 1))
        self.config.num_pools = max(1, pools)
        self.config.countdown = max(0, countdown)

    # ── Users ─────────────────────────────────────────────────────────────
    def _subtitlers(self):
        return [u for u in self.users.values() if u.user_id != ADMIN_ID]

    def _smallest_pool(self) -> int:
        counts = {p: 0 for p in range(1, self.config.num_pools + 1)}
        for u in self._subtitlers():
            counts[u.pool_id] = counts.get(u.pool_id, 0) + 1
        return min(counts, key=counts.get)

    def add_user(self, user_id: str, username: str) -> User:
        if user_id == ADMIN_ID:
            user = User(user_id=user_id, username=username, pool_id=0, order_in_pool=0)
            self.users[user_id] = user
            return user

        pool_id = self._smallest_pool()
        order = len([u for u in self._subtitlers() if u.pool_id == pool_id])
        user = User(user_id=user_id, username=username, pool_id=pool_id, order_in_pool=order)
        self.users[user_id] = user
        return user

    def remove_user(self, user_id: str):
        user = self.users.pop(user_id, None)
        if not user or user.user_id == ADMIN_ID:
            return
        pool_users = sorted(
            [u for u in self._subtitlers() if u.pool_id == user.pool_id],
            key=lambda u: u.order_in_pool,
        )
        for i, u in enumerate(pool_users):
            u.order_in_pool = i

    def assign_user(self, user_id: str, new_pool: int, new_order: int):
        """Réaffecte un user à un pool/ordre donné, en réindexant les autres."""
        user = self.users.get(user_id)
        if not user or user.user_id == ADMIN_ID:
            return
        old_pool = user.pool_id
        new_pool = max(1, min(new_pool, self.config.num_pools))

        if old_pool != new_pool:
            old_users = sorted(
                [u for u in self._subtitlers() if u.pool_id == old_pool and u.user_id != user_id],
                key=lambda u: u.order_in_pool,
            )
            for i, u in enumerate(old_users):
                u.order_in_pool = i

        new_pool_users = sorted(
            [u for u in self._subtitlers() if u.pool_id == new_pool and u.user_id != user_id],
            key=lambda u: u.order_in_pool,
        )
        new_order = max(0, min(new_order, len(new_pool_users)))
        user.pool_id = new_pool
        new_pool_users.insert(new_order, user)
        for i, u in enumerate(new_pool_users):
            u.order_in_pool = i

    # ── Adaptation dynamique ───────────────────────────────────────────────
    def update_typing_speed(self, user_id: str, words_per_second: float):
        """
        Met à jour la vitesse de frappe et adapte le slot personnel.
        - Rapide (>= 1.5 mots/s) : slot allongé de 5s
        - Lent   (<= 0.8 mots/s) : slot raccourci de 5s
        - Normal                 : slot standard
        """
        user = self.users.get(user_id)
        if not user or user.user_id == ADMIN_ID:
            return

        user.typing_speed = round(words_per_second, 2)
        base = self.config.slot_duration

        if words_per_second >= 1.5:
            user.slot_duration_personal = min(base + 5, 40)
        elif words_per_second <= 0.8:
            user.slot_duration_personal = max(base - 5, 10)
        else:
            user.slot_duration_personal = base

    # ── Pause ─────────────────────────────────────────────────────────────
    def toggle_pause(self):
        if not self.config.is_active:
            return
        now = time.time()
        if not self.config.is_paused:
            self.config.is_paused = True
            self.config.paused_at = now
        else:
            if self.config.paused_at is not None:
                self.config.total_paused_time += now - self.config.paused_at
            self.config.is_paused = False
            self.config.paused_at = None

    # ── État courant ──────────────────────────────────────────────────────
    def get_current_state(self, user_id: Optional[str] = None) -> dict:
        if not self.config.is_active or not self.config.start_time:
            return {
                "active": False,
                "paused": False,
                "time_left": 0,
                "is_my_turn": False,
                "slot_index": 0,
                "my_slot_index": 0,
                "countdown": 0,
            }

        now = self.config.paused_at if self.config.is_paused else time.time()
        elapsed_raw = now - self.config.start_time - self.config.total_paused_time

        if elapsed_raw < 0:
            return {
                "active": True,
                "paused": False,
                "time_left": 0,
                "is_my_turn": False,
                "slot_index": 0,
                "my_slot_index": 0,
                "countdown": max(1, int(-elapsed_raw) + 1),
                "config": self.config.model_dump(),
            }

        elapsed = elapsed_raw
        slot_dur = self.config.slot_duration
        overlap = self.config.overlap_duration
        cycle_time = max(1, slot_dur - overlap)

        global_slot_index = int(elapsed // cycle_time)
        e_in_cycle = elapsed % cycle_time
        time_left = round(max(0.0, slot_dur - e_in_cycle), 1)

        is_my_turn = False
        my_slot_index = global_slot_index
        my_time_left = time_left
        personal_slot = slot_dur
        typing_speed = 0.0

        if user_id and user_id in self.users and user_id != ADMIN_ID:
            user = self.users[user_id]
            typing_speed = user.typing_speed

            # Slot adapté à la vitesse du sous-titreur
            personal_slot = user.slot_duration_personal or slot_dur

            pool_users = [u for u in self._subtitlers() if u.pool_id == user.pool_id]
            n = len(pool_users)
            if n > 0:
                main = (global_slot_index % n) == user.order_in_pool
                tail = (
                    global_slot_index > 0
                    and ((global_slot_index - 1) % n) == user.order_in_pool
                    and e_in_cycle < overlap
                )
                is_my_turn = main or tail
                if tail and not main:
                    my_slot_index = global_slot_index - 1
                    my_time_left = round(max(0.0, overlap - e_in_cycle), 1)
                elif main:
                    my_slot_index = global_slot_index
                    my_time_left = round(max(0.0, personal_slot - e_in_cycle), 1)

        return {
            "active": self.config.is_active,
            "paused": self.config.is_paused,
            "slot_index": global_slot_index,
            "time_left": my_time_left if user_id else time_left,
            "is_my_turn": is_my_turn,
            "my_slot_index": my_slot_index,
            "countdown": 0,
            "elapsed": round(elapsed, 1),
            "typing_speed": typing_speed,
            "personal_slot": personal_slot,
            "config": self.config.model_dump(),
        }
