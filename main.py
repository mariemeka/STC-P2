from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import json
import uuid

from scheduler import Scheduler
from fusion import fuse

# ── Application ────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── État global en mémoire ─────────────────────────────────────────────────
scheduler = Scheduler(slot_duration=30, overlap=5)

# { user_id: websocket }
active_connections: dict = {}

# { slot_index: [ {user_id, text}, ... ] }
captions: dict = {}


# ── Routes HTTP ────────────────────────────────────────────────────────────

@app.get("/")
async def get_subtitler():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/viewer")
async def get_viewer():
    with open("static/viewer.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/status")
async def get_status():
    """Endpoint utile pour déboguer : voir l'état du serveur"""
    return {
        "connected_users": scheduler.connected_users(),
        "active_user": scheduler.get_active_user(),
        "total_slots": len(scheduler.slots),
        "total_captions": sum(len(v) for v in captions.values()),
    }


# ── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    user_id = str(uuid.uuid4())[:8]
    slot = scheduler.assign_slot(user_id)
    active_connections[user_id] = websocket

    # On informe le sous-titreur de son slot (timestamps réels)
    await websocket.send_json({
        "type": "slot_assigned",
        "user_id": user_id,
        "slot_index": slot.slot_index,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
    })

    # On informe tout le monde qu'un nouveau sous-titreur est connecté
    await broadcast({
        "type": "user_joined",
        "user_id": user_id,
        "connected_users": scheduler.connected_users(),
    })

    print(f"✅ {user_id} connecté → slot {slot.slot_index}")

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            # ── Réception d'un sous-titre ──────────────────────────────────
            if msg["type"] == "caption":
                slot_index = msg["slot_index"]
                text = msg["text"].strip()

                if not text:
                    continue

                # Stockage de la contribution
                if slot_index not in captions:
                    captions[slot_index] = []

                # On met à jour si cet user a déjà soumis pour ce slot
                existing = next(
                    (c for c in captions[slot_index] if c["user_id"] == user_id),
                    None
                )
                if existing:
                    existing["text"] = text
                else:
                    captions[slot_index].append({
                        "user_id": user_id,
                        "text": text
                    })

                # Fusion et diffusion
                contributions = [c["text"] for c in captions[slot_index]]
                fused_text = fuse(contributions)

                await broadcast({
                    "type": "fused_caption",
                    "slot_index": slot_index,
                    "text": fused_text,
                    "contributors": len(contributions),
                })

    except WebSocketDisconnect:
        print(f"❌ {user_id} déconnecté")
        del active_connections[user_id]
        scheduler.release_slot(user_id)

        # On informe tout le monde
        await broadcast({
            "type": "user_left",
            "user_id": user_id,
            "connected_users": scheduler.connected_users(),
        })


# ── Broadcast ──────────────────────────────────────────────────────────────

async def broadcast(message: dict):
    """Envoie un message à tous les connectés"""
    disconnected = []
    for uid, ws in active_connections.items():
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(uid)

    # Nettoyage des connexions mortes
    for uid in disconnected:
        del active_connections[uid]
        scheduler.release_slot(uid)