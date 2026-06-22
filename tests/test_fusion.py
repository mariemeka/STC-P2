"""Tests de la déduplication d'overlap entre slots (tolérante aux fautes)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.fusion import _dedupe_pair, collapse_repeats


class TestDedupePair(unittest.TestCase):
    def test_overlap_exact_supprime(self):
        """Régression : un overlap exact est bien retiré du début de next."""
        prev, nxt = _dedupe_pair("je vais bien", "bien merci")
        self.assertEqual(prev, "je vais bien")
        self.assertEqual(nxt, "merci")

    def test_overlap_avec_faute_supprime(self):
        """BUG visé : l'overlap répété avec une faute de frappe doit être dédupliqué."""
        # prev finit par "toutes les", next reprend "toutse les" (faute) + la suite
        prev, nxt = _dedupe_pair("le serveur fusionne toutes les",
                                 "toutse les contributions et produit")
        self.assertEqual(nxt, "contributions et produit")

    def test_overlap_accent_faute_supprime(self):
        """'prêt à' repris en 'pêrt à' (faute d'accent) doit aussi être dédupliqué."""
        prev, nxt = _dedupe_pair("un fichier prêt à", "pêrt à l'emploi")
        self.assertEqual(nxt, "l'emploi")

    def test_pas_de_faux_positif(self):
        """Deux segments sans overlap réel ne doivent pas être charcutés."""
        prev, nxt = _dedupe_pair("bonjour le monde", "autre chose ici")
        self.assertEqual(nxt, "autre chose ici")

    def test_mots_courts_exigent_exact(self):
        """Les mots courts (<3) ne doivent pas être fuzzy-matchés (trop risqué)."""
        # "de" vs "le" ne doit PAS être considéré comme un overlap
        prev, nxt = _dedupe_pair("la fin de", "le début ici")
        self.assertEqual(nxt, "le début ici")

    def test_gros_bloc_avec_mot_coupe(self):
        """Cas réel relais : prev finit par un mot coupé, next reprend tout le bloc complet."""
        prev = "le vrai julien etait le fils dun homme tres pa"
        nxt = "julien etait le fils dun homme tres pauvre son pere"
        new_prev, new_next = _dedupe_pair(prev, nxt)
        # le bloc n'apparaît qu'UNE fois après recollage, et "pa" coupé -> "pauvre"
        self.assertEqual(new_prev + " " + new_next,
                         "le vrai julien etait le fils dun homme tres pauvre son pere")

    def test_mot_coupe_seul_pas_de_faux_positif(self):
        """'la' + 'lavande' ne doit PAS fusionner (pas de contexte avant le partiel)."""
        prev, nxt = _dedupe_pair("je vois la", "lavande pousse")
        self.assertEqual(prev, "je vois la")
        self.assertEqual(nxt, "lavande pousse")


class TestCollapseRepeats(unittest.TestCase):
    def test_anchor_repete_garde_les_suites(self):
        words = "il avait beaucoup traca il avait beaucoup voyage il avait beaucoup trace".split()
        self.assertEqual(" ".join(collapse_repeats(words)),
                         "il avait beaucoup traca beaucoup voyage beaucoup trace")

    def test_bloc_double_collapse(self):
        words = "julien etait le fils dun homme julien etait le fils dun homme tres pauvre".split()
        self.assertEqual(" ".join(collapse_repeats(words)),
                         "julien etait le fils dun homme tres pauvre")

    def test_preserve_negation(self):
        # 'navait' != 'avait' -> on ne doit PAS supprimer la négation
        words = "il avait de largent il navait pas de i".split()
        self.assertIn("navait", collapse_repeats(words))

    def test_pas_de_collapse_sans_repetition(self):
        words = "le chat dort sur le tapis".split()
        self.assertEqual(collapse_repeats(words), words)


if __name__ == "__main__":
    unittest.main(verbosity=2)
