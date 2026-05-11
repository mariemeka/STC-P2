"""Fusion des contributions d'un même slot/pool.

Stratégie :
 - Pour chaque (slot, user) : on combine les captions successives en gérant
   deux cas :
     * Révision (le nouveau texte commence par l'ancien) → on remplace.
     * Phrase nouvelle → on ajoute sur une nouvelle ligne.
 - Si plusieurs users contribuent au même slot (cas overlap inhabituel) :
   on garde la version la plus longue.
"""
from typing import Dict, List
from app.core.database import read_session_csvs


def fuse(contributions: List[str]) -> str:
    """Fusion entre versions concurrentes d'un même slot : on garde la plus longue."""
    contributions = [c for c in contributions if c and c.strip()]
    if not contributions:
        return ""
    return max(contributions, key=len)


def merge_user_captions(captions: List[dict]) -> str:
    """Combine les captions d'un même user dans un slot.

    Si une caption est le prolongement de la précédente (préfixe) → révision.
    Sinon → nouvelle ligne.
    """
    captions = sorted(captions, key=lambda c: c["timestamp"])
    lines: List[str] = []
    for c in captions:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if lines and text.startswith(lines[-1]):
            # Révision : on remplace la dernière ligne
            lines[-1] = text
        elif lines and lines[-1].startswith(text):
            # Texte raccourci (l'utilisateur a effacé puis renvoyé) → on ignore
            continue
        else:
            lines.append(text)
    return "\n".join(lines)


def fuse_session(session_id: str, num_pools: int) -> Dict[int, List[dict]]:
    """Lit les CSV de la session et renvoie, par pool, la liste fusionnée des slots."""
    result: Dict[int, List[dict]] = {}
    for pool_id in range(1, num_pools + 1):
        rows = read_session_csvs(session_id, pool_id)

        # 1) Regrouper par (slot, user) et fusionner les multi-Entrées de chaque user
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
        result[pool_id] = fused_slots
    return result
