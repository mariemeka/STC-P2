"""
STC - Évaluation expérimentale simplifiée
Teste directement l'algorithme de fusion sans passer par le serveur.
"""

import random
import json
import os
from app.logic.fusion import fuse_session
from app.core.models import Caption
from app.core.database import save_caption_to_csv
import time

TEXTE_REFERENCE = (
    "Le sous-titrage collaboratif permet à plusieurs participants "
    "de travailler ensemble en temps réel pour produire des sous-titres "
    "cohérents et complets à partir d'un flux audio en direct "
    "chaque sous-titreur prend en charge une portion du discours "
    "et le système fusionne automatiquement toutes les contributions"
)

VITESSE_PAROLE = 2.5  # mots/seconde

CONFIGURATIONS = [
    {"slot": 20, "overlap": 3,  "nb": 2},
    {"slot": 20, "overlap": 5,  "nb": 2},
    {"slot": 20, "overlap": 5,  "nb": 4},
    {"slot": 15, "overlap": 3,  "nb": 2},
    {"slot": 15, "overlap": 5,  "nb": 4},
]


def taux_couverture(reference, obtenu):
    mots_ref = set(reference.lower().split())
    mots_obt = set(obtenu.lower().split())
    if not mots_ref:
        return 0.0
    return round(len(mots_ref & mots_obt) / len(mots_ref) * 100, 1)

def compter_mots_manques(reference, obtenu):
    return len(set(reference.lower().split()) - set(obtenu.lower().split()))

def compter_doublons(texte):
    mots = texte.lower().split()
    if not mots:
        return 0.0
    doublons = sum(1 for i in range(1, len(mots)) if mots[i] == mots[i-1])
    return round(doublons / len(mots) * 100, 1)

def introduire_faute(mot):
    if len(mot) < 3:
        return mot
    i = random.randint(1, len(mot) - 2)
    return mot[:i] + mot[i+1] + mot[i] + mot[i+2:]

def simuler_sous_titreur(user_id, vitesse_frappe, slot_duration, slot_index, session_id):
    """
    Simule un sous-titreur qui tape pendant slot_duration secondes.
    Retourne les captions qu'il a produites.
    """
    mots = TEXTE_REFERENCE.split()
    temps_par_mot_parole = 1.0 / VITESSE_PAROLE
    temps_par_mot_frappe = 1.0 / vitesse_frappe
    
    texte_courant = ""
    captions = []
    temps_ecoule = 0.0

    for mot in mots:
        temps_ecoule += temps_par_mot_parole

        # Arrêt si slot terminé
        if temps_ecoule > slot_duration:
            break

        # Décrochage si trop lent
        if temps_par_mot_frappe > temps_par_mot_parole:
            ratio = temps_par_mot_frappe / temps_par_mot_parole
            if random.random() < (1 - 1/ratio) * 0.7:
                continue

        # Faute de frappe (5%)
        mot_tape = introduire_faute(mot) if random.random() < 0.05 else mot
        texte_courant += ("" if not texte_courant else " ") + mot_tape

        caption = Caption(
            user_id=user_id,
            pool_id=1,
            text=texte_courant,
            timestamp=time.time(),
            slot_index=slot_index,
        )
        captions.append(caption)
        save_caption_to_csv(session_id, caption)

    return captions

def lancer_experience(config):
    nb = config["nb"]
    slot_dur = config["slot"]
    overlap = config["overlap"]
    session_id = f"eval_{int(time.time())}_{nb}st"

    # Nettoyer les anciens fichiers
    os.makedirs("data", exist_ok=True)

    # Simuler chaque sous-titreur sur son slot
    vitesses = [random.uniform(0.8, 1.8) for _ in range(nb)]
    
    for i in range(nb):
        simuler_sous_titreur(
            user_id=f"user_{i}",
            vitesse_frappe=vitesses[i],
            slot_duration=slot_dur,
            slot_index=i,
            session_id=session_id,
        )

    # Fusionner les contributions
    pool_files = fuse_session(session_id, num_pools=1)
    
    # Récupérer le texte fusionné
    texte_final = ""
    if 1 in pool_files and pool_files[1]:
        texte_final = " ".join(c["text"] for c in pool_files[1])
        
    return {
        "config": config,
        "texte_fusionne": texte_final,
        "couverture": taux_couverture(TEXTE_REFERENCE, texte_final),
        "doublons": compter_doublons(texte_final),
        "mots_manques": compter_mots_manques(TEXTE_REFERENCE, texte_final),
        "mots_total": len(TEXTE_REFERENCE.split()),
    }

def afficher_resultats(resultats):
    print("\n" + "="*72)
    print("RÉSULTATS DES ÉVALUATIONS EXPÉRIMENTALES — STC")
    print("="*72)
    print(f"\n{'Configuration':<35} {'ST':>3} {'Couverture':>11} {'Doublons':>9}")
    print("-"*72)

    for r in resultats:
        c = r["config"]
        print(f"slot={c['slot']}s | overlap={c['overlap']}s{'':20} "
              f"{c['nb']:>3} "
              f"{r['couverture']:>10}% "
              f"{r['doublons']:>8}%")

    print("\n" + "="*72)
    print("DÉTAIL PAR EXPÉRIENCE")
    print("="*72)

    for i, r in enumerate(resultats, 1):
        c = r["config"]
        print(f"\n── Expérience {i} ───────────────────────────────────────────")
        print(f"  Config        : slot={c['slot']}s | overlap={c['overlap']}s | {c['nb']} sous-titreurs")
        print(f"  Texte fusionné: {r['texte_fusionne'][:80]}{'...' if len(r['texte_fusionne']) > 80 else ''}")
        print(f"  Couverture    : {r['couverture']}% ({r['mots_total'] - r['mots_manques']}/{r['mots_total']} mots)")
        print(f"  Doublons      : {r['doublons']}%")

    meilleure = max(resultats, key=lambda r: r["couverture"] - r["doublons"])
    c = meilleure["config"]
    print(f"\n{'='*72}")
    print(f"✅ MEILLEURE CONFIG : slot={c['slot']}s | overlap={c['overlap']}s | {c['nb']} sous-titreurs")
    print(f"   Couverture {meilleure['couverture']}% | Doublons {meilleure['doublons']}%")
    print("="*72)

    # Sauvegarde JSON
    data = [{
        "config": f"slot={r['config']['slot']}s | overlap={r['config']['overlap']}s",
        "slot_duration": r["config"]["slot"],
        "overlap": r["config"]["overlap"],
        "nb_sous_titreurs": r["config"]["nb"],
        "couverture": r["couverture"],
        "doublons": r["doublons"],
        "mots_manques": r["mots_manques"],
        "mots_total": r["mots_total"],
        "duree": 0,
        "texte_fusionne": r["texte_fusionne"],
    } for r in resultats]

    with open("static/results_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\n💾 Résultats → static/results_data.json")
    print("   Voir sur : http://localhost:8000/results")


if __name__ == "__main__":
    print("🔬 Évaluations expérimentales STC")
    print(f"   Texte référence : {len(TEXTE_REFERENCE.split())} mots\n")

    resultats = []
    for i, config in enumerate(CONFIGURATIONS, 1):
        print(f"⏳ Expérience {i}/{len(CONFIGURATIONS)} : "
              f"slot={config['slot']}s | overlap={config['overlap']}s | {config['nb']} sous-titreurs...")
        r = lancer_experience(config)
        resultats.append(r)
        print(f"   ✅ Couverture: {r['couverture']}% | Doublons: {r['doublons']}%")

    afficher_resultats(resultats)