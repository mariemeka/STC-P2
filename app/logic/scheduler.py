import math
import time
from typing import Dict, Optional
from pydantic import BaseModel, computed_field


class SessionConfig(BaseModel):
    slot_duration: int = 8          # "temps d'écoute" : durée d'un segment (utilisée aussi pour le découpage SRT)
    writing_time: int = 24          # "temps d'écriture" : durée pendant laquelle un sous-titreur peut taper son segment
    pre_alert: int = 5              # préavis (s) avant le début du tour, pour alerter le sous-titreur en amont
    num_pools: int = 1
    countdown: int = 3
    start_time: Optional[float] = None
    paused_at: Optional[float] = None
    total_paused_time: float = 0.0
    is_active: bool = False
    is_paused: bool = False

    @computed_field
    @property
    def overlap_duration(self) -> int:
        """Conservé pour compat / affichage : durée de chevauchement entre deux sous-titreurs successifs."""
        return max(0, self.writing_time - self.slot_duration)


class User(BaseModel):
    user_id: str
    username: str
    pool_id: int
    order_in_pool: int
    typing_speed: float = 0.0           # mots/seconde mesuré en temps réel
    writing_time_personal: int = 0      # 0 = utiliser la valeur globale (writing_time)


ADMIN_ID = "admin_master"


class Scheduler:
    def __init__(self):
        self.config = SessionConfig()
        self.users: Dict[str, User] = {}

    # ── Config ────────────────────────────────────────────────────────────
    def set_config(self, slot_dur: int, writing_time: int, pools: int, countdown: int = 3, pre_alert: int = 5):
        self.config.slot_duration = max(1, slot_dur)
        # le temps d'écriture ne peut jamais être plus court que le temps d'écoute
        self.config.writing_time = max(self.config.slot_duration, writing_time)
        self.config.num_pools = max(1, pools)
        self.config.countdown = max(0, countdown)
        self.config.pre_alert = max(0, pre_alert)

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
        Met à jour la vitesse de frappe et adapte le TEMPS D'ÉCRITURE personnel.
        - Rapide (>= 1.5 mots/s) : temps d'écriture allongé de 5s
        - Lent   (<= 0.8 mots/s) : temps d'écriture raccourci de 5s
        - Normal                 : temps d'écriture standard
        """
        user = self.users.get(user_id)
        if not user or user.user_id == ADMIN_ID:
            return

        user.typing_speed = round(words_per_second, 2)
        base = self.config.writing_time

        if words_per_second >= 1.5:
            user.writing_time_personal = min(base + 5, base * 2)
        elif words_per_second <= 0.8:
            user.writing_time_personal = max(base - 5, max(5, base // 2))
        else:
            user.writing_time_personal = base

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
        writing_time = self.config.writing_time

        # "global_slot_index" = quel segment d'écoute (8s par défaut) est en cours
        # globalement (utile pour le viewer / l'affichage général).
        global_slot_index = int(elapsed // slot_dur)
        e_in_slot = elapsed % slot_dur
        time_left = round(max(0.0, slot_dur - e_in_slot), 1)

        is_my_turn = False
        my_slot_index = global_slot_index
        my_time_left = time_left
        time_in_turn = 0.0
        personal_writing_time = writing_time
        typing_speed = 0.0
        next_turn_in = None

        if user_id and user_id in self.users and user_id != ADMIN_ID:
            user = self.users[user_id]
            typing_speed = user.typing_speed
            personal_writing_time = user.writing_time_personal or writing_time

            pool_users = [u for u in self._subtitlers() if u.pool_id == user.pool_id]
            n = len(pool_users)
            if n > 0:
                # Le temps d'écriture peut s'étaler sur plusieurs segments d'écoute :
                # on regarde en arrière sur autant de segments que nécessaire pour
                # retrouver le segment dont ce sous-titreur est responsable et dont
                # la fenêtre d'écriture couvre l'instant présent.
                max_back = int(math.ceil(personal_writing_time / slot_dur)) + 1
                for k in range(global_slot_index, max(-1, global_slot_index - max_back), -1):
                    if k < 0 or (k % n) != user.order_in_pool:
                        continue
                    seg_start = k * slot_dur
                    seg_end = seg_start + personal_writing_time
                    if seg_start <= elapsed < seg_end:
                        is_my_turn = True
                        my_slot_index = k
                        my_time_left = round(max(0.0, seg_end - elapsed), 1)
                        time_in_turn = round(elapsed - seg_start, 1)
                        break

                if not is_my_turn:
                    # Préavis : dans combien de temps son prochain segment commence-t-il ?
                    for k in range(global_slot_index, global_slot_index + 2 * n + 1):
                        if k < 0 or (k % n) != user.order_in_pool:
                            continue
                        seg_start = k * slot_dur
                        if seg_start > elapsed:
                            next_turn_in = round(seg_start - elapsed, 1)
                            break

        return {
            "active": self.config.is_active,
            "paused": self.config.is_paused,
            "slot_index": global_slot_index,
            "time_left": my_time_left if user_id else time_left,
            "is_my_turn": is_my_turn,
            "my_slot_index": my_slot_index,
            "time_in_turn": time_in_turn,
            "countdown": 0,
            "elapsed": round(elapsed, 1),
            "typing_speed": typing_speed,
            "personal_writing_time": personal_writing_time,
            "next_turn_in": next_turn_in,
            "config": self.config.model_dump(),
        }
