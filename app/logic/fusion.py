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
from typing import Dict, List, Tuple
from difflib import SequenceMatcher
from app.core.database import read_session_csvs


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
    captions = sorted(captions, key=lambda c: c["timestamp"])
    lines: List[str] = []
    for c in captions:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if lines and text == lines[-1]:
            continue
        if lines and text.startswith(lines[-1]):
            lines[-1] = text
        elif lines and lines[-1].startswith(text):
            continue
        elif lines and _is_typo_correction(lines[-1], text):
            lines[-1] = text
        else:
            lines.append(text)
    return "\n".join(lines)


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
    k_prev, k_next = _find_word_overlap(prev_words, next_words)

    if k_prev == 0 and k_next == 0:
        return prev_text, next_text

    # Reconstruit la dernière ligne de prev
    new_last = " ".join(prev_words[:-k_prev]) if k_prev > 0 else prev_last
    if new_last:
        prev_lines[-1] = new_last
    else:
        prev_lines = prev_lines[:-1]

    # Reconstruit la première ligne de next
    new_first = " ".join(next_words[k_next:]) if k_next > 0 else next_first
    if new_first:
        next_lines[0] = new_first
    else:
        next_lines = next_lines[1:]

    return "\n".join(prev_lines), "\n".join(next_lines)


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
            if cleaned:
                new_prev_text, new_next_text = _dedupe_pair(
                    cleaned[-1]["text"], s["text"]
                )
                cleaned[-1] = dict(cleaned[-1], text=new_prev_text)
                if not cleaned[-1]["text"].strip():
                    cleaned.pop()
                if not new_next_text.strip():
                    continue
                s = dict(s, text=new_next_text)
            cleaned.append(s)

        result[pool_id] = cleaned
    return result
