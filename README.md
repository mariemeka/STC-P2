# STC - Sous-Titrage Collaboratif

Plateforme web temps réel pour le sous-titrage en relais.
Plusieurs sous-titreurs se relaient sur des "slots" (durée fixe), avec une zone d'overlap entre deux slots successifs. Le serveur fusionne automatiquement les contributions et exporte un fichier SRT standard à la fin de la session.

---

## 🚀 Installation rapide

### 1. Cloner / récupérer la branche

```bash
git clone https://github.com/mariemeka/STC-P2.git
cd STC-P2
git checkout IlyassV2
```

### 2. Créer un environnement virtuel

```bash
python -m venv venv
```

Activer l'environnement :

```bash
# Windows (PowerShell ou CMD)
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate
```

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

---

## ▶️ Lancer le serveur

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Le serveur écoute sur `http://<TON-IP>:8000`.

---

## 🌐 Accès aux écrans

| Rôle | URL | Description |
|------|-----|-------------|
| **Admin** | `http://<IP>:8000/admin` | Configurer et piloter la session (start/pause/stop) |
| **Sous-titreur** | `http://<IP>:8000/` | Saisir les sous-titres pendant son tour |
| **Viewer** | `http://<IP>:8000/viewer` | Écran d'affichage public des sous-titres en direct |

> Sur la même machine, remplace `<IP>` par `127.0.0.1`. Pour tester avec d'autres personnes sur le même réseau, utilise ton IP locale (ex. `192.168.1.42`).

---

## 🧪 Scénario de test rapide

1. Ouvrir **`/admin`** dans un onglet.
2. Ouvrir **2 ou 3 onglets** sur **`/`** (un par sous-titreur, mettre des noms différents).
3. Ouvrir **`/viewer`** dans un autre onglet pour voir le rendu live.
4. Sur l'admin : régler `slot=30`, `overlap=5`, `pools=1`, cliquer **Démarrer**.
5. Le 1er sous-titreur reçoit le tour (badge vert "C'EST VOTRE TOUR" + beep + flash). Il tape, puis **Entrée** pour envoyer.
6. À la fin de chaque slot, le suivant prend le relais. Pendant l'overlap (5 dernières secondes), les deux sont actifs en parallèle.
7. Cliquer **Arrêter** sur l'admin → liens `.srt` et `.txt` apparaissent, cliquables.

> **Astuce** : ouvrir tous les onglets sous-titreurs dans le **même navigateur** fonctionne — chaque onglet a sa propre identité (sessionStorage).

---

## 🎛️ Paramètres de session

| Paramètre | Description | Valeur typique |
|-----------|-------------|----------------|
| **Slot** | Durée pendant laquelle un sous-titreur écrit (secondes) | 30 |
| **Overlap** | Pendant les N dernières secondes du slot, le suivant a déjà commencé | 3-5 |
| **Pools** | Groupes de sous-titreurs (1 pool = 1 flux). Plusieurs pools = flux parallèles (ex. plusieurs langues) | 1 |

---

## ✨ Fonctionnalités

- **Rotation automatique** des sous-titreurs avec calcul `is_my_turn` côté serveur (anti-triche).
- **Overlap** entre slots : deux sous-titreurs actifs simultanément pendant la transition, pas de trou.
- **Auto-envoi** en fin de slot si l'utilisateur n'a pas pressé Entrée (rien n'est perdu).
- **Alertes** : beep + flash vert au démarrage du tour, timer rouge clignotant quand `< 3s`.
- **Correcteur français** natif intégré au navigateur (`lang="fr"`).
- **Fusion intelligente** : les frappes successives sont combinées (révision si préfixe, sinon nouvelle ligne).
- **Export SRT** standard à la fin (compatible VLC, YouTube, Premiere, etc.) + TXT brut + CSV pour analyse.
- **Pause / reprise** propre (le temps de pause est exclu du décompte).
- **Réindexation** automatique quand un sous-titreur se déconnecte.

---

## 🏗️ Architecture

```
┌──────────────────┐         ┌──────────────────────────┐         ┌──────────────────┐
│  CLIENTS WEB     │         │  FASTAPI + WEBSOCKET     │         │  STOCKAGE        │
│                  │         │                          │         │                  │
│  /  (sous-tit.)  │  WS  ┌──┤  Scheduler (slots/pools) │         │  data/*.csv      │
│  /admin          │ <───>│  │  Fusion (merge texts)    │  ───>   │  data/*.srt      │
│  /viewer         │      └──┤  Formatter (SRT export)  │         │  data/*.txt      │
└──────────────────┘         └──────────────────────────┘         └──────────────────┘
```

**Stack** : Python 3, FastAPI, WebSocket, Pydantic v2 — frontend HTML/CSS/JS vanilla, pas de framework.

---

## 📁 Structure du projet

```
STC-P2/
├── main.py                     # Backend FastAPI + endpoints WebSocket
├── requirements.txt
├── README.md
├── app/
│   ├── core/
│   │   ├── database.py         # Lecture/écriture CSV
│   │   └── models.py           # Modèles Pydantic (Caption, User, SessionConfig)
│   ├── logic/
│   │   ├── scheduler.py        # Gestion slots, pools, rotation, overlap
│   │   └── fusion.py           # Fusion intelligente des contributions
│   └── engine/
│       └── formatter.py        # Export SRT + TXT
├── static/
│   ├── index.html              # UI sous-titreur
│   ├── admin.html              # UI admin
│   └── viewer.html             # UI viewer
└── data/                       # Fichiers générés (ignorés par git)
```

---

## 🔌 Protocole WebSocket (résumé)

Tous les clients se connectent sur `ws://<host>/ws`.

**Messages client → serveur :**

| Type | Payload | Émis par |
|------|---------|----------|
| `join` | `{user_id?, username}` | Sous-titreur / admin |
| `viewer_join` | `{}` | Viewer |
| `admin_start` | `{slot, overlap, pools}` | Admin |
| `admin_pause` | `{}` | Admin |
| `admin_stop` | `{}` | Admin |
| `caption` | `{text}` | Sous-titreur |
| `get_sync` | `{}` | Tous |

**Messages serveur → client :**

| Type | Contenu |
|------|---------|
| `welcome` | `user_id`, `pool_id`, `order_in_pool`, `state` |
| `viewer_welcome` | `state` |
| `sync_update` | `state` (pushé toutes les secondes) |
| `session_started` / `session_paused` / `session_ended` | `state` ou `files` |
| `new_text` | `pool`, `user`, `text`, `slot_index` |
| `user_update` | `users[]` (pour l'admin) |

---

## 🌿 Branches

| Branche | Auteur | Contenu |
|---------|--------|---------|
| `main` | tous | base partagée |
| `feature/api` | Dev A | backend `main.py` |
| `feature/scheduler` | Dev B | `scheduler.py` |
| `feature/fusion` | Dev C | `fusion.py` |
| `feature/front` | Dev D | `static/` |
| **`IlyassV2`** | Ilyass | refactor complet + overlap + fusion intelligente + auto-envoi + UI simplifiée + export SRT |

---

## 🐛 Dépannage

- **Port 8000 déjà occupé** → changer le port : `uvicorn main:app --port 8001`
- **Plusieurs onglets sous-titreur, un seul actif** → le code utilise `sessionStorage` donc chaque onglet est isolé. Si problème : F12 → Application → Session Storage → vider.
- **Pas de son d'alerte** → certains navigateurs bloquent l'audio avant interaction. Cliquer une fois sur la page débloque le `AudioContext`.
- **Le SRT a des timestamps bizarres** → vérifier que tu as bien arrêté la session via le bouton **Arrêter** de l'admin (sinon les fichiers ne sont pas générés).
