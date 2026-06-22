import time
import math
from typing import Dict, List, Optional
from pydantic import BaseModel


class SessionConfig(BaseModel):
    slot_duration: int = 8
    overlap_duration: int = 2
    num_pools: int = 1
    countdown: int = 3
    audio_duration: Optional[float] = None
    start_time: Optional[float] = None
    paused_at: Optional[float] = None
    total_paused_time: float = 0.0
    is_active: bool = False
    is_paused: bool = False
    # Rotation dynamique : le tour en cours peut être prolongé en temps réel
    # si le sous-titreur actif a besoin de plus de temps (cf. update_typing_speed).
    rotation_index: int = 0
    turn_start: float = 0.0
    turn_deadline: float = 0.0


class User(BaseModel):
    user_id: str
    username: str
    pool_id: int
    order_in_pool: int
    typing_speed: float = 0.0       # mots/seconde mesuré en temps réel
    slot_duration_personal: int = 0  # 0 = utiliser la valeur globale
    current_turn_index: int = -1     # my_slot_index du tour en cours (pour mesurer sa durée réelle)
    current_turn_start: float = 0.0  # elapsed au moment où ce tour a commencé
    typing_start: float = -1.0       # elapsed au 1er caractère tapé CE tour (-1 = pas encore tapé)


ADMIN_ID = "admin_master"


class Scheduler:
    def __init__(self):
        self.config = SessionConfig()
        self.users: Dict[str, User] = {}

    # ── Config ────────────────────────────────────────────────────────────
    def set_config(self, slot_dur: int, overlap: int, countdown: int = 3):
        self.config.slot_duration = max(1, slot_dur)
        self.config.overlap_duration = max(0, min(overlap, self.config.slot_duration - 1))
        self.config.countdown = max(0, countdown)
        self._auto_distribute()

    TARGET_TEAM_SIZE = 2

    def _max_useful_pools(self) -> Optional[int]:
        """Nombre de cycles de rotation que l'audio peut réellement supporter.

        Au-delà, une équipe ne jouerait jamais avant la fin de l'audio.
        """
        if not self.config.audio_duration:
            return None
        cycle_time = max(1, self.config.slot_duration - self.config.overlap_duration)
        return max(1, math.ceil(self.config.audio_duration / cycle_time))

    def _auto_distribute(self):
        n = len(self._subtitlers())
        if n <= 1:
            self.config.num_pools = 1
        else:
            # Au moins 2 équipes pour préserver la rotation (relais anti-fatigue),
            # la taille des équipes ne grandit vers TARGET_TEAM_SIZE qu'au-delà.
            target = max(2, n // self.TARGET_TEAM_SIZE)
            max_useful = self._max_useful_pools()
            if max_useful is not None:
                target = min(target, max_useful)
            self.config.num_pools = max(1, target)
        self._redistribute_users()

    def _redistribute_users(self):
        subtitlers = sorted(self._subtitlers(), key=lambda u: (u.pool_id, u.order_in_pool))
        n = self.config.num_pools
        counts = [0] * n
        for i, user in enumerate(subtitlers):
            pool_id = (i % n) + 1
            user.pool_id = pool_id
            user.order_in_pool = counts[pool_id - 1]
            counts[pool_id - 1] += 1

    # ── Users ─────────────────────────────────────────────────────────────
    def _subtitlers(self):
        return [u for u in self.users.values() if u.user_id != ADMIN_ID]

    def add_user(self, user_id: str, username: str) -> User:
        if user_id == ADMIN_ID:
            user = User(user_id=user_id, username=username, pool_id=0, order_in_pool=0)
            self.users[user_id] = user
            return user

        user = User(user_id=user_id, username=username, pool_id=1, order_in_pool=0)
        self.users[user_id] = user
        self._auto_distribute()
        return user

    def remove_user(self, user_id: str):
        user = self.users.pop(user_id, None)
        if not user or user.user_id == ADMIN_ID:
            return

        empty_pool = user.pool_id
        if any(u.pool_id == empty_pool for u in self._subtitlers()):
            return  # équipe pas vide, on ne touche à rien d'autre

        by_pool: Dict[int, List[User]] = {}
        for u in self._subtitlers():
            by_pool.setdefault(u.pool_id, []).append(u)

        if not by_pool:
            self.config.num_pools = 1
            return

        largest_pool_id = max(by_pool, key=lambda p: len(by_pool[p]))
        if len(by_pool[largest_pool_id]) > 1:
            # On déplace le membre le plus ancien (premier connecté, pas le
            # dernier arrivé) de l'équipe la plus peuplée vers l'équipe vide,
            # pour rééquilibrer sans perturber tout le monde.
            members = by_pool[largest_pool_id]
            oldest = members[0]
            oldest.pool_id = empty_pool
            oldest.order_in_pool = 0
            for i, u in enumerate(members[1:]):
                u.order_in_pool = i
        else:
            # Pas de surplus disponible : on retire l'équipe vide et on
            # renumérote pour ne pas gaspiller un tour de rotation à vide.
            for u in self._subtitlers():
                if u.pool_id > empty_pool:
                    u.pool_id -= 1
            self.config.num_pools = max(1, self.config.num_pools - 1)

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
    # Débit de parole moyen d'un discours articulé en français (~150 mots/min)
    DEBIT_PAROLE = 2.5  # mots/seconde

    def update_typing_speed(self, user_id: str, words_per_second: float):
        """
        Adapte le slot personnel au temps réellement nécessaire pour taper
        le volume de mots produit par l'audio pendant un slot de base.

        mots_cible = slot_duration_base * DEBIT_PAROLE
        slot_personnel = mots_cible / vitesse_de_frappe, jamais sous la base
        (un rapide n'est jamais raccourci, seuls les lents ont plus de temps),
        borné à MAX_SLOT_PERSONNEL secondes au maximum.
        """
        MAX_SLOT_PERSONNEL = 14
        user = self.users.get(user_id)
        if not user or user.user_id == ADMIN_ID:
            return
        if words_per_second <= 0:
            return

        user.typing_speed = round(words_per_second, 2)
        base = self.config.slot_duration
        mots_cible = base * self.DEBIT_PAROLE
        slot_calcule = mots_cible / words_per_second
        user.slot_duration_personal = round(max(base, min(slot_calcule, MAX_SLOT_PERSONNEL)))

        # Si c'est l'équipe actuellement active, on prolonge réellement son tour
        # (jamais raccourci) au lieu de juste l'afficher sans effet.
        active_pool = (self.config.rotation_index % self.config.num_pools) + 1
        if user.pool_id == active_pool:
            needed_deadline = self.config.turn_start + user.slot_duration_personal
            if needed_deadline > self.config.turn_deadline:
                self.config.turn_deadline = needed_deadline

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
            first_active_pool = (0 % self.config.num_pools) + 1
            user_in_first_pool = (
                user_id and user_id in self.users
                and self.users[user_id].pool_id == first_active_pool
            )
            cd = max(1, int(-elapsed_raw) + 1) if user_in_first_pool else 0
            return {
                "active": True,
                "paused": False,
                "time_left": 0,
                "is_my_turn": False,
                "slot_index": 0,
                "my_slot_index": 0,
                "countdown": cd,
                "config": self.config.model_dump(),
            }

        elapsed = elapsed_raw
        slot_dur = self.config.slot_duration
        overlap = self.config.overlap_duration
        num_pools = self.config.num_pools

        # Initialisation du tout premier tour si besoin
        if self.config.turn_deadline <= 0:
            self.config.turn_start = 0.0
            self.config.turn_deadline = slot_dur

        # Avance la rotation tant que le tour courant (deadline dynamique,
        # potentiellement prolongée) est réellement terminé.
        while elapsed >= self.config.turn_deadline:
            self.config.rotation_index += 1
            self.config.turn_start = self.config.turn_deadline
            self.config.turn_deadline = self.config.turn_start + slot_dur

        rotation_index = self.config.rotation_index
        turn_start = self.config.turn_start
        turn_deadline = self.config.turn_deadline
        planned_deadline = turn_start + slot_dur  # plan de base, sert au repère de l'overlap
        pre_start_begin = planned_deadline - overlap

        time_left = round(max(0.0, turn_deadline - elapsed), 1)

        is_my_turn = False
        my_slot_index = rotation_index
        my_time_left = time_left
        personal_slot = slot_dur
        typing_speed = 0.0
        pre_cd = 0

        if user_id and user_id in self.users and user_id != ADMIN_ID:
            user = self.users[user_id]

            active_pool = (rotation_index % num_pools) + 1
            next_pool = ((rotation_index + 1) % num_pools) + 1
            main = user.pool_id == active_pool
            pre_main = (
                user.pool_id == next_pool
                and elapsed >= pre_start_begin
                and elapsed < turn_deadline
            )
            is_my_turn = main or pre_main

            if main:
                my_slot_index = rotation_index
            elif pre_main:
                my_slot_index = rotation_index + 1
            else:
                cycles_until_mine = (user.pool_id - 1 - rotation_index) % num_pools
                if cycles_until_mine == 0:
                    cycles_until_mine = num_pools
                my_slot_index = rotation_index + cycles_until_mine

            # Nouveau tour détecté : on repart de zéro, indépendamment des
            # tours précédents (pas de continuité de vitesse/slot entre eux).
            if is_my_turn and my_slot_index != user.current_turn_index:
                user.current_turn_index = my_slot_index
                user.current_turn_start = elapsed
                user.typing_speed = 0.0
                user.slot_duration_personal = 0
                user.typing_start = -1.0

            typing_speed = user.typing_speed
            # Slot adapté à la vitesse du sous-titreur (0 = pas encore mesurée → base)
            personal_slot = user.slot_duration_personal or slot_dur

            if main or pre_main:
                my_time_left = round(max(0.0, turn_deadline - elapsed), 1)
            else:
                # En attente : temps réel avant que CETTE équipe redevienne active.
                # On ne connaît pas à l'avance la durée des tours intermédiaires,
                # donc on estime avec la durée de base pour ceux-là.
                my_time_left = round(max(
                    0.0,
                    (turn_deadline - elapsed) + (cycles_until_mine - 1) * slot_dur
                ), 1)

                # Pre-turn countdown : l'équipe suivante voit "votre tour dans Xs"
                if user.pool_id == next_pool:
                    time_until_pre_start = pre_start_begin - elapsed
                    if 0 <= time_until_pre_start <= self.config.countdown:
                        pre_cd = round(time_until_pre_start)

        return {
            "active": self.config.is_active,
            "paused": self.config.is_paused,
            "slot_index": rotation_index,
            "time_left": my_time_left if user_id else time_left,
            "is_my_turn": is_my_turn,
            "my_slot_index": my_slot_index,
            "countdown": pre_cd if user_id and user_id in self.users and user_id != ADMIN_ID else 0,
            "elapsed": round(elapsed, 1),
            "typing_speed": typing_speed,
            "personal_slot": personal_slot,
            "config": self.config.model_dump(),
        }
