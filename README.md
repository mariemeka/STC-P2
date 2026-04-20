# STC - Sous-Titrage Collaboratif

## Installation (à faire une seule fois)

### 1. Cloner le repo
git clone https://github.com/[votre-repo]/stc.git
cd stc

### 2. Créer un environnement virtuel
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

### 3. Installer les dépendances
pip install -r requirements.txt

## 🚀 Lancer le serveur
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

## 🌐 Accès
- Sous-titreur : http://[TON-IP]:8000/
- Viewer       : http://[TON-IP]:8000/viewer

## 🌿 Branches Git
| Membre | Branche | Fichier |
|--------|---------|---------|
| Dev A       | feature/api      | main.py       |
| Dev B       | feature/scheduler| scheduler.py  |
| Dev C       | feature/fusion   | fusion.py     |
| Dev D       | feature/frontend | static/       |

## 🔄 Workflow Git
# Récupérer les dernières modifications des autres
git fetch origin
git merge origin/feature/api  ← pour récupérer le travail de Dev A

# Travailler sur sa branche
git checkout feature/scheduler  ← exemple pour Dev B
git add .
git commit -m "feat: ..."
git push origin feature/scheduler
