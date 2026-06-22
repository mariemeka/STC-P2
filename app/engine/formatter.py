"""Export des slots fusionnés vers SRT / TXT."""
from typing import List


def redistribute_words(captions: List[dict], texte_corrige: str) -> List[dict]:
    """Répartit le texte final corrigé uniformément sur les captions (par nb de
    mots), en conservant le `slot_index` (donc le minutage) de chaque caption.

    Retire les sous-titres devenus vides — cas où il y a moins de mots que de
    slots, qui produisait sinon des blocs SRT vides.
    """
    if not captions:
        return []
    mots = texte_corrige.split()
    mots_par_slot = max(1, len(mots) // len(captions))
    for i, caption in enumerate(captions):
        debut = i * mots_par_slot
        fin = debut + mots_par_slot if i < len(captions) - 1 else len(mots)
        caption["text"] = " ".join(mots[debut:fin])
    return [c for c in captions if c["text"].strip()]


def _srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(slots: List[dict], slot_duration: int) -> str:
    """slots: liste de {slot_index, text, timestamp}.

    Le minutage SRT est calculé à partir de `slot_index * slot_duration`
    (la grille fixe des segments d'écoute), et non à partir de l'horodatage
    réel de frappe : le temps d'écriture peut être plus long que le slot
    (cf. SessionConfig.writing_time), donc l'horodatage de soumission ne
    correspond plus au moment réel du segment dans la vidéo/audio source.
    """
    if not slots:
        return ""
    lines = []
    for i, s in enumerate(slots, start=1):
        idx = s.get("slot_index", i - 1)
        start = idx * slot_duration
        end = start + slot_duration
        lines.append(str(i))
        lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        lines.append(s["text"])
        lines.append("")
    return "\n".join(lines)


def to_txt(slots: List[dict]) -> str:
    return "\n".join(s["text"] for s in slots if s.get("text"))
