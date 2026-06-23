"""Tests du scheduler (relais simplifié : sans pools ni adaptation)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.scheduler import Scheduler


class TestRelais(unittest.TestCase):
    def _session(self, n, slot, writing, elapsed):
        s = Scheduler()
        for i in range(n):
            s.add_user(f"u{i}", f"U{i}")
        s.config.slot_duration = slot
        s.config.writing_time = writing
        s.config.is_active = True
        s.config.start_time = time.time() - elapsed
        return s

    def test_owner_joue_non_owner_attend(self):
        s = self._session(2, 6, 9, elapsed=2)   # slot 0 -> u0
        self.assertTrue(s.get_current_state("u0")["is_my_turn"])
        st1 = s.get_current_state("u1")
        self.assertFalse(st1["is_my_turn"])
        self.assertIsNotNone(st1["next_turn_in"])

    def test_rotation_et_repos(self):
        s = self._session(2, 6, 9, elapsed=0)
        base = time.time()
        s.config.start_time = base - 2    # slot 0 -> u0 actif
        self.assertTrue(s.get_current_state("u0")["is_my_turn"])
        s.config.start_time = base - 10   # u0 au repos (fenêtre [0,9] finie), u1 actif
        self.assertFalse(s.get_current_state("u0")["is_my_turn"])
        self.assertTrue(s.get_current_state("u1")["is_my_turn"])
        s.config.start_time = base - 14   # slot 2 -> u0 reprend
        self.assertTrue(s.get_current_state("u0")["is_my_turn"])

    def test_deux_soustitreurs_passent_la_main(self):
        # writing 10 > slot 8, 2 sous-titreurs : il existe un moment de repos pour u0
        s = self._session(2, 8, 10, elapsed=13)   # u0 [0,10] fini, u1 actif
        self.assertFalse(s.get_current_state("u0")["is_my_turn"])
        self.assertTrue(s.get_current_state("u1")["is_my_turn"])

    def test_temps_complet_si_assez_de_monde(self):
        # 3 sous-titreurs, slot 8, écriture 20 : 20 < 3*8=24 -> on garde bien ~20s
        # (et NON 16 = (n-1)*slot). Repos = 24-20 = 4s.
        s = self._session(3, 8, 20, elapsed=1)    # u0 en plein tour (slot 0)
        st = s.get_current_state("u0")
        self.assertTrue(st["is_my_turn"])
        self.assertGreater(st["time_left"], 16)   # ~19s, pas bridé à 16

    def test_borne_anti_frappe_infinie(self):
        # n=1, writing 24 >> slot 6 -> fenêtre bornée au slot, le tour avance
        s = self._session(1, 6, 24, elapsed=7)
        st = s.get_current_state("u0")
        self.assertTrue(st["is_my_turn"])
        self.assertLessEqual(st["time_left"], 6)
        self.assertEqual(st["my_slot_index"], 1)

    def test_assign_reordonne(self):
        s = Scheduler()
        s.add_user("a", "A")
        s.add_user("b", "B")
        s.add_user("c", "C")
        s.assign_user("c", 0)   # c passe en premier
        self.assertEqual(s.users["c"].order, 0)
        self.assertEqual(sorted(u.order for u in s._subtitlers()), [0, 1, 2])

    def test_inactif_par_defaut(self):
        s = Scheduler()
        self.assertFalse(s.get_current_state()["active"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
