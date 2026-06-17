import json
import time
import uuid
import os
import asyncio
from typing import Dict
from symspellpy import SymSpell, Verbosity
from pygrammalecte import grammalecte_text, GrammalecteGrammarMessage
import unicodedata
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from app.logic.scheduler import Scheduler
from app.logic.fusion import fuse_session
from app.engine.formatter import to_srt, to_txt
from app.core.database import save_caption_to_csv
from app.core.models import Caption

app = FastAPI()

if not os.path.exists("data"):
    os.makedirs("data")

# ── Correction orthographique ─────────────────────────────────────────────

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

_sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
_dict_loaded = False
if os.path.exists("fr-100k.txt"):
    _sym.load_dictionary("fr-100k.txt", term_index=0, count_index=1, encoding="utf-8")
    _dict_loaded = True

_accent_map: dict = {}
if _dict_loaded:
    for entry in _sym.words.keys():
        stripped = _strip_accents(entry)
        if stripped != entry and stripped not in _accent_map:
            _accent_map[stripped] = entry

# Types de règles grammalecte qu'on garde (accords, participes passés...)
_GRAM_SAFE_TYPES = {"conj", "gn", "ppas"}
_GRAM_SAFE_CONF_RULES = {"g2__conf_ce_ceux_se__b2_a1_1"}
# Règle "ma/ta/sa" -> "mon/ton/son" confond souvent "ma" avec "m'a", on l'exclut
_GRAM_UNSAFE_RULE_PREFIXES = ("g3__gn_ma_ta_sa_1m",)

# Premier appel = chargement des règles (~10s)
try:
    list(grammalecte_text("test"))
except Exception:
    pass

def _grammar_correct(text: str) -> str:
    try:
        results = list(grammalecte_text(text))
    except Exception:
        return text
    edits = []
    for r in results:
        if not isinstance(r, GrammalecteGrammarMessage):
            continue
        if len(r.suggestions) != 1:
            continue
        if r.rule.startswith(_GRAM_UNSAFE_RULE_PREFIXES):
            continue
        if r.type in _GRAM_SAFE_TYPES or (r.type == "conf" and r.rule in _GRAM_SAFE_CONF_RULES):
            edits.append((r.start, r.end, r.suggestions[0]))
    edits.sort(reverse=True)
    for start, end, repl in edits:
        text = text[:start] + repl + text[end:]
    return text

# Élisions tapées sans apostrophe (jai -> j'ai, cest -> c'est, quon -> qu'on...)
_ELISION_PREFIXES = ["qu", "j", "m", "t", "s", "l", "c", "d", "n"]
_ELISION_VOWELS = set("aeiouyàâäéèêëïîôöùûü") | {"h"}

def _try_elision(w_lower: str):
    for p in _ELISION_PREFIXES:
        if w_lower.startswith(p) and len(w_lower) > len(p) + 1:
            rest = w_lower[len(p):]
            if rest[0] not in _ELISION_VOWELS:
                continue
            if _sym.lookup(rest, Verbosity.TOP, max_edit_distance=0):
                return f"{p}'{rest}"
            if rest in _accent_map:
                return f"{p}'{_accent_map[rest]}"
    return None

def correct_text(text: str, final: bool = False) -> str:
    if not _dict_loaded:
        return text
    text = text.replace("\u2019", "'").replace("\u02bc", "'")
    words = text.split(" ")
    trailing_space = text.endswith(" ")
    complete = words if (trailing_space or final) else words[:-1]
    last = [] if (trailing_space or final) else [words[-1]]
    corrected = []
    for word in complete:
        if not word:
            corrected.append(word)
            continue
        w_lower = word.lower()
        if "'" in w_lower:
            sep = "'"
            if _sym.lookup(w_lower, Verbosity.TOP, max_edit_distance=0):
                corrected.append(word)
                continue
            parts = w_lower.split(sep, 1)
            fixed_parts = []
            for part in parts:
                if not part or len(part) < 3:
                    fixed_parts.append(part)
                elif _sym.lookup(part, Verbosity.TOP, max_edit_distance=0):
                    fixed_parts.append(part)
                elif part in _accent_map:
                    fixed_parts.append(_accent_map[part])
                else:
                    sugg = _sym.lookup(part, Verbosity.CLOSEST, max_edit_distance=2)
                    fixed_parts.append(sugg[0].term if sugg else part)
            corrected.append(sep.join(fixed_parts))
            continue
        if _sym.lookup(w_lower, Verbosity.TOP, max_edit_distance=0):
            corrected.append(word)
            continue
        if w_lower in _accent_map:
            fix = _accent_map[w_lower]
            corrected.append(fix.capitalize() if word[0].isupper() else fix)
            continue
        elision = _try_elision(w_lower)
        if elision:
            corrected.append(elision.capitalize() if word[0].isupper() else elision)
            continue
        suggestions = _sym.lookup(w_lower, Verbosity.CLOSEST, max_edit_distance=2)
        if suggestions:
            fix = suggestions[0].term
            corrected.append(fix.capitalize() if word[0].isupper() else fix)
        else:
            corrected.append(word)
    result = " ".join(corrected + last) + (" " if trailing_space else "")
    if final:
        result = _grammar_correct(result)
    return result

# ─────────────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

scheduler = Scheduler()
connections: Dict[str, WebSocket] = {}
viewers: Dict[str, WebSocket] = {}
session_id = "live_session"
last_live_save: Dict[tuple, float] = {}
LIVE_SAVE_THROTTLE = 1.0

# ─── ROUTES HTTP ──────────────────────────────────────────────────────────

@app.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    allowed = {".mp4", ".mp3", ".wav", ".ogg", ".webm"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        return JSONResponse({"error": "Format non supporté"}, status_code=400)
    os.makedirs("static/media", exist_ok=True)
    dest = f"static/media/audio{ext}"
    with open(dest, "wb") as f:
        f.write(await file.read())
    return JSONResponse({"url": f"/static/media/audio{ext}"})

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

@app.get("/results")
async def get_results():
    with open("static/results.html", encoding="utf-8") as f:
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
                last_live_save.clear()
                scheduler.set_config(
                    slot_dur=int(msg.get("slot", 20)),
                    overlap=int(msg.get("overlap", 5)),
                    pools=int(msg.get("pools", 1)),
                    countdown=int(msg.get("countdown", 3)),
                )
                scheduler.config.start_time = time.time() + scheduler.config.countdown
                scheduler.config.is_active = True
                scheduler.config.is_paused = False
                scheduler.config.total_paused_time = 0.0
                scheduler.config.paused_at = None
                await broadcast_state("session_started")

            elif t == "admin_pause":
                scheduler.toggle_pause()
                await broadcast_state("session_paused")

            elif t == "admin_stop":
                await stop_and_export()

            elif t == "admin_assign":
                target_id = msg.get("user_id")
                new_pool = int(msg.get("pool_id", 1))
                new_order = int(msg.get("order", 0))
                scheduler.assign_user(target_id, new_pool, new_order)
                await broadcast_user_list()
                await broadcast_state("sync_update")

            elif t == "caption":
                user = scheduler.users.get(user_id)
                if user and scheduler.config.is_active and not scheduler.config.is_paused:
                    state = scheduler.get_current_state(user_id)
                    if state.get("countdown", 0) > 0:
                        continue
                    if not state.get("is_my_turn"):
                        continue
                    slot_idx = state.get("my_slot_index", 0)
                    corrected = correct_text(msg["text"], final=True)
                    new_caption = Caption(
                        user_id=user_id,
                        pool_id=user.pool_id,
                        text=corrected,
                        timestamp=time.time(),
                        slot_index=slot_idx,
                    )
                    save_caption_to_csv(session_id, new_caption)
                    await broadcast({
                        "type": "new_text",
                        "pool": user.pool_id,
                        "text": corrected,
                        "user": user.username,
                        "slot_index": slot_idx,
                    })

            elif t == "live_typing":
                user = scheduler.users.get(user_id)
                if user and scheduler.config.is_active and not scheduler.config.is_paused:
                    state = scheduler.get_current_state(user_id)
                    if state.get("countdown", 0) > 0:
                        continue
                    if state.get("is_my_turn"):
                        text = msg.get("text", "")
                        slot_idx = state.get("my_slot_index", 0)
                        corrected_live = correct_text(text, final=False)

                        # ── Mesure vitesse de frappe + adaptation ─────────
                        nb_mots = len(text.split()) if text.strip() else 0
                        elapsed = state.get("elapsed", 1)
                        cycle_time = max(1, scheduler.config.slot_duration - scheduler.config.overlap_duration)
                        temps_dans_slot = elapsed % cycle_time
                        if temps_dans_slot > 0 and nb_mots > 0:
                            vitesse = round(nb_mots / temps_dans_slot, 2)
                            scheduler.update_typing_speed(user_id, vitesse)
                            await broadcast_user_list()  # mise à jour admin
                        # ─────────────────────────────────────────────────

                        if text.strip():
                            key = (user_id, slot_idx)
                            now_ts = time.time()
                            if now_ts - last_live_save.get(key, 0) >= LIVE_SAVE_THROTTLE:
                                save_caption_to_csv(session_id, Caption(
                                    user_id=user_id,
                                    pool_id=user.pool_id,
                                    text=corrected_live,
                                    timestamp=now_ts,
                                    slot_index=slot_idx,
                                ))
                                last_live_save[key] = now_ts

                        await broadcast_to_viewers({
                            "type": "live_typing",
                            "pool": user.pool_id,
                            "user": user.username,
                            "text": corrected_live,
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
            await broadcast_state("sync_update")

# ─── BACKGROUND SYNC + KEEPALIVE ──────────────────────────────────────────

@app.on_event("startup")
async def start_sync_loop():
    asyncio.create_task(sync_loop())

async def sync_loop():
    tick = 0
    while True:
        await asyncio.sleep(0.5)
        tick += 1
        if scheduler.config.is_active:
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
        elif tick % 60 == 0:
            for ws in list(connections.values()) + list(viewers.values()):
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    pass
async def stop_and_export():
    if not scheduler.config.is_active:
        return
    scheduler.config.is_active = False
    pool_files = fuse_session(session_id, scheduler.config.num_pools)
    exports = []
    for pool_id, captions in pool_files.items():

        # ── Correction post-fusion sur le texte final complet ─────────
        texte_complet = " ".join(c["text"] for c in captions if c["text"].strip())
        texte_corrige = correct_text(texte_complet, final=True)
        mots = texte_corrige.split()
        mots_par_slot = max(1, len(mots) // len(captions)) if captions else len(mots)
        for i, caption in enumerate(captions):
            debut = i * mots_par_slot
            fin = debut + mots_par_slot if i < len(captions) - 1 else len(mots)
            caption["text"] = " ".join(mots[debut:fin])
        # ─────────────────────────────────────────────────────────────

        srt_path = os.path.join("data", f"{session_id}_pool_{pool_id}.srt")
        txt_path = os.path.join("data", f"{session_id}_pool_{pool_id}.txt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(to_srt(captions, scheduler.config.slot_duration))
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(to_txt(captions))
        exports.append(os.path.basename(srt_path))
        exports.append(os.path.basename(txt_path))
    await broadcast({"type": "session_ended", "files": exports})

# ─── UTILS ────────────────────────────────────────────────────────────────

async def broadcast_user_list():
    user_list = [
        {
            "id": u.user_id,
            "name": u.username,
            "pool": u.pool_id,
            "order": u.order_in_pool,
            "speed": u.typing_speed,
            "personal_slot": u.slot_duration_personal or 0,
        }
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

async def broadcast_to_viewers(data: dict):
    for ws in list(viewers.values()):
        try:
            await ws.send_json(data)
        except Exception:
            pass
    admin_ws = connections.get("admin_master")
    if admin_ws:
        try:
            await admin_ws.send_json(data)
        except Exception:
            pass
