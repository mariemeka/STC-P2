"""Tests de la déduplication d'overlap entre slots (tolérante aux fautes)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.fusion import _dedupe_pair


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
