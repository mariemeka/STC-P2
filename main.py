from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from functools import reduce
import json
import os
import time
import uuid

from scheduler import Scheduler
from fusion import fuse, _merge_two

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── État global ────────────────────────────────────────────────────────────
scheduler = Scheduler(
    slot_duration=int(os.getenv("STC_SLOT_DURATION", "30")),
    overlap=int(os.getenv("STC_OVERLAP", "5")),
)
active_connections: dict = {}      # user_id -> WS (sub-titreurs + viewers)
confirmed_users: set = set()        # user_ids que l'admin a valides
captions: dict = {}
audio_state = {
    "url": None,         # URL publique du fichier (ex: /static/audio/current.mp3)
    "filename": None,    # nom d'origine pour affichage
    "is_playing": False,
    "started_at": None,  # time.time() du moment ou la lecture a demarre
}
AUDIO_DIR = "static/audio"

# ── Routes HTTP ────────────────────────────────────────────────────────────

@app.get("/")
async def get_subtitler():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/viewer")
async def get_viewer():
    with open("static/viewer.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/admin")
async def get_admin():
    with open("static/admin.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/status")
async def get_status():
    return {
        "connected_users": scheduler.connected_users(),
        "active_user": scheduler.get_active_user(),
        "total_slots": len(scheduler.slots),
        "total_captions": sum(len(v) for v in captions.values()),
    }

def pending_user_ids() -> list[str]:
    """User_ids dans le lobby (connectes mais pas confirmes, et pas viewers)."""
    return [
        uid for uid in active_connections
        if uid not in confirmed_users and not uid.startswith("viewer_")
    ]


@app.post("/admin/confirm/{user_id}")
async def confirm_user(user_id: str):
    """Admin confirme un user du lobby -> il recoit son slot et peut bosser."""
    if user_id not in active_connections:
        raise HTTPException(404, f"User {user_id} non connecte")
    if user_id.startswith("viewer_"):
        raise HTTPException(400, "Les viewers n'ont pas besoin de confirmation")
    if user_id in confirmed_users:
        return {"ok": True, "already_confirmed": True}

    confirmed_users.add(user_id)
    slot = scheduler.assign(user_id)
    ws = active_connections[user_id]
    await ws.send_json(_slot_payload(slot) | {"user_id": user_id})

    # Si une session audio globale est en cours, sync immediatement
    if audio_state["is_playing"]:
        await ws.send_json({
            "type": "audio_start",
            "url": audio_state["url"],
            "started_at": audio_state["started_at"],
        })

    await broadcast({
        "type": "user_joined",
        "user_id": user_id,
        "connected_users": scheduler.connected_users(),
    })
    print(f"[CONFIRM] {user_id} -> chunk {slot.slot_index} [{slot.audio_start:.0f}s-{slot.audio_end:.0f}s]")
    return {"ok": True, "slot_index": slot.slot_index}


@app.post("/admin/reset")
async def reset_session():
    """Vide tous les slots/captions, demote les sub-titreurs en lobby."""
    # Memo des users a demoter (pour leur envoyer un signal)
    demoted = list(confirmed_users)

    confirmed_users.clear()
    scheduler.reset()
    captions.clear()
    audio_state["is_playing"] = False
    audio_state["started_at"] = None

    # Broadcast a tout le monde (sub-titreurs et viewers)
    await broadcast({"type": "session_reset"})

    # Re-envoie un "pending" aux users qui etaient confirmes
    for uid in demoted:
        if uid in active_connections:
            try:
                await active_connections[uid].send_json({
                    "type": "pending",
                    "user_id": uid,
                    "message": "Session reset par l'admin. En attente d'une nouvelle confirmation.",
                })
            except Exception:
                pass

    print(f"[RESET] session videe, {len(demoted)} user(s) remis en lobby")
    return {"ok": True, "demoted": len(demoted)}


@app.get("/admin/data")
async def get_admin_data():
    now = time.time()

    slots_data = []
    for slot in scheduler.slots:
        contribs = captions.get(slot.slot_index, [])
        fused_text = fuse([c["text"] for c in contribs]) if contribs else ""
        slots_data.append({
            "index": slot.slot_index,
            "audio_start": slot.audio_start,
            "audio_end": slot.audio_end,
            "assigned_to": slot.assigned_to,
            "completed": slot.completed,
            "contributors": len(contribs),
            "text": fused_text,
        })

    completed_count = sum(1 for s in scheduler.slots if s.completed)

    return {
        "connected_users": scheduler.connected_users(),
        "pending_users": pending_user_ids(),
        "confirmed_users": sorted(confirmed_users),
        "active_user": scheduler.get_active_user(),
        "slot_duration": scheduler.slot_duration,
        "overlap": scheduler.overlap,
        "total_slots": len(scheduler.slots),
        "completed_slots": completed_count,
        "total_captions": sum(len(v) for v in captions.values()),
        "slots": slots_data,
        "audio": {
            "url": audio_state["url"],
            "filename": audio_state["filename"],
            "is_playing": audio_state["is_playing"],
            "started_at": audio_state["started_at"],
            "elapsed_s": round(now - audio_state["started_at"], 1)
                if audio_state["started_at"] else None,
        },
    }


# ── Audio (lecteur partage) ────────────────────────────────────────────────

@app.post("/audio/upload")
async def upload_audio(file: UploadFile = File(...)):
    if audio_state["is_playing"]:
        audio_state["is_playing"] = False
        audio_state["started_at"] = None
        await broadcast({"type": "audio_stop"})

    os.makedirs(AUDIO_DIR, exist_ok=True)
    # Nettoyage des anciens fichiers
    for old in os.listdir(AUDIO_DIR):
        if old.startswith("current"):
            try:
                os.remove(os.path.join(AUDIO_DIR, old))
            except OSError:
                pass

    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp3"
    if ext not in (".mp3", ".wav", ".ogg", ".m4a", ".webm"):
        raise HTTPException(400, f"Format audio non supporte: {ext}")

    path = os.path.join(AUDIO_DIR, f"current{ext}")
    contents = await file.read()
    with open(path, "wb") as f:
        f.write(contents)

    audio_state["url"] = f"/static/audio/current{ext}"
    audio_state["filename"] = file.filename

    # Notifie tous les clients deja connectes
    await broadcast({
        "type": "audio_available",
        "url": audio_state["url"],
        "filename": audio_state["filename"],
    })
    print(f"[AUDIO] uploaded {file.filename} ({len(contents)} bytes)")
    return {"ok": True, "url": audio_state["url"], "filename": file.filename, "size_bytes": len(contents)}


@app.post("/audio/start")
async def start_audio():
    if not audio_state["url"]:
        raise HTTPException(400, "Aucun audio charge. Upload d'abord un fichier.")
    audio_state["is_playing"] = True
    audio_state["started_at"] = time.time()
    await broadcast({
        "type": "audio_start",
        "url": audio_state["url"],
        "started_at": audio_state["started_at"],
    })
    print(f"[AUDIO] start {audio_state['filename']}")
    return {"ok": True, "started_at": audio_state["started_at"]}


@app.post("/audio/stop")
async def stop_audio():
    audio_state["is_playing"] = False
    audio_state["started_at"] = None
    await broadcast({"type": "audio_stop"})
    print("[AUDIO] stop")
    return {"ok": True}

# ── Export ─────────────────────────────────────────────────────────────────

def _srt_timestamp(seconds: float) -> str:
    """Formate un offset en secondes au format SRT 'HH:MM:SS,mmm'."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _fused_slots() -> list[tuple[float, float, str]]:
    """Retourne (audio_start, audio_end, fused_text) pour chaque slot non-vide.

    Les offsets sont deja relatifs au debut de l'audio (pas de t0 a soustraire).
    """
    out = []
    for slot in scheduler.slots:
        contribs = [c["text"] for c in captions.get(slot.slot_index, [])]
        text = fuse(contribs).strip()
        if not text:
            continue
        out.append((slot.audio_start, slot.audio_end, text))
    return out


@app.get("/export.srt")
async def export_srt():
    entries = _fused_slots()
    lines = []
    for i, (start, end, text) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return PlainTextResponse(
        content="\n".join(lines),
        media_type="application/x-subrip",
        headers={"Content-Disposition": 'attachment; filename="stc.srt"'},
    )


@app.get("/export.txt")
async def export_txt():
    """Texte continu : on fusionne les chunks adjacents en exploitant l'overlap."""
    entries = _fused_slots()
    if not entries:
        body = ""
    else:
        texts = [t for _, _, t in entries]
        body = reduce(_merge_two, texts)
    return PlainTextResponse(
        content=body,
        headers={"Content-Disposition": 'attachment; filename="stc.txt"'},
    )

# ── WebSocket sous-titreurs ────────────────────────────────────────────────

def _slot_payload(slot):
    """Construit le message slot_assigned pour un slot donne."""
    return {
        "type": "slot_assigned",
        "slot_index": slot.slot_index,
        "audio_start": slot.audio_start,
        "audio_end": slot.audio_end,
        "audio_url": audio_state["url"],
        "audio_filename": audio_state["filename"],
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    user_id = str(uuid.uuid4())[:8]

    # Le user est en lobby — il attend que l'admin clique "Confirmer"
    await websocket.send_json({
        "type": "pending",
        "user_id": user_id,
        "message": "En attente de confirmation par l'admin",
    })
    active_connections[user_id] = websocket

    await broadcast_admin_data_change()
    print(f"[PENDING] {user_id} en attente de confirmation")

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg["type"] == "caption":
                # Ignore les captions des users non confirmes
                if user_id not in confirmed_users:
                    continue
                slot_index = msg["slot_index"]
                text = msg["text"].strip()
                if not text:
                    continue

                if slot_index not in captions:
                    captions[slot_index] = []

                existing = next(
                    (c for c in captions[slot_index] if c["user_id"] == user_id),
                    None
                )
                if existing:
                    existing["text"] = text
                else:
                    captions[slot_index].append({"user_id": user_id, "text": text})

                contributions = [c["text"] for c in captions[slot_index]]
                fused_text = fuse(contributions)

                await broadcast({
                    "type": "fused_caption",
                    "slot_index": slot_index,
                    "text": fused_text,
                    "contributors": len(contributions),
                })

            elif msg["type"] == "submit":
                if user_id not in confirmed_users:
                    continue
                slot_index = msg.get("slot_index")
                new_slot = scheduler.mark_completed(user_id, slot_index)
                print(f"[SUBMIT] {user_id} chunk {slot_index} done -> chunk {new_slot.slot_index}")
                await websocket.send_json(_slot_payload(new_slot))

    except WebSocketDisconnect:
        print(f"[X] {user_id} deconnecte")
        if user_id in active_connections:
            del active_connections[user_id]
        confirmed_users.discard(user_id)
        scheduler.release_user(user_id)
        await broadcast_admin_data_change()


async def broadcast_admin_data_change():
    """Broadcast generique pour faire refresh la page admin (qui poll deja /admin/data)."""
    await broadcast({
        "type": "admin_state_changed",
        "connected": len(active_connections),
        "confirmed": len(confirmed_users),
    })

# ── WebSocket viewer ───────────────────────────────────────────────────────

@app.websocket("/ws/viewer")
async def websocket_viewer(websocket: WebSocket):
    await websocket.accept()
    viewer_id = "viewer_" + str(uuid.uuid4())[:4]
    active_connections[viewer_id] = websocket
    if audio_state["is_playing"]:
        await websocket.send_json({
            "type": "audio_start",
            "url": audio_state["url"],
            "started_at": audio_state["started_at"],
        })
    print(f"[VIEWER] connecte ({viewer_id})")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        print(f"[VIEWER] deconnecte ({viewer_id})")
        if viewer_id in active_connections:
            del active_connections[viewer_id]

# ── Broadcast ──────────────────────────────────────────────────────────────

async def broadcast(message: dict):
    disconnected = []
    for uid, ws in active_connections.items():
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(uid)
    for uid in disconnected:
        del active_connections[uid]
        scheduler.release_slot(uid)