"""Tests de l'export final (redistribution du texte + génération SRT)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.formatter import redistribute_words, to_srt


class TestRedistributeWords(unittest.TestCase):
    def test_cas_normal(self):
        """Le texte corrigé est réparti sur les slots, dans l'ordre."""
        captions = [
            {"slot_index": 0, "text": "x"},
            {"slot_index": 1, "text": "y"},
        ]
        out = redistribute_words(captions, "bonjour tout le monde")
        self.assertEqual(len(out), 2)
        # tous les mots présents, dans l'ordre, sans perte
        recombine = " ".join(c["text"] for c in out)
        self.assertEqual(recombine, "bonjour tout le monde")

    def test_moins_de_mots_que_de_slots_pas_de_vide(self):
        """BUG visé : 3 slots mais 1 seul mot → aucun sous-titre vide."""
        captions = [
            {"slot_index": 0, "text": "a"},
            {"slot_index": 1, "text": "b"},
            {"slot_index": 2, "text": "c"},
        ]
        out = redistribute_words(captions, "salut")
        # Aucun sous-titre vide ne doit subsister
        self.assertTrue(all(c["text"].strip() for c in out))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "salut")

    def test_slot_index_preserve(self):
        """Le minutage (slot_index) de chaque sous-titre conservé est intact."""
        captions = [
            {"slot_index": 5, "text": "x"},
            {"slot_index": 8, "text": "y"},
        ]
        out = redistribute_words(captions, "un deux trois quatre")
        self.assertEqual([c["slot_index"] for c in out], [5, 8])

    def test_aucune_caption(self):
        self.assertEqual(redistribute_words([], "peu importe"), [])

    def test_pas_de_bloc_srt_vide(self):
        """Conséquence : le SRT produit ne contient aucun bloc de texte vide."""
        captions = [
            {"slot_index": 0, "text": "a"},
            {"slot_index": 1, "text": "b"},
            {"slot_index": 2, "text": "c"},
        ]
        out = redistribute_words(captions, "salut")
        srt = to_srt(out, slot_duration=5)
        # le SRT ne doit pas contenir de ligne de texte vide entre deux blocs
        lignes = srt.split("\n")
        # on récupère les lignes de texte (après chaque ligne "-->")
        for i, l in enumerate(lignes):
            if "-->" in l:
                self.assertTrue(lignes[i + 1].strip(), "bloc SRT avec texte vide détecté")


if __name__ == "__main__":
    unittest.main(verbosity=2)
