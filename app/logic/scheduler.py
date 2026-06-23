import time
from typing import Dict, Optional
from pydantic import BaseModel


class SessionConfig(BaseModel):
    slot_duration: int = 8      # "temps d'écoute" : durée d'un segment (sert aussi au découpage SRT)
    writing_time: int = 20      # "temps d'écriture" : durée totale pour taper son segment
    pre_alert: int = 3          # préavis (s) avant le début du tour
    start_time: Optional[float] = None
    paused_at: Optional[float] = None
    total_paused_time: float = 0.0
    is_active: bool = False
    is_paused: bool = False


class User(BaseModel):
    user_id: str
    username: str
    order: int          # position dans le relais (0, 1, 2, ...)


ADMIN_ID = "admin_master"


class Scheduler:
    def __init__(self):
        self.config = SessionConfig()
        self.users: Dict[str, User] = {}

    # ── Config ────────────────────────────────────────────────────────────
    def set_config(self, slot_dur: int, writing_time: int, pre_alert: int = 3):
        self.config.slot_duration = max(1, slot_dur)
        # le temps d'écriture ne peut jamais être plus court que le temps d'écoute
        self.config.writing_time = max(self.config.slot_duration, writing_time)
        self.config.pre_alert = max(0, pre_alert)

    # ── Users ─────────────────────────────────────────────────────────────
    def _subtitlers(self):
        return [u for u in self.users.values() if u.user_id != ADMIN_ID]

    def add_user(self, user_id: str, username: str) -> User:
        if user_id == ADMIN_ID:
            user = User(user_id=user_id, username=username, order=0)
            self.users[user_id] = user
            return user
        order = len(self._subtitlers())
        user = User(user_id=user_id, username=username, order=order)
        self.users[user_id] = user
        return user

    def remove_user(self, user_id: str):
        user = self.users.pop(user_id, None)
        if not user or user.user_id == ADMIN_ID:
            return
        for i, u in enumerate(sorted(self._subtitlers(), key=lambda u: u.order)):
            u.order = i

    def assign_user(self, user_id: str, new_order: int):
        """Change la position d'un sous-titreur dans le relais."""
        user = self.users.get(user_id)
        if not user or user.user_id == ADMIN_ID:
            return
        others = sorted([u for u in self._subtitlers() if u.user_id != user_id],
                        key=lambda u: u.order)
        new_order = max(0, min(new_order, len(others)))
        others.insert(new_order, user)
        for i, u in enumerate(others):
            u.order = i

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
        cfg = self.config
        if not cfg.is_active or not cfg.start_time:
            return {
                "active": False, "paused": False, "time_left": 0,
                "is_my_turn": False, "slot_index": 0, "my_slot_index": 0,
                "next_turn_in": None,
            }

        now = cfg.paused_at if cfg.is_paused else time.time()
        elapsed = now - cfg.start_time - cfg.total_paused_time
        slot_dur = cfg.slot_duration

        # Phase de préparation AVANT le tout début : chaque sous-titreur (y compris
        # le 1er) a un compte à rebours "À vous dans..." avant son premier tour.
        if elapsed < 0:
            next_turn_in = None
            if user_id and user_id in self.users and user_id != ADMIN_ID:
                n = len(self._subtitlers())
                if n > 0:
                    order = self.users[user_id].order
                    next_turn_in = round(order * slot_dur - elapsed, 1)
            return {
                "active": True, "paused": cfg.is_paused, "starting": True,
                "time_left": 0, "is_my_turn": False,
                "slot_index": 0, "my_slot_index": 0,
                "next_turn_in": next_turn_in, "elapsed": round(elapsed, 1),
                "config": cfg.model_dump(),
            }

        global_slot_index = int(elapsed // slot_dur)
        e_in_slot = elapsed % slot_dur
        time_left = round(max(0.0, slot_dur - e_in_slot), 1)

        is_my_turn = False
        my_slot_index = global_slot_index
        my_time_left = time_left
        time_in_turn = 0.0
        next_turn_in = None

        if user_id and user_id in self.users and user_id != ADMIN_ID:
            user = self.users[user_id]
            n = len(self._subtitlers())
            if n > 0:
                order = user.order
                # Borne : la fenêtre d'écriture est limitée à (n-1)*slot pour
                # garantir TOUJOURS un vrai cycle (au moins un slot de repos avant
                # le prochain tour du même sous-titreur). Avec 1 sous-titreur, il
                # tape en continu (personne à qui passer la main, c'est normal).
                wt = max(slot_dur, min(cfg.writing_time, (n - 1) * slot_dur))
                # Segment le plus récent appartenant à ce sous-titreur
                k_active = global_slot_index - ((global_slot_index - order) % n)
                if k_active >= 0:
                    seg_start = k_active * slot_dur
                    seg_end = seg_start + wt
                    if seg_start <= elapsed < seg_end:
                        is_my_turn = True
                        my_slot_index = k_active
                        my_time_left = round(max(0.0, seg_end - elapsed), 1)
                        time_in_turn = round(elapsed - seg_start, 1)
                if not is_my_turn:
                    # Préavis : quand commence son prochain segment ?
                    for k in range(global_slot_index + 1, global_slot_index + 2 * n + 1):
                        if (k % n) == order:
                            ss = k * slot_dur
                            if ss > elapsed:
                                next_turn_in = round(ss - elapsed, 1)
                                break

        return {
            "active": cfg.is_active,
            "paused": cfg.is_paused,
            "slot_index": global_slot_index,
            "time_left": my_time_left if user_id else time_left,
            "is_my_turn": is_my_turn,
            "my_slot_index": my_slot_index,
            "time_in_turn": time_in_turn,
            "next_turn_in": next_turn_in,
            "elapsed": round(elapsed, 1),
            "config": cfg.model_dump(),
        }
