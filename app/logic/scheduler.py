import time
from typing import Dict, List, Optional
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
    order_in_pool: int # Définit l'ordre de passage dans le relais

class Scheduler:
    def __init__(self):
        self.config = SessionConfig()
        self.users: Dict[str, User] = {}

    def set_config(self, slot_dur: int, overlap: int, pools: int):
        self.config.slot_duration = slot_dur
        self.config.overlap_duration = overlap
        self.config.num_pools = pools

    def add_user(self, user_id: str, username: str) -> User:
        # On ne compte que les sous-titreurs pour la rotation
        subtitlers = [u for u in self.users.values() if u.user_id != 'admin_master']
        pool_id = (len(subtitlers) % self.config.num_pools) + 1
        
        # Position de l'user dans son pool pour savoir quand c'est son tour
        order = len([u for u in subtitlers if u.pool_id == pool_id])
        
        user = User(user_id=user_id, username=username, pool_id=pool_id, order_in_pool=order)
        self.users[user_id] = user
        return user

    def toggle_pause(self):
        if not self.config.is_active: return
        now = time.time()
        if not self.config.is_paused:
            self.config.is_paused = True
            self.config.paused_at = now
        else:
            self.config.total_paused_time += (now - self.config.paused_at)
            self.config.is_paused = False
            self.config.paused_at = None

    def get_current_state(self, user_id: str = None):
        if not self.config.is_active or not self.config.start_time:
            return {"active": False, "paused": False, "time_left": 0, "is_my_turn": False}

        now = self.config.paused_at if self.config.is_paused else time.time()
        elapsed = now - self.config.start_time - self.config.total_paused_time
        
        # Le cycle de passage de relais
        cycle_time = self.config.slot_duration - self.config.overlap_duration
        
        # Index global du slot actuel
        global_slot_index = int(elapsed // cycle_time)
        
        # Temps restant dans le slot actuel
        time_left = self.config.slot_duration - (elapsed % cycle_time)
        
        # Vérification du tour pour l'utilisateur spécifique
        is_my_turn = False
        if user_id and user_id in self.users:
            user = self.users[user_id]
            subtitlers_in_pool = [u for u in self.users.values() if u.pool_id == user.pool_id]
            n_users = len(subtitlers_in_pool)
            if n_users > 0:
                # C'est mon tour si (Index du Slot % Nombre d'utilisateurs) == Mon Ordre
                is_my_turn = (global_slot_index % n_users) == user.order_in_pool

        return {
            "active": self.config.is_active,
            "paused": self.config.is_paused,
            "slot_index": global_slot_index,
            "time_left": round(max(0, time_left), 1),
            "is_my_turn": is_my_turn,
            "config": self.config.dict()
        }