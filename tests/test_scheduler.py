"""Tests de la logique de scheduler (adaptation de la vitesse de frappe)."""
import os
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
