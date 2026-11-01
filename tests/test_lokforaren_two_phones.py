"""#225: samma förarkonto på två telefoner. En tjänst är aktiv på en telefon i taget.

Den andra telefonen får frågan om den ska ta över, den första får veta att
tjänsten tagits över och kan ta tillbaka den. Inga dubbla aktiva tjänster och
inga tysta byten. Incheckningar och synlighet följer kontot, inte telefonen.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tmbox_gateway.driver import DEMO_DUTY, DriverError, DriverStore, device_id

import test_lokforaren

PHONE_A = "a" * 32
PHONE_B = "b" * 32


class TwoPhonesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = DriverStore(Path(directory.name) / "trainmeet.db")
        self.addCleanup(self.store.close)
        self.duty = {**DEMO_DUTY, "id": "demo-132"}
        self.other = {**DEMO_DUTY, "id": "duty-7", "number": "7"}

    def test_a_second_phone_is_asked_and_never_switches_silently(self) -> None:
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_A)
        with self.assertRaises(DriverError) as asked:
            self.store.activate("benny", "meet", self.other, device_id=PHONE_B)
        self.assertEqual((409, "active_on_other_device"), (asked.exception.status, asked.exception.code))
        self.assertEqual("132", asked.exception.details["duty_number"])
        # Ingenting ändrades: tjänsten är kvar på telefon A.
        self.assertEqual("demo-132", self.store.active("benny", "meet", PHONE_A)["duty"]["id"])
        self.assertTrue(self.store.active("benny", "meet", PHONE_A)["on_this_phone"])
        self.assertFalse(self.store.active("benny", "meet", PHONE_B)["on_this_phone"])

    def test_taking_over_moves_the_duty_and_the_first_phone_can_take_it_back(self) -> None:
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_A)
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_B, take_over=True)
        self.assertFalse(self.store.active("benny", "meet", PHONE_A)["on_this_phone"])
        self.assertTrue(self.store.active("benny", "meet", PHONE_B)["on_this_phone"])
        self.assertEqual(1, len(self.store.active_drivers("meet")))
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_A, take_over=True)
        self.assertTrue(self.store.active("benny", "meet", PHONE_A)["on_this_phone"])

    def test_only_the_phone_with_the_duty_can_end_it(self) -> None:
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_A)
        with self.assertRaises(DriverError) as refused:
            self.store.end("benny", "meet", PHONE_B)
        self.assertEqual("active_on_other_device", refused.exception.code)
        self.assertIsNotNone(self.store.active("benny", "meet"))
        self.store.end("benny", "meet", PHONE_A)
        self.assertIsNone(self.store.active("benny", "meet"))

    def test_the_same_phone_changes_duty_without_a_question(self) -> None:
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_A)
        self.store.activate("benny", "meet", self.other, device_id=PHONE_A)
        self.assertEqual("duty-7", self.store.active("benny", "meet", PHONE_A)["duty"]["id"])

    def test_an_older_app_without_a_phone_id_works_as_before(self) -> None:
        self.store.activate("benny", "meet", self.duty)
        self.store.activate("benny", "meet", self.other, device_id=PHONE_B)
        self.assertTrue(self.store.active("benny", "meet")["on_this_phone"])
        self.store.activate("benny", "meet", self.duty)
        self.store.end("benny", "meet")
        self.assertIsNone(self.store.active("benny", "meet"))

    def test_other_accounts_and_meets_are_their_own(self) -> None:
        self.store.activate("benny", "meet", self.duty, device_id=PHONE_A)
        self.store.activate("maja", "meet", self.duty, device_id=PHONE_B)
        self.store.activate("benny", "other-meet", self.duty, device_id=PHONE_B)
        self.assertEqual(2, len(self.store.active_drivers("meet")))

    def test_the_phone_id_is_checked(self) -> None:
        self.assertEqual(PHONE_A, device_id(PHONE_A.upper()))
        for value in (None, "", "kort", "z" * 32, "a" * 65, "a" * 31 + "/"):
            self.assertIsNone(device_id(value), value)


class TwoPhonesHTTPTests(test_lokforaren.LokforarenHTTPTests):
    """Samma prov genom HTTP, med telefonens id i X-Lokforaren-Device."""

    def phone(self, device: str) -> dict:
        return {"X-Lokforaren-Device": device}

    def test_two_phones_on_one_account(self) -> None:
        benny = self.login("benny")
        a, b = self.phone(PHONE_A), self.phone(PHONE_B)
        self.assertEqual(200, self.request("/v1/driver/active", {"duty_id": "demo-132"}, cookie=benny, headers=a)[0])

        # Telefon B väljer en annan tjänst: frågan, inget byte.
        status, payload, _ = self.request("/v1/driver/active", {"duty_id": "duty-7"}, cookie=benny, headers=b)
        self.assertEqual((409, "active_on_other_device", "132"), (status, payload["error"], payload["duty_number"]))
        self.assertIn("Tjänst 132 är aktiv på en annan telefon", payload["message"])
        clock_a = self.request("/v1/driver/clock", cookie=benny, headers=a)[1]
        self.assertEqual(("demo-132", True), (clock_a["active"]["duty_id"], clock_a["active"]["on_this_phone"]))

        # B tar över: A får veta det i klockans svar, sessionen säger samma sak.
        taken = self.request("/v1/driver/active", {"duty_id": "duty-7", "take_over": True}, cookie=benny, headers=b)[1]
        self.assertEqual("7", taken["active"]["duty"]["number"])
        clock_a = self.request("/v1/driver/clock", cookie=benny, headers=a)[1]
        self.assertEqual(("duty-7", "7", False), (clock_a["active"]["duty_id"], clock_a["active"]["number"],
                                                  clock_a["active"]["on_this_phone"]))
        self.assertFalse(self.request("/v1/driver/session", cookie=benny, headers=a)[1]["active"]["on_this_phone"])
        self.assertTrue(self.request("/v1/driver/session", cookie=benny, headers=b)[1]["active"]["on_this_phone"])

        # A kan inte avsluta B:s tjänst, men checka in (kontot) och ta tillbaka.
        status, payload, _ = self.request("/v1/driver/active/end", {}, cookie=benny, headers=a)
        self.assertEqual((409, "active_on_other_device"), (status, payload["error"]))
        checkin = self.request("/v1/driver/checkins", {"station": "Tveiten"}, cookie=benny, headers=a)
        self.assertEqual(200, checkin[0])
        back = self.request("/v1/driver/active", {"duty_id": "demo-132", "take_over": True}, cookie=benny, headers=a)
        self.assertEqual(200, back[0])
        self.assertFalse(self.request("/v1/driver/clock", cookie=benny, headers=b)[1]["active"]["on_this_phone"])
        self.assertEqual(200, self.request("/v1/driver/active/end", {}, cookie=benny, headers=a)[0])
        self.assertIsNone(self.request("/v1/driver/clock", cookie=benny, headers=b)[1]["active"])

    def test_a_request_without_a_phone_id_is_served_as_before(self) -> None:
        benny = self.login("benny")
        self.assertEqual(200, self.request("/v1/driver/active", {"duty_id": "demo-132"}, cookie=benny,
                                           headers=self.phone(PHONE_A))[0])
        clock = self.request("/v1/driver/clock", cookie=benny)[1]
        self.assertTrue(clock["active"]["on_this_phone"])
        self.assertEqual(200, self.request("/v1/driver/active", {"duty_id": "duty-7"}, cookie=benny)[0])


# Ärvda prov körs redan i test_lokforaren; här körs bara de nya.
for name in [name for name in dir(test_lokforaren.LokforarenHTTPTests) if name.startswith("test_")]:
    if name not in TwoPhonesHTTPTests.__dict__:
        setattr(TwoPhonesHTTPTests, name, None)

if __name__ == "__main__":
    unittest.main()
