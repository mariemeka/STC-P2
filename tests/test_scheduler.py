"""Tests de la logique de scheduler (adaptation de la vitesse de frappe)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.scheduler import Scheduler


class TestUpdateTypingSpeed(unittest.TestCase):
    def _scheduler_avec_user(self, writing_time=24):
        sch = Scheduler()
        sch.config.writing_time = writing_time
        sch.add_user("u1", "Alice")
        return sch

    def test_premier_update_signale_un_changement(self):
        sch = self._scheduler_avec_user()
        # writing_time_personal part de 0 -> passe a 24 (normal) => changement
        self.assertTrue(sch.update_typing_speed("u1", 1.0))
        self.assertEqual(sch.users["u1"].writing_time_personal, 24)

    def test_meme_palier_aucun_changement(self):
        sch = self._scheduler_avec_user()
        sch.update_typing_speed("u1", 1.0)              # 1er => change
        self.assertFalse(sch.update_typing_speed("u1", 1.1))  # toujours normal => pas de change

    def test_changement_de_palier_signale(self):
        sch = self._scheduler_avec_user()
        sch.update_typing_speed("u1", 1.0)             # normal -> 24
        self.assertTrue(sch.update_typing_speed("u1", 2.0))   # rapide -> 29
        self.assertEqual(sch.users["u1"].writing_time_personal, 29)

    def test_lent_reduit_le_temps(self):
        sch = self._scheduler_avec_user(writing_time=24)
        sch.update_typing_speed("u1", 1.0)             # normal -> 24
        self.assertTrue(sch.update_typing_speed("u1", 0.5))   # lent -> 19
        self.assertEqual(sch.users["u1"].writing_time_personal, 19)

    def test_admin_ne_change_rien(self):
        sch = self._scheduler_avec_user()
        sch.add_user("admin_master", "ADMIN")
        self.assertFalse(sch.update_typing_speed("admin_master", 2.0))


class TestTempsEcritureFige(unittest.TestCase):
    """Bug #3 : l'adaptation ne doit pas changer la fin du tour EN COURS."""

    def _session(self, slot_dur, writing_time, elapsed):
        sch = Scheduler()
        sch.add_user("u1", "Alice")   # order 0
        sch.add_user("u2", "Bob")     # order 1  -> n = 2
        sch.config.slot_duration = slot_dur
        sch.config.writing_time = writing_time
        sch.config.is_active = True
        sch.config.start_time = time.time() - elapsed
        return sch

    def test_temps_fige_pendant_le_tour(self):
        # slot=10, writing=20, n=2 -> u1 possède le slot 0, fenêtre [0, 20]
        sch = self._session(slot_dur=10, writing_time=20, elapsed=15)
        st1 = sch.get_current_state("u1")
        self.assertTrue(st1["is_my_turn"])
        self.assertEqual(st1["personal_writing_time"], 20)  # figé à 20

        # L'utilisateur ralentit en plein tour -> sa valeur "future" baisse à 10
        sch.users["u1"].writing_time_personal = 10
        st2 = sch.get_current_state("u1")
        # Le tour EN COURS garde 20 (pas coupé), pas 10
        self.assertTrue(st2["is_my_turn"], "le tour ne doit pas être coupé en plein milieu")
        self.assertEqual(st2["personal_writing_time"], 20)

    def test_owner_a_le_tour_non_owner_a_un_preavis(self):
        # elapsed=5 : slot 0 en cours -> u1 (order 0) joue, u2 (order 1) attend
        sch = self._session(slot_dur=10, writing_time=10, elapsed=5)
        st_u1 = sch.get_current_state("u1")
        st_u2 = sch.get_current_state("u2")
        self.assertTrue(st_u1["is_my_turn"])
        self.assertFalse(st_u2["is_my_turn"])
        self.assertIsNotNone(st_u2["next_turn_in"])  # u2 sait quand vient son tour

    def test_reset_adaptation(self):
        sch = self._session(slot_dur=10, writing_time=20, elapsed=5)
        sch.get_current_state("u1")                 # fige
        sch.users["u1"].writing_time_personal = 30
        sch.reset_adaptation()
        self.assertEqual(sch.users["u1"].writing_time_personal, 0)
        self.assertEqual(sch.users["u1"].frozen_slot, -1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
