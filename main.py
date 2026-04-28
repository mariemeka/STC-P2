import json
import time
import uuid
import os
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

# Tes modules personnels
from app.logic.scheduler import Scheduler
from app.core.database import save_caption_to_csv
from app.core.models import Caption

app = FastAPI()

# Création du dossier data s'il n'existe pas
if not os.path.exists("data"):
    os.makedirs("data")

# Montage des fichiers statiques
app.mount("/static", StaticFiles(directory="static"), name="static")

# État Global
scheduler = Scheduler()
connections: Dict[str, WebSocket] = {}

# ─── ROUTES HTTP ──────────────────────────────────────────────────────────

@app.get("/")
async def get_index():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/admin")
async def get_admin():
    with open("static/admin.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ─── GESTION DES WEBSOCKETS ────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = None
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            # 1. CONNEXION / CRÉATION DE COMPTE PERSO
            if msg["type"] == "join":
                # On réutilise l'ID existant ou on en crée un nouveau
                user_id = msg.get("user_id") or str(uuid.uuid4())[:8]
                username = msg.get("username", "Anonyme")
                
                # Le scheduler assigne l'utilisateur à un pool
                user = scheduler.add_user(user_id, username)
                connections[user_id] = websocket
                
                # Message de bienvenue avec l'état actuel de la session
                await websocket.send_json({
                    "type": "welcome",
                    "user_id": user_id,
                    "pool_id": user.pool_id,
                    "state": scheduler.get_current_state()
                })
                
                # On prévient l'Admin que la liste des users a changé
                await broadcast_user_list()

            # 2. CONTRÔLE ADMIN (DÉMARRER / ARRÊTER)
            elif msg["type"] == "admin_start":
                scheduler.set_config(
                    slot_dur=int(msg["slot"]), 
                    overlap=int(msg["overlap"]), 
                    pools=int(msg["pools"])
                )
                scheduler.config.start_time = time.time()
                scheduler.config.is_active = True
                
                await broadcast({
                    "type": "session_started", 
                    "state": scheduler.get_current_state()
                })

            elif msg["type"] == "admin_stop":
                scheduler.config.is_active = False
                await broadcast({"type": "session_ended"})

            # 3. RÉCEPTION DU SOUS-TITRE (SAUVEGARDE CSV)
            elif msg["type"] == "caption":
                user = scheduler.users.get(user_id)
                if user and scheduler.config.is_active:
                    new_caption = Caption(
                        user_id=user_id,
                        pool_id=user.pool_id,
                        text=msg["text"], 
                        timestamp=time.time(),
                        slot_index=msg["slot_index"]
                    )
                    # Sauvegarde immédiate dans le dossier /data
                    save_caption_to_csv("live_session", new_caption)
                    
                    # Diffusion du texte (pour le futur viewer)
                    await broadcast({
                        "type": "new_text", 
                        "pool": user.pool_id, 
                        "text": msg["text"],
                        "user": user.username
                    })

            # 4. SYNCHRONISATION (ITÉRATION DES SLOTS)
            elif msg["type"] == "get_sync":
                await websocket.send_json({
                    "type": "sync_update",
                    "state": scheduler.get_current_state()
                })

    except WebSocketDisconnect:
        if user_id in connections:
            del connections[user_id]
        await broadcast_user_list()

# ─── UTILITAIRES ───────────────────────────────────────────────────────────

async def broadcast_user_list():
    """Envoie la liste des connectés à tout le monde (pour l'admin)"""
    user_list = [{"id": u.user_id, "name": u.username, "pool": u.pool_id} for u in scheduler.users.values()]
    await broadcast({
        "type": "user_update",
        "users": user_list
    })

async def broadcast(data: dict):
    """Envoie un message à tous les sockets actifs"""
    for ws in list(connections.values()):
        try:
            await ws.send_json(data)
        except:
            pass