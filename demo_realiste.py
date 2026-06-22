"""
STC - Démonstration RÉALISTE (imparfaite, avec overlap).

Simule de vrais sous-titreurs en relais, avec tout ce qui rend la tâche dure :
  - overlap : le temps d'écriture (10 s) > le slot (5 s), donc on répète souvent
    les derniers mots du sous-titreur précédent (zone de chevauchement)
  - fautes de frappe (lettres inversées)
  - apostrophes oubliées (j'entends -> jentends)
  - frappe progressive (plusieurs sauvegardes partielles par slot)
  - mots perdus de temps en temps (sous-titreur trop lent)

Le but : montrer comment la FUSION (dédup overlap, fuzzy, garde-la-plus-longue)
+ la CORRECTION nettoient ce bazar pour produire un .srt propre.
"""
import sys
import time
import random

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.models import Caption
from app.core.database import save_caption_to_csv
from app.logic.fusion import fuse_session
from app.engine.formatter import redistribute_words, to_srt
from main import correct_text

random.seed(7)   # reproductible pour la démo

# ── PARAMÈTRES ───────────────────────────────────────────────────────────
SLOT_DURATION = 5
WRITING_TIME = 10        # > slot -> overlap de 5 s entre deux sous-titreurs
PRE_ALERT = 3
NB_POOLS = 1
NB_SOUS_TITREURS = 2
VITESSE_PAROLE = 2.0

TRANSCRIPT = (
    "Bonjour à tous et bienvenue dans cette démonstration du sous-titrage "
    "collaboratif en temps réel. Plusieurs sous-titreurs se relaient sur des "
    "segments successifs pour transcrire ce que j'entends sans jamais perdre le "
    "fil du discours. Le serveur fusionne ensuite toutes les contributions et "
    "produit automatiquement un fichier de sous-titres prêt à l'emploi."
)


def faute_de_frappe(mot):
    """Inverse 2 lettres dans ~18% des mots assez longs."""
    if len(mot) < 4 or random.random() > 0.18:
        return mot
    i = random.randint(1, len(mot) - 2)
    l = list(mot)
    l[i], l[i + 1] = l[i + 1], l[i]
    return "".join(l)


def oublie_apostrophe(mot):
    """Oublie l'apostrophe une fois sur deux (j'entends -> jentends)."""
    if "'" in mot and random.random() < 0.5:
        return mot.replace("'", "")
    return mot


def salir(mot):
    return faute_de_frappe(oublie_apostrophe(mot))


def simuler_session():
    session_id = f"demor_{int(time.time())}"
    mots = TRANSCRIPT.split()
    mots_par_slot = max(1, int(SLOT_DURATION * VITESSE_PAROLE))
    slots = [mots[i:i + mots_par_slot] for i in range(0, len(mots), mots_par_slot)]

    print(f"  Audio : {len(mots)} mots -> {len(slots)} slots (~{mots_par_slot} mots/slot)")
    print(f"  Overlap : écriture {WRITING_TIME}s > slot {SLOT_DURATION}s -> chevauchement de {WRITING_TIME - SLOT_DURATION}s\n")
    print("  ── Ce que chaque sous-titreur a RÉELLEMENT tapé (brut) ──")

    for k, mots_du_slot in enumerate(slots):
        owner = k % NB_SOUS_TITREURS

        # 1) overlap : reprend 1 à 2 derniers mots du slot précédent
        tape = list(mots_du_slot)
        if k > 0:
            ov = random.randint(1, 2)
            tape = slots[k - 1][-ov:] + tape

        # 2) mots perdus (trop lent) : ~12%
        tape = [w for w in tape if random.random() > 0.12]

        # 3) fautes + apostrophes oubliées
        tape = [salir(w) for w in tape]

        if not tape:
            continue

        # 4) frappe progressive : 2 sauvegardes partielles + finale
        coupes = sorted(set([max(1, len(tape) // 2), len(tape)]))
        for c in coupes:
            cap = Caption(
                user_id=f"user{owner}",
                pool_id=1,
                text=" ".join(tape[:c]),
                timestamp=time.time() + k * 10 + c,
                slot_index=k,
            )
            save_caption_to_csv(session_id, cap)

        print(f"   [slot {k:>2} | S{owner}] {' '.join(tape)}")

    return session_id


def exporter(session_id):
    pool_files = fuse_session(session_id, NB_POOLS)

    for pool_id, captions in pool_files.items():
        texte_fusionne = " ".join(c["text"] for c in captions if c["text"].strip())
        print("\n  ── Texte FUSIONNÉ (overlaps dédupliqués, doublons retirés) ──")
        print(f"   {texte_fusionne}")

        texte_corrige = correct_text(texte_fusionne, final=True)
        captions = redistribute_words(captions, texte_corrige)
        srt = to_srt(captions, SLOT_DURATION)

        print("\n" + "=" * 74)
        print(f"  .SRT FINAL ({len(captions)} sous-titres) — après correction")
        print("=" * 74)
        print(srt)

        # mesures
        ref = [w.lower().strip(".,") for w in TRANSCRIPT.split()]
        obt = " ".join(c["text"] for c in captions).lower()
        gardes = sum(1 for w in set(ref) if w in obt)
        couverture = round(gardes / len(set(ref)) * 100, 1)
        # doublons consécutifs dans le résultat
        words = obt.split()
        doublons = sum(1 for i in range(1, len(words)) if words[i] == words[i - 1])
        return couverture, doublons


if __name__ == "__main__":
    print("\n🎬 DÉMO RÉALISTE STC (avec overlap + erreurs)\n" + "-" * 74)
    sid = simuler_session()
    couv, doublons = exporter(sid)
    print("\n" + "=" * 74)
    print("PARAMÈTRES & RÉSULTAT")
    print("=" * 74)
    print(f"  Slot={SLOT_DURATION}s | Écriture={WRITING_TIME}s | Préavis={PRE_ALERT}s "
          f"| Pools={NB_POOLS} | Sous-titreurs={NB_SOUS_TITREURS}")
    print(f"  Couverture des mots : {couv}%")
    print(f"  Doublons consécutifs restants : {doublons}")
