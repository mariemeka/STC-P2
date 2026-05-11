import time
from typing import Dict, Optional
from pydantic import BaseModel


class SessionConfig(BaseModel):
    slot_duration: int = 30
    overlap_duration: int = 5
    num_pools: int = 1
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


ADMIN_ID = "admin_master"


class Scheduler:
    def __init__(self):
        self.config = SessionConfig()
        self.users: Dict[str, User] = {}

    # ── Config ────────────────────────────────────────────────────────────
    def set_config(self, slot_dur: int, overlap: int, pools: int):
        self.config.slot_duration = max(1, slot_dur)
        self.config.overlap_duration = max(0, min(overlap, self.config.slot_duration - 1))
        self.config.num_pools = max(1, pools)

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
        """Retire un user et compacte order_in_pool dans son pool."""
        user = self.users.pop(user_id, None)
        if not user or user.user_id == ADMIN_ID:
            return
        pool_users = sorted(
            [u for u in self._subtitlers() if u.pool_id == user.pool_id],
            key=lambda u: u.order_in_pool,
        )
        for i, u in enumerate(pool_users):
            u.order_in_pool = i

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
            }

        now = self.config.paused_at if self.config.is_paused else time.time()
        elapsed = max(0.0, now - self.config.start_time - self.config.total_paused_time)

        slot_dur = self.config.slot_duration
        overlap = self.config.overlap_duration
        cycle_time = max(1, slot_dur - overlap)

        global_slot_index = int(elapsed // cycle_time)
        e_in_cycle = elapsed % cycle_time
        time_left = round(max(0.0, slot_dur - e_in_cycle), 1)

        is_my_turn = False
        my_slot_index = global_slot_index
        my_time_left = time_left

        if user_id and user_id in self.users and user_id != ADMIN_ID:
            user = self.users[user_id]
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
                    my_time_left = time_left

        return {
            "active": self.config.is_active,
            "paused": self.config.is_paused,
            "slot_index": global_slot_index,
            "time_left": my_time_left if user_id else time_left,
            "is_my_turn": is_my_turn,
            "my_slot_index": my_slot_index,
            "config": self.config.model_dump(),
        }
