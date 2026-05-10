# STC — Sous-Titrage Collaboratif

Plusieurs personnes transcrivent un même fichier audio en parallèle. L'audio est découpé en chunks distribués aux sous-titreurs, l'algorithme de fusion concatène les contributions avec détection d'overlap, et le résultat est exporté au format SRT.

---

## 🛠 Installation

```bash
# 1. Cloner le repo
git clone https://github.com/mariemeka/STC-P2.git
cd STC-P2
git checkout Ilyass

# 2. Environnement virtuel
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

# 3. Dépendances
pip install -r requirements.txt
```

---

## 🚀 Lancer le serveur

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> Sur Windows, si tu as une erreur d'encodage Unicode, ajoute `set PYTHONIOENCODING=utf-8` avant la commande.

### Configuration via variables d'environnement (optionnel)

| Variable | Défaut | Description |
|---|---|---|
| `STC_SLOT_DURATION` | `30` | Durée d'un chunk audio en secondes |
| `STC_OVERLAP` | `5` | Chevauchement entre chunks consécutifs (en secondes) |

Pour un sous-titrage style "temps réel" (chunks courts) :

```bash
# Linux/Mac
STC_SLOT_DURATION=6 STC_OVERLAP=1 uvicorn main:app --host 0.0.0.0 --port 8000

# Windows PowerShell
$env:STC_SLOT_DURATION="6"; $env:STC_OVERLAP="1"; uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 🌐 Les 3 interfaces

| Page | Pour qui | URL |
|---|---|---|
| `/` | Sous-titreur | http://localhost:8000/ |
| `/admin` | Admin (toi qui orchestres) | http://localhost:8000/admin |
| `/viewer` | Écran de projection / spectateurs | http://localhost:8000/viewer |

---

## 📋 Workflow d'une session

1. **Admin** ouvre `/admin` et **uploade un MP3 / WAV** dans la section "Audio partagé"
2. Les **sous-titreurs** ouvrent `/` → ils voient un overlay "⏳ En attente de l'admin"
3. L'admin voit chaque user apparaître dans la **section Lobby** (jaune) avec un bouton **✓ Confirmer**
4. L'admin clique **✓ Confirmer** sur chaque user → il reçoit son chunk (portion d'audio à transcrire)
5. Chaque sous-titreur :
   - Clique **▶ Écouter ma portion** → l'audio joue uniquement sa portion (auto-pause à la fin)
   - Tape ce qu'il entend dans le textarea
   - Clique **✓ Soumettre et chunk suivant** → marque son chunk comme terminé, reçoit le suivant si dispo
6. L'admin télécharge le résultat via **↓ Télécharger .srt** ou **↓ .txt**
7. Pour recommencer une session : bouton **⟲ Reset session** sur l'admin

---

## 🌍 Rendre l'app accessible aux coéquipiers (ngrok)

Si vous travaillez à distance, exposez le serveur local via ngrok :

```bash
# 1. Installer ngrok
winget install ngrok       # Windows
brew install ngrok          # Mac

# 2. Configurer le token (gratuit sur ngrok.com)
ngrok config add-authtoken <TON_TOKEN>

# 3. Démarrer le tunnel (dans un 2ème terminal, le serveur tourne déjà)
ngrok http http://127.0.0.1:8000
```

Ngrok affiche une URL publique du type `https://xxxxx.ngrok-free.dev`. Partage-la aux coéquipiers : `/`, `/admin`, `/viewer` fonctionnent tous via cette URL.

---

## 🧪 Tests

```bash
# Tests unitaires de l'algorithme de fusion (13 cas)
python fusion.py
```

---

## 🏗 Architecture

```
                 ┌──────────┐    ┌────────────────┐    ┌───────────┐
                 │  Admin   │    │  Sous-titreur  │    │  Viewer   │
                 │  /admin  │    │       /        │    │  /viewer  │
                 └────┬─────┘    └────────┬───────┘    └─────┬─────┘
                      │     WebSocket     │                  │
                      └─────────┬─────────┴──────────────────┘
                                │
                      ┌─────────▼──────────┐
                      │  FastAPI + WS      │
                      │  ┌──────────────┐  │
                      │  │ scheduler.py │  │   ← découpe l'audio en chunks
                      │  │ fusion.py    │  │   ← fusionne avec détection d'overlap
                      │  │ main.py      │  │   ← orchestre tout (lobby, broadcast, export)
                      │  └──────────────┘  │
                      └────────┬───────────┘
                               │
                      ┌────────▼─────────────┐
                      │ static/audio/*.wav   │
                      │ (uploadé par admin)  │
                      └──────────────────────┘
```

### Fichiers clés

| Fichier | Rôle |
|---|---|
| `main.py` | Routes HTTP + WebSocket, lobby/confirm, broadcast, export SRT/TXT |
| `scheduler.py` | Distribution des chunks audio (1 chunk = 1 sous-titreur, single-pass + rotation) |
| `fusion.py` | Algorithme de fusion : overlap suffix↔prefix, fuzzy matching, containment |
| `static/index.html` | Interface sous-titreur (lobby, audio, transcription, soumission) |
| `static/admin.html` | Tableau de bord admin (lobby, chunks, audio, export, reset) |
| `static/viewer.html` | Affichage temps réel style projection TV |

---

## 🧬 Comment marche l'algo de fusion

Quand 2 chunks adjacents se chevauchent, leurs textes ont des mots en commun. L'algo détecte cet overlap et concatène sans doublon :

```
Sub A (chunk N)   : "Le projet est super"
Sub B (chunk N+1) :        "est super et avance bien"
                            └─ overlap k=2 mots ─┘

→ Résultat fusionné : "Le projet est super et avance bien"
```

Avec tolérance fuzzy pour les fautes de frappe (mot ≥ 4 chars, ratio ≥ 0.85), et détection des contributions contenues dans une autre.

---

## ⚙️ Protocole WebSocket (résumé)

### Serveur → Client

| Type | Quand | Contenu |
|---|---|---|
| `pending` | À la connexion | `user_id`, `message` (en attente d'admin) |
| `slot_assigned` | Après confirmation admin | `slot_index`, `audio_start`, `audio_end`, `audio_url` |
| `audio_available` | À l'upload d'un audio | `url`, `filename` |
| `audio_start` / `audio_stop` | Démarrage / arrêt de session | `url`, `started_at` |
| `fused_caption` | À chaque update de texte | `slot_index`, `text` (fusionné), `contributors` |
| `user_joined` / `user_left` | Connexion / déconnexion | `connected_users` |
| `session_reset` | Click "Reset" admin | (vide) |

### Client → Serveur

| Type | Contenu |
|---|---|
| `caption` | `slot_index`, `text` (envoyé après debounce 250ms) |
| `submit` | `slot_index` (chunk terminé, demande le suivant) |

### Endpoints HTTP admin

| Méthode | Route | Effet |
|---|---|---|
| POST | `/audio/upload` | Upload un fichier audio (multipart) |
| POST | `/audio/start` | Diffuse `audio_start` à tous (option démo) |
| POST | `/audio/stop` | Diffuse `audio_stop` |
| POST | `/admin/confirm/{user_id}` | Confirme un user du lobby |
| POST | `/admin/reset` | Reset complet (vide tout, démote les users) |
| GET | `/admin/data` | État JSON complet (rafraîchi 1×/s côté admin) |
| GET | `/export.srt` | Télécharge le SRT |
| GET | `/export.txt` | Télécharge le texte continu (fusion inter-chunks) |

---

## 🌿 Branches Git

| Membre | Branche | Contribution |
|---|---|---|
| Dev A | `feature/api` | Squelette WebSocket initial |
| Dev B | `feature/scheduler` | Distribution des slots |
| Dev C | `feature/fusion` | Algorithme de fusion |
| Dev D | `feature/frontend` | (placeholder) |
| Ilyass | **`Ilyass`** | Modèle chunked single-pass, lobby/confirm, audio synchronisé, fusion fuzzy + multi-locuteur, page admin, export SRT/TXT |
