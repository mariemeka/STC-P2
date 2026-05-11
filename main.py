import json
import time
import uuid
import os
import asyncio
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from app.logic.scheduler import Scheduler
from app.logic.fusion import fuse_session
from app.engine.formatter import to_srt, to_txt
from app.core.database import save_caption_to_csv
from app.core.models import Caption

app = FastAPI()

if not os.path.exists("data"):
    os.makedirs("data")

app.mount("/static", StaticFiles(directory="static"), name="static")

scheduler = Scheduler()
connections: Dict[str, WebSocket] = {}
viewers: Dict[str, WebSocket] = {}
session_id = "live_session"

# ─── ROUTES HTTP ──────────────────────────────────────────────────────────

@app.get("/")
async def get_index():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/admin")
async def get_admin():
    with open("static/admin.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/viewer")
async def get_viewer():
    with open("static/viewer.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/download/{filename}")
async def download(filename: str):
    path = os.path.join("data", filename)
    if not os.path.isfile(path):
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(path, filename=filename)

# ─── WEBSOCKET ────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = None
    is_viewer = False

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            t = msg.get("type")

            if t == "viewer_join":
                vid = str(uuid.uuid4())[:8]
                viewers[vid] = websocket
                user_id = vid
                is_viewer = True
                await websocket.send_json({
                    "type": "viewer_welcome",
                    "state": scheduler.get_current_state()
                })

            elif t == "join":
                user_id = msg.get("user_id") or str(uuid.uuid4())[:8]
                username = msg.get("username", "Anonyme")
                user = scheduler.add_user(user_id, username)
                connections[user_id] = websocket

                await websocket.send_json({
                    "type": "welcome",
                    "user_id": user_id,
                    "pool_id": user.pool_id,
                    "order_in_pool": user.order_in_pool,
                    "state": scheduler.get_current_state(user_id)
                })
                await broadcast_user_list()

            elif t == "admin_start":
                global session_id
                session_id = f"sess_{int(time.time())}"
                scheduler.set_config(
                    slot_dur=int(msg["slot"]),
                    overlap=int(msg["overlap"]),
                    pools=int(msg["pools"])
                )
                scheduler.config.start_time = time.time()
                scheduler.config.is_active = True
                scheduler.config.is_paused = False
                scheduler.config.total_paused_time = 0.0
                scheduler.config.paused_at = None

                await broadcast_state("session_started")

            elif t == "admin_pause":
                scheduler.toggle_pause()
                await broadcast_state("session_paused")

            elif t == "admin_stop":
                scheduler.config.is_active = False
                # Fusion + export
                pool_files = fuse_session(session_id, scheduler.config.num_pools)
                exports = []
                for pool_id, captions in pool_files.items():
                    srt_path = os.path.join("data", f"{session_id}_pool_{pool_id}.srt")
                    txt_path = os.path.join("data", f"{session_id}_pool_{pool_id}.txt")
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(to_srt(captions, scheduler.config.slot_duration))
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(to_txt(captions))
                    exports.append(os.path.basename(srt_path))
                    exports.append(os.path.basename(txt_path))

                await broadcast({"type": "session_ended", "files": exports})

            elif t == "caption":
                user = scheduler.users.get(user_id)
                if user and scheduler.config.is_active and not scheduler.config.is_paused:
                    state = scheduler.get_current_state(user_id)
                    if not state.get("is_my_turn"):
                        # Pas son tour côté serveur — on ignore (client en retard)
                        continue
                    slot_idx = state.get("my_slot_index", 0)
                    new_caption = Caption(
                        user_id=user_id,
                        pool_id=user.pool_id,
                        text=msg["text"],
                        timestamp=time.time(),
                        slot_index=slot_idx,
                    )
                    save_caption_to_csv(session_id, new_caption)
                    await broadcast({
                        "type": "new_text",
                        "pool": user.pool_id,
                        "text": msg["text"],
                        "user": user.username,
                        "slot_index": slot_idx,
                    })

            elif t == "get_sync":
                await websocket.send_json({
                    "type": "sync_update",
                    "state": scheduler.get_current_state(user_id)
                })

    except WebSocketDisconnect:
        if user_id:
            connections.pop(user_id, None)
            viewers.pop(user_id, None)
            scheduler.remove_user(user_id)
        if not is_viewer:
            await broadcast_user_list()
            # Les ordres ont pu changer → repousser l'état à chaque sous-titreur
            await broadcast_state("sync_update")

# ─── BACKGROUND: PUSH SYNC PERIODIQUEMENT ─────────────────────────────────

@app.on_event("startup")
async def start_sync_loop():
    asyncio.create_task(sync_loop())

async def sync_loop():
    while True:
        await asyncio.sleep(1.0)
        if not scheduler.config.is_active:
            continue
        for uid, ws in list(connections.items()):
            try:
                await ws.send_json({
                    "type": "sync_update",
                    "state": scheduler.get_current_state(uid)
                })
            except Exception:
                pass
        for vid, ws in list(viewers.items()):
            try:
                await ws.send_json({
                    "type": "sync_update",
                    "state": scheduler.get_current_state()
                })
            except Exception:
                pass

# ─── UTILS ────────────────────────────────────────────────────────────────

async def broadcast_user_list():
    user_list = [
        {"id": u.user_id, "name": u.username, "pool": u.pool_id, "order": u.order_in_pool}
        for u in scheduler.users.values()
    ]
    await broadcast({"type": "user_update", "users": user_list})

async def broadcast_state(event_type: str):
    for uid, ws in list(connections.items()):
        try:
            await ws.send_json({
                "type": event_type,
                "state": scheduler.get_current_state(uid)
            })
        except Exception:
            pass
    for vid, ws in list(viewers.items()):
        try:
            await ws.send_json({
                "type": event_type,
                "state": scheduler.get_current_state()
            })
        except Exception:
            pass

async def broadcast(data: dict):
    for ws in list(connections.values()) + list(viewers.values()):
        try:
            await ws.send_json(data)
        except Exception:
            pass
