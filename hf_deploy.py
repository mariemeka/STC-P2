"""
Déploie l'app STC sur Hugging Face Spaces (Docker).

Pré-requis : être connecté à Hugging Face
  huggingface-cli login        (coller un token WRITE)
puis :
  python hf_deploy.py
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from huggingface_hub import HfApi, create_repo, get_token

SPACE_NAME = "stc-sous-titrage"

README = """---
title: STC Sous-Titrage Collaboratif
emoji: 🎬
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# STC - Sous-Titrage Collaboratif

Plateforme web temps réel de sous-titrage en relais (FastAPI + WebSocket).

- **Admin** : `/admin`
- **Sous-titreur** : `/`
- **Viewer** : `/viewer`
"""


def main():
    token = get_token()
    if not token:
        print("❌ Pas de token Hugging Face. Lance d'abord :  huggingface-cli login")
        sys.exit(1)

    api = HfApi(token=token)
    user = api.whoami()["name"]
    repo_id = f"{user}/{SPACE_NAME}"
    print(f"Compte HF : {user}")
    print(f"Space     : {repo_id}")

    create_repo(repo_id, repo_type="space", space_sdk="docker",
                exist_ok=True, token=token)

    print("Upload des fichiers...")
    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=".",
        allow_patterns=[
            "Dockerfile", ".dockerignore",
            "main.py", "requirements.txt", "fr-100k.txt",
            "app/**", "static/**",
        ],
        ignore_patterns=["static/audio/*"],
        commit_message="Deploy STC",
    )

    # README avec les métadonnées HF (sdk docker + port)
    api.upload_file(
        path_or_fileobj=README.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="space",
        commit_message="HF Space metadata",
    )

    print("\n✅ Déployé !")
    print(f"   Build & URL : https://huggingface.co/spaces/{repo_id}")
    print(f"   URL directe : https://{user}-{SPACE_NAME}.hf.space")


if __name__ == "__main__":
    main()
