import os
import unicodedata
from symspellpy import SymSpell, Verbosity


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
dict_loaded = False
if os.path.exists("fr-100k.txt"):
    sym.load_dictionary("fr-100k.txt", term_index=0, count_index=1, encoding="utf-8")
    dict_loaded = True

accent_map: dict = {}
if dict_loaded:
    for entry in sym.words.keys():
        stripped = _strip_accents(entry)
        if stripped != entry and stripped not in accent_map:
            accent_map[stripped] = entry


def is_valid_word(word: str) -> bool:
    if not dict_loaded:
        return True
    w = word.lower().strip(".,!?;:\"'")
    if not w:
        return False
    if sym.lookup(w, Verbosity.TOP, max_edit_distance=0):
        return True
    return w in accent_map
