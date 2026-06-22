"""Fusion des contributions d'un même slot/pool.

Stratégie :
 - Pour chaque (slot, user) : on combine les captions successives en gérant
     * Révision (le nouveau texte commence par l'ancien) → on remplace.
     * Texte raccourci (l'ancien commence par le nouveau) → on ignore.
     * Phrase nouvelle → on ajoute sur une nouvelle ligne.
 - Si plusieurs users contribuent au même slot (cas overlap inhabituel) :
   on garde la version la plus longue.
 - Cross-slot deduplication : si le début du slot N+1 reprend la fin du slot N
   (cas où la frappe a chevauché la frontière de slot), on déduplique au
   niveau mot, avec tolérance si le dernier mot du slot N est un préfixe
   d'un mot du slot N+1.
"""
import unicodedata
from typing import Dict, List, Tuple
from difflib import SequenceMatcher
from app.core.database import read_session_csvs


def _norm(w: str) -> str:
    """Minuscule + suppression des accents (pour comparer 'prêt' et 'pret')."""
    w = unicodedata.normalize("NFD", w.lower())
    return "".join(c for c in w if unicodedata.category(c) != "Mn")


def _damerau(a: str, b: str) -> int:
    """Distance de Damerau-Levenshtein (transposition de 2 lettres adjacentes = 1)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def _word_match(a: str, b: str) -> bool:
    """Deux mots se correspondent pour la déduplication s'ils sont identiques,
    identiques aux accents près, ou très proches (faute de frappe).

    Tolérance proportionnelle à la longueur ; les mots courts (<4) doivent être
    identiques (sinon trop de faux positifs : 'de' vs 'le')."""
    if a == b:
        return True
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    longueur = max(len(na), len(nb))
    if longueur < 4:
        return False
    return _damerau(na, nb) <= (2 if longueur >= 6 else 1)


def _is_typo_correction(prev: str, new: str) -> bool:
    """Détecte si `new` est une correction probable de `prev`.

    Critères :
     - `new` est strictement plus long que `prev`
     - Les `len(prev)` premiers caractères de `new` ressemblent fortement à `prev`
       (ratio de similarité Levenshtein >= 0.75)
    Couvre le cas typique « j'ai tapé une faute, j'ai backspace, j'ai continué ».
    """
    if not prev or not new or len(new) <= len(prev):
        return False
    head = new[:len(prev)]
    if head == prev:
        return False  # Cas déjà couvert par startswith → pas une "correction"
    ratio = SequenceMatcher(None, prev, head).ratio()
    return ratio >= 0.75


def fuse(contributions: List[str]) -> str:
    contributions = [c for c in contributions if c and c.strip()]
    if not contributions:
        return ""
    return max(contributions, key=len)


def merge_user_captions(captions: List[dict]) -> str:
    """
    Pour un même (user, slot), garde uniquement la dernière contribution
    la plus longue — c'est toujours la plus complète car le sous-titreur
    tape progressivement.
    """
    captions = sorted(captions, key=lambda c: c["timestamp"])
    texts = [c.get("text", "").strip() for c in captions if c.get("text", "").strip()]
    if not texts:
        return ""
    # On garde la plus longue (dernière contribution complète)
    return max(texts, key=len)

def _find_word_overlap(prev_words: List[str], next_words: List[str]) -> Tuple[int, int]:
    """Trouve combien de mots de la FIN de prev se retrouvent au DÉBUT de next.

    Retourne (k_prev, k_next) : nombre de mots à retirer de la fin de prev
    et du début de next pour qu'ils s'enchaînent proprement.

    Gère le cas où le DERNIER mot de prev est un préfixe partiel d'un mot
    de next (ex. "e" → "ensuite") : alors on retire le mot partiel de prev
    et on garde le mot complet dans next.
    """
    if not prev_words or not next_words:
        return 0, 0

    max_k = min(len(prev_words), len(next_words))

    # Cas 1 : match exact - les k derniers mots de prev == les k premiers de next
    for k in range(max_k, 0, -1):
        if prev_words[-k:] == next_words[:k]:
            return k, k

    # Cas 2 : les (k-1) derniers mots exacts + le dernier mot de prev est
    # un préfixe strict du k-ième mot de next (mot partiel complété dans next)
    last = prev_words[-1]
    if len(last) >= 1:
        for k in range(max_k, 0, -1):
            if k == 1:
                if next_words[0].startswith(last) and next_words[0] != last:
                    return 1, 0  # retire le partiel de prev, garde next entier
            else:
                if (prev_words[-k:-1] == next_words[:k - 1]
                        and next_words[k - 1].startswith(last)
                        and next_words[k - 1] != last):
                    return k, k - 1

    return 0, 0


def _best_overlap(prev_words: List[str], next_words: List[str]) -> Tuple[int, int]:
    """Trouve le plus grand chevauchement entre la FIN de prev et le DÉBUT de next.

    Retourne (drop_prev, drop_next) :
      - drop_next : nombre de mots dupliqués à retirer du début de next
      - drop_prev : 1 si le dernier mot de prev est un mot COUPÉ (préfixe du mot
        complet de next) qu'il faut retirer (on garde la version complète de next), sinon 0

    Comparaison tolérante aux fautes (_word_match) pour rattraper les overlaps
    mal tapés ("toutes"/"toutse", "prêt"/"pêrt"...).
    """
    max_k = min(len(prev_words), len(next_words))
    for k in range(max_k, 0, -1):
        partial = False
        ok = True
        for i in range(k):
            p, n = prev_words[-k + i], next_words[i]
            if _word_match(p, n):
                continue
            # Dernier mot de l'overlap : prev peut être un mot COUPÉ (préfixe de n).
            # Exigé k>=2 (au moins un mot de contexte avant) pour éviter les faux positifs.
            if i == k - 1 and k >= 2 and len(p) >= 2 and _norm(n).startswith(_norm(p)):
                partial = True
                continue
            ok = False
            break
        if ok:
            return (1, k - 1) if partial else (0, k)
    return 0, 0


def _dedupe_pair(prev_text: str, next_text: str) -> Tuple[str, str]:
    """Retire l'overlap entre prev et next. Retourne (new_prev, new_next)."""
    prev_lines = prev_text.split("\n")
    next_lines = next_text.split("\n")
    if not prev_lines or not next_lines:
        return prev_text, next_text

    prev_last = prev_lines[-1].strip()
    next_first = next_lines[0].strip()
    if not prev_last or not next_first:
        return prev_text, next_text

    prev_words = prev_last.split()
    next_words = next_first.split()

    drop_prev, drop_next = _best_overlap(prev_words, next_words)
    if drop_prev == 0 and drop_next == 0:
        return prev_text, next_text

    if drop_prev:
        prev_lines[-1] = " ".join(prev_words[:-drop_prev])

    new_first = " ".join(next_words[drop_next:])
    if new_first:
        next_lines[0] = new_first
    else:
        next_lines = next_lines[1:]

    return "\n".join(prev_lines), "\n".join(next_lines)


def _blocks_match(a: List[str], b: List[str]) -> bool:
    return len(a) == len(b) and len(a) > 0 and all(_norm(x) == _norm(y) for x, y in zip(a, b))


def collapse_repeats(words: List[str], window: int = 10, min_len: int = 2) -> List[str]:
    """Retire la 2e occurrence d'un groupe de mots déjà vu peu avant (<= window
    mots), en gardant les suites. Typique du relais où plusieurs sous-titreurs
    réécrivent le même bout d'audio ("il avait beaucoup X ... il avait beaucoup Y").

    Comparaison EXACTE (accents/casse ignorés, mais pas les fautes) pour ne JAMAIS
    supprimer un mot réellement différent (ex. la négation "n'avait" vs "avait").
    """
    out: List[str] = []
    for w in words:
        out.append(w)
        n = len(out)
        best_L = 0
        for L in range(min(window, n // 2), min_len - 1, -1):
            tail = out[n - L:]
            for gap in range(0, window + 1):
                b = n - L - gap
                a = b - L
                if a < 0:
                    break
                if _blocks_match(out[a:b], tail):
                    best_L = L
                    break
            if best_L:
                break
        if best_L:
            del out[n - best_L:]
    return out


def fuse_session(session_id: str, num_pools: int) -> Dict[int, List[dict]]:
    result: Dict[int, List[dict]] = {}
    for pool_id in range(1, num_pools + 1):
        rows = read_session_csvs(session_id, pool_id)

        # 1) Regrouper par (slot, user) et fusionner les multi-Entrées
        grouped: Dict[tuple, List[dict]] = {}
        for r in rows:
            grouped.setdefault((r["slot_index"], r["user_id"]), []).append(r)

        per_user: Dict[tuple, dict] = {}
        for key, captions in grouped.items():
            merged_text = merge_user_captions(captions)
            if merged_text:
                per_user[key] = {
                    "slot_index": key[0],
                    "user_id": key[1],
                    "text": merged_text,
                    "timestamp": min(c["timestamp"] for c in captions),
                }

        # 2) Regrouper par slot et fusionner entre users (cas overlap multi-users)
        by_slot: Dict[int, List[dict]] = {}
        for r in per_user.values():
            by_slot.setdefault(r["slot_index"], []).append(r)

        fused_slots = []
        for slot in sorted(by_slot.keys()):
            versions = by_slot[slot]
            texts = [v["text"] for v in versions]
            first_ts = min(v["timestamp"] for v in versions)
            fused_slots.append({
                "slot_index": slot,
                "text": fuse(texts),
                "timestamp": first_ts,
            })

        # 3) Dédup cross-slot
        cleaned: List[dict] = []
        for s in fused_slots:
            if not s["text"].strip():
                continue
            if cleaned:
                new_prev_text, new_next_text = _dedupe_pair(cleaned[-1]["text"], s["text"])
                cleaned[-1]["text"] = new_prev_text
                if not new_next_text.strip():
                    continue
                s = dict(s, text=new_next_text)
            cleaned.append(s)

        result[pool_id] = cleaned
    return result
