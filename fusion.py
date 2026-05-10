"""
Module de fusion des sous-titres collaboratifs (Dev C).

Quand plusieurs sous-titreurs tapent pendant la même fenêtre d'overlap,
ils écrivent souvent les mêmes mots. Ce module détecte ce chevauchement
et produit un texte unique et propre.

Interface imposee par main.py :
    fuse(contributions: list[str]) -> str
"""

from functools import reduce
from itertools import permutations
from difflib import SequenceMatcher


# Seuil de similarite pour tolerer les fautes de frappe (0.0 a 1.0)
FUZZY_THRESHOLD = 0.85


def fuse(contributions: list[str]) -> str:
    """
    Fusionne une liste de contributions en un seul texte propre.

    Les contributions sont fusionnees deux par deux, dans l'ordre, en
    detectant l'overlap (suffixe de A == prefixe de B) au niveau des mots.

    >>> fuse(["Le projet est super", "est super et avance bien"])
    'Le projet est super et avance bien'
    """
    if not contributions:
        return ""

    # On nettoie les chaines vides eventuelles
    cleaned = [c.strip() for c in contributions if c and c.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return _merge_two(cleaned[0], cleaned[1])

    # 3+ contributions : l'ordre d'arrivee n'est pas forcement le bon ordre
    # narratif. On essaie toutes les permutations et on garde la plus courte
    # (= plus d'overlap detecte = moins de redondance). Limite a 5 contribs
    # pour eviter l'explosion combinatoire (5! = 120).
    if len(cleaned) > 5:
        return reduce(_merge_two, cleaned)

    best = None
    for perm in permutations(cleaned):
        merged = reduce(_merge_two, perm)
        if best is None or len(merged) < len(best):
            best = merged
    return best


def _merge_two(a: str, b: str) -> str:
    """Fusionne deux textes en detectant l'overlap au niveau des mots."""
    if not a:
        return b
    if not b:
        return a

    words_a = a.split()
    words_b = b.split()

    # Si une contribution est entierement contenue dans l'autre (cas
    # multi-locuteur ou plusieurs personnes transcrivent le meme passage
    # avec des vues partielles qui se recouvrent), on garde la plus longue.
    if _contained(words_a, words_b):
        return a
    if _contained(words_b, words_a):
        return b

    # On cherche le plus grand k tel que les k derniers mots de A
    # correspondent (de maniere tolerante) aux k premiers mots de B.
    max_k = min(len(words_a), len(words_b))
    for k in range(max_k, 0, -1):
        suffix = words_a[-k:]
        prefix = words_b[:k]
        if _words_match(suffix, prefix):
            return " ".join(words_a + words_b[k:])

    # Aucun overlap trouve : on concatene avec un espace
    return a + " " + b


def _contained(big: list[str], small: list[str]) -> bool:
    """True si `small` apparait comme sous-sequence contigue de `big` (avec fuzzy)."""
    if not small:
        return True
    if len(small) > len(big):
        return False
    for i in range(len(big) - len(small) + 1):
        if all(_similar(big[i + j], small[j]) for j in range(len(small))):
            return True
    return False


def _words_match(suffix: list[str], prefix: list[str]) -> bool:
    """Compare deux sequences de mots avec tolerance aux fautes."""
    if len(suffix) != len(prefix):
        return False
    return all(_similar(s, p) for s, p in zip(suffix, prefix))


def _similar(w1: str, w2: str) -> bool:
    """Deux mots sont consideres egaux si tres similaires (tolerance fautes)."""
    n1 = _normalize(w1)
    n2 = _normalize(w2)
    if n1 == n2:
        return True
    if not n1 or not n2:
        return False
    # Pour les mots tres courts (<= 3 lettres), on exige l'egalite stricte
    if min(len(n1), len(n2)) <= 3:
        return False
    return SequenceMatcher(None, n1, n2).ratio() >= FUZZY_THRESHOLD


def _normalize(word: str) -> str:
    """Mise en minuscules + suppression de la ponctuation aux extremites."""
    return word.lower().strip(".,!?;:'\"()[]")


# ---------------------------------------------------------------------------
# Tests rapides : `python fusion.py`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cases = [
        # (description, contributions, attendu)
        (
            "exemple du cahier des charges",
            ["Le projet est super", "est super et avance bien"],
            "Le projet est super et avance bien",
        ),
        (
            "overlap d'un seul mot",
            ["j'ai mange une pomme", "pomme rouge et juteuse"],
            "j'ai mange une pomme rouge et juteuse",
        ),
        (
            "aucun overlap",
            ["bonjour tout le monde", "comment ca va"],
            "bonjour tout le monde comment ca va",
        ),
        (
            "trois contributions enchainees",
            ["le chat noir", "chat noir dort", "noir dort sur le tapis"],
            "le chat noir dort sur le tapis",
        ),
        (
            "tolerance majuscules / ponctuation",
            ["Bonjour, comment ca va", "Comment ca va aujourd'hui"],
            "Bonjour, comment ca va aujourd'hui",
        ),
        (
            "tolerance petite faute de frappe",
            ["la maison est grande", "grnde et belle"],
            "la maison est grande et belle",
        ),
        (
            "liste vide",
            [],
            "",
        ),
        (
            "une seule contribution",
            ["bonjour"],
            "bonjour",
        ),
        (
            "textes identiques (cas limite)",
            ["bonjour tout le monde", "bonjour tout le monde"],
            "bonjour tout le monde",
        ),
        (
            "contribution courte contenue dans la longue",
            ["le chat noir dort sur le tapis", "noir dort sur"],
            "le chat noir dort sur le tapis",
        ),
        (
            "contribution longue qui englobe la courte",
            ["chat noir", "le chat noir dort sur le tapis"],
            "le chat noir dort sur le tapis",
        ),
        (
            "trois vues partielles parallele (cas multi-locuteur)",
            [
                "Bonjour a tous et bienvenue",
                "tous et bienvenue dans cette presentation",
                "a tous et bienvenue dans",
            ],
            "Bonjour a tous et bienvenue dans cette presentation",
        ),
        (
            "vue interne avec faute de frappe (fuzzy + contained)",
            ["le chat noir dort sur le tapis", "noir dort sur le tappis"],
            "le chat noir dort sur le tapis",
        ),
    ]

    ok = 0
    ko = 0
    for desc, contribs, expected in cases:
        got = fuse(contribs)
        if got == expected:
            print(f"[OK]  {desc}")
            ok += 1
        else:
            print(f"[KO]  {desc}")
            print(f"      attendu  : {expected!r}")
            print(f"      obtenu   : {got!r}")
            ko += 1

    print(f"\nResultat : {ok}/{ok + ko} tests passes")
