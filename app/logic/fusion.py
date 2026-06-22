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
from typing import Dict, List, Tuple, Optional
from difflib import SequenceMatcher
from app.core.database import read_session_csvs
from app.core.spellcheck import is_valid_word


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


def _align_to_pivot(pivot_words: List[str], other_words: List[str]) -> List[Optional[str]]:
    """Aligne other_words sur les positions de pivot_words (None si absent à cette position)."""
    aligned: List[Optional[str]] = [None] * len(pivot_words)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, pivot_words, other_words).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                aligned[i1 + k] = pivot_words[i1 + k]
        elif tag == "replace":
            n = min(i2 - i1, j2 - j1)
            for k in range(n):
                aligned[i1 + k] = other_words[j1 + k]
    return aligned


def fuse(contributions: List[str]) -> str:
    """Fusionne par alignement multiple + vote majoritaire (MSA).

    À chaque position, le mot le plus voté gagne. En cas d'égalité,
    le dictionnaire orthographique départage (le mot valide l'emporte).
    """
    contributions = [c for c in contributions if c and c.strip()]
    if not contributions:
        return ""
    if len(contributions) == 1:
        return contributions[0]

    pivot_words = max(contributions, key=len).split()
    alignments = [_align_to_pivot(pivot_words, c.split()) for c in contributions]

    result = []
    for i, pivot_word in enumerate(pivot_words):
        votes: Dict[str, int] = {}
        for aligned in alignments:
            w = aligned[i]
            if w is not None:
                votes[w] = votes.get(w, 0) + 1
        if not votes:
            result.append(pivot_word)
            continue
        top_count = max(votes.values())
        tied = [w for w, c in votes.items() if c == top_count]
        if len(tied) == 1:
            result.append(tied[0])
        else:
            valid = [w for w in tied if is_valid_word(w)]
            result.append(valid[0] if valid else pivot_word)
    return " ".join(result)


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

    # Chercher combien de mots du début de next
    # correspondent à la fin de prev
    max_k = min(len(prev_words), len(next_words))
    k_next = 0

    for k in range(max_k, 0, -1):
        if prev_words[-k:] == next_words[:k]:
            k_next = k
            break

    # Cas mot partiel : dernier mot de prev est un préfixe d'un mot de next
    if k_next == 0 and prev_words and next_words:
        last = prev_words[-1]
        if len(last) >= 2 and next_words[0].startswith(last) and next_words[0] != last:
            k_next = 1

    if k_next == 0:
        return prev_text, next_text

    # On garde prev intact
    # On supprime uniquement le début de next (les k_next mots dupliqués)
    new_first = " ".join(next_words[k_next:])
    if new_first:
        next_lines[0] = new_first
    else:
        next_lines = next_lines[1:]

    return prev_text, "\n".join(next_lines)


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
                prev_text = cleaned[-1]["text"]
                _, new_next_text = _dedupe_pair(prev_text, s["text"])
                if not new_next_text.strip():
                    continue
                s = dict(s, text=new_next_text)
            cleaned.append(s)

        result[pool_id] = cleaned
    return result


def merge_pools_to_timeline(pool_files: Dict[int, List[dict]]) -> List[dict]:
    """Fusionne toutes les équipes en une seule timeline chronologique.

    Avec le modèle "équipes qui se relaient", chaque pool ne couvre que certains
    slot_index (différents tours de rotation) — ils ne sont pas des flux
    indépendants, mais des segments successifs de la MÊME timeline finale.
    """
    merged: List[dict] = []
    for captions in pool_files.values():
        merged.extend(captions)
    merged.sort(key=lambda c: c["slot_index"])

    cleaned: List[dict] = []
    for s in merged:
        if not s["text"].strip():
            continue
        if cleaned:
            prev_text = cleaned[-1]["text"]
            _, new_next_text = _dedupe_pair(prev_text, s["text"])
            if not new_next_text.strip():
                continue
            s = dict(s, text=new_next_text)
        cleaned.append(s)
    return cleaned
