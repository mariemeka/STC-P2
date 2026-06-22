"""
STC - Scénario de démonstration de bout en bout.

Simule une vraie session de sous-titrage en relais et fait tourner TOUTE la
chaîne de production du serveur (les mêmes fonctions que main.py utilise) :
  1. des sous-titreurs se relaient sur des slots et "tapent" leur segment
  2. sauvegarde CSV  -> database.save_caption_to_csv
  3. fusion          -> fusion.fuse_session
  4. correction      -> main.correct_text (SymSpell + Grammalecte + élisions)
  5. redistribution  -> formatter.redistribute_words
  6. export          -> formatter.to_srt / to_txt

On simule la FRAPPE (pas le timing réel des WebSocket), mais tout le
traitement côté serveur est le vrai code.
"""
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.models import Caption
from app.core.database import save_caption_to_csv
from app.logic.fusion import fuse_session
from app.engine.formatter import redistribute_words, to_srt, to_txt
from main import correct_text   # la vraie correction ortho du serveur

# ── PARAMÈTRES DE LA DÉMO ────────────────────────────────────────────────
SLOT_DURATION = 5      # temps d'écoute : 1 segment = 5 s  (=> 1 sous-titre = 5 s dans le .srt)
WRITING_TIME = 10      # temps d'écriture : 10 s (chacun a 2 slots pour taper le sien)
PRE_ALERT = 3          # préavis : alerte 3 s avant le tour
NB_POOLS = 1
NB_SOUS_TITREURS = 2   # writing_time / slot = 10/5 = 2 -> 2 sous-titreurs suffisent
VITESSE_PAROLE = 2.0   # mots/seconde dans l'audio source

# Transcript de référence (ce qui est "dit" dans l'audio). On glisse volontairement
# des fautes de frappe et des apostrophes manquantes pour montrer la correction.
TRANSCRIPT = (
    "Bonjour à tous et bienvenue dans cette démonstration du sous-titrage "
    "collaboratif en temps réel. Plusieurs sous-titreurs se relaient sur des "
    "segments successifs pour transcrire ce que jentends sans jamais perdre le "
    "fil du discours. Le serveur fusionne ensuite toutes les contributions et "
    "produit automatiquement un fichier de sous-titres prêt à lemploi."
)

# Fautes typiques qu'un sous-titreur pourrait taper sous la pression
FAUTES = {
    "jentends": "jentends",     # élision manquante -> doit devenir "j'entends"
    "lemploi": "lemploi",       # élision manquante -> "l'emploi"
    "bienvenue": "bienvenu",    # faute -> doit être recorrigée
    "fusionne": "fusione",      # faute de frappe
}


def simuler_session():
    session_id = f"demo_{int(time.time())}"
    mots = TRANSCRIPT.split()

    # 1) Répartir les mots sur les slots selon le temps de parole
    #    (slot k = mots prononcés entre k*slot et (k+1)*slot secondes)
    mots_par_slot = max(1, int(SLOT_DURATION * VITESSE_PAROLE))
    slots = [mots[i:i + mots_par_slot] for i in range(0, len(mots), mots_par_slot)]

    print(f"  Transcript : {len(mots)} mots  ->  {len(slots)} slots de ~{mots_par_slot} mots")
    print(f"  Relais     : {NB_SOUS_TITREURS} sous-titreurs (le slot k est tapé par le sous-titreur k % {NB_SOUS_TITREURS})\n")

    # 2) Chaque sous-titreur "tape" les slots dont il est responsable
    for k, mots_du_slot in enumerate(slots):
        owner = k % NB_SOUS_TITREURS
        texte_tape = " ".join(FAUTES.get(m, m) for m in mots_du_slot)
        cap = Caption(
            user_id=f"user{owner}",
            pool_id=1,
            text=texte_tape,
            timestamp=time.time() + k,   # ordre chronologique
            slot_index=k,
        )
        save_caption_to_csv(session_id, cap)
        print(f"  [slot {k:>2} | sous-titreur {owner}] tape : {texte_tape}")

    return session_id, slots


def exporter(session_id):
    # 3) Fusion (le vrai algo : overlap, fuzzy, dédup cross-slot)
    pool_files = fuse_session(session_id, NB_POOLS)

    print("\n" + "=" * 74)
    print("RÉSULTAT FINAL (après fusion + correction + export)")
    print("=" * 74)

    for pool_id, captions in pool_files.items():
        # 4) Correction post-fusion sur le texte complet (comme stop_and_export)
        texte_complet = " ".join(c["text"] for c in captions if c["text"].strip())
        texte_corrige = correct_text(texte_complet, final=True)
        # 5) Redistribution sur les slots (le fix : pas de sous-titre vide)
        captions = redistribute_words(captions, texte_corrige)
        # 6) Génération SRT
        srt = to_srt(captions, SLOT_DURATION)

        print(f"\n--- Pool {pool_id} : {len(captions)} sous-titres ---\n")
        print(srt)

        return texte_corrige


def evaluer(texte_corrige):
    ref = set(w.lower().strip(".,") for w in TRANSCRIPT.split())
    # version corrigée attendue des élisions, pour une mesure indicative
    obt = set(w.lower().strip(".,") for w in texte_corrige.split())
    couverture = round(len(ref & obt) / len(ref) * 100, 1)
    print("=" * 74)
    print("PARAMÈTRES UTILISÉS")
    print("=" * 74)
    print(f"  Slot (temps d'écoute)   : {SLOT_DURATION} s")
    print(f"  Temps d'écriture        : {WRITING_TIME} s")
    print(f"  Préavis                 : {PRE_ALERT} s")
    print(f"  Pools                   : {NB_POOLS}")
    print(f"  Sous-titreurs           : {NB_SOUS_TITREURS}")
    print(f"  Vitesse de parole       : {VITESSE_PAROLE} mots/s")
    print(f"  Couverture (mots gardés): {couverture}%")


if __name__ == "__main__":
    print("\n🎬 SCÉNARIO DE DÉMONSTRATION STC\n" + "-" * 74)
    sid, _ = simuler_session()
    texte = exporter(sid)
    print()
    evaluer(texte)
