# Image du serveur STC (FastAPI + WebSocket) pour Cloudflare Containers
FROM python:3.12-slim

WORKDIR /app

# Dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pré-télécharge / construit le dictionnaire Grammalecte AU BUILD
# (sinon pygrammalecte tente de l'installer au 1er run -> échec hors-ligne)
RUN python -c "from pygrammalecte import grammalecte_text; list(grammalecte_text('test'))"

# Code de l'application
COPY main.py .
COPY fr-100k.txt .
COPY reference.txt .
COPY app ./app
COPY static ./static

# Port d'écoute (Hugging Face Spaces = 7860 ; fallback si PORT non défini)
EXPOSE 7860

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}
