from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import json
import uuid

from scheduler import Scheduler
from fusion import fuse

# ── Création de l'application ──────────────────────────────────────────────
app = FastAPI()

# Sert les fichiers HTML du dossier static/
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── État global en mémoire (pas de base de données) ───────────────────────
scheduler = Scheduler(slot_duration=30, overlap=5)

# Dictionnaire de toutes les connexions WebSocket actives
# { user_id: websocket }
active_connections: dict = {}

# Toutes les captions reçues
# { slot_index: [ {user_id, text}, ... ] }
captions: dict = {}


# ── Routes HTTP simples ────────────────────────────────────────────────────

@app.get("/")
async def get_subtitler():
    """Page pour les sous-titreurs"""
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/viewer")
async def get_viewer():
    """Page d'affichage pour le projecteur/prof"""
    with open("static/viewer.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── WebSocket principal ────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # On génère un ID unique pour ce sous-titreur
    user_id = str(uuid.uuid4())[:8]

    # On lui assigne un slot
    slot = scheduler.assign_slot(user_id)

    # On l'enregistre dans les connexions actives
    active_connections[user_id] = websocket

    # On lui envoie son slot assigné
    await websocket.send_json({
        "type": "slot_assigned",
        "user_id": user_id,
        "slot_index": slot.slot_index,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
    })

    print(f"✅ {user_id} connecté → slot {slot.slot_index}")

    try:
        while True:
            # On attend un message du sous-titreur
            data = await websocket.receive_text()
            msg = json.loads(data)

            # Le sous-titreur envoie un sous-titre
            if msg["type"] == "caption":
                slot_index = msg["slot_index"]
                text = msg["text"]

                # On stocke la contribution
                if slot_index not in captions:
                    captions[slot_index] = []
                captions[slot_index].append({
                    "user_id": user_id,
                    "text": text
                })

                # On fusionne toutes les contributions de ce slot
                contributions = [c["text"] for c in captions[slot_index]]
                fused_text = fuse(contributions)

                # On diffuse le résultat fusionné à tout le monde
                await broadcast({
                    "type": "fused_caption",
                    "slot_index": slot_index,
                    "text": fused_text
                })

    except WebSocketDisconnect:
        # Le sous-titreur s'est déconnecté
        print(f"❌ {user_id} déconnecté")
        del active_connections[user_id]
        scheduler.release_slot(user_id)


# ── Fonction utilitaire : envoyer un message à tous ───────────────────────

async def broadcast(message: dict):
    """Envoie un message JSON à tous les connectés"""
    for ws in active_connections.values():
        await ws.send_json(message)
