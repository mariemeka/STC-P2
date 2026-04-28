from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
class Caption(BaseModel):
    user_id: str
    pool_id: int    
    text: str
    timestamp: float
    slot_index: int

class User(BaseModel):
    user_id: str
    username: str
    pool_id: Optional[int] = None

class SessionConfig(BaseModel):
    slot_duration: int = 30
    overlap_duration: int = 5
    num_pools: int = 1
    start_time: Optional[float] = None
    is_active: bool = False