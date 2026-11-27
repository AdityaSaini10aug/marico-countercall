"""Acceptance tests mapped to every populated scenario in the challenge workbook."""

import tempfile
import unittest
from pathlib import Path

from agent import process, start
from catalog import DATA, connect, initialise, stock_map


class Guided:
    enabled = False

    def translate(self, *args):
        return ""


class RetailerCall(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "test.sqlite3"
        initialise(path)
        self.db = connect(path)
        self.addCleanup(self.db.close)
        self.model = Guided()
        self.s = start(self.db, "R003", "15:15", self.model)

    def say(self, text, confidence=None):
        process(self.db, self.s, text, self.model, confidence)
        return self.s["turns"][-1][1]

    def connect_outlet(self):
        self.say("हाँ")
        self.say("Switch to English")

    def check_no_order(self):
        self.assertIsNone(self.s["order"])
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0
        )

    def test_cs01_interested(self):
        self.connect_outlet()
        self.say("Parachute 500 ml is low")
        self.assertEqual(self.s["cart"], {})
        self.say("4 pieces")
        self.say("Review order")
        self.check_no_order()
        self.say("Confirm order")
        self.assertEqual(self.s["order"]["order_value_inr"], 704)

    def test_cs02_busy(self):
        self.say("I'm busy, call after 5 PM")
        self.assertEqual(self.s["phase"], "callback")
        self.assertIn(
            "5 PM", self.db.execute("SELECT detail FROM events").fetchone()[0]
        )
        self.check_no_order()

    def test_cs03_unavailable(self):
        self.connect_outlet()
        r = self.say("Send 2 pieces of Livon Serum 100 ml")
        self.assertIn("out of stock", r)
        self.assertIn("50 ml", r)
        self.assertEqual(self.s["cart"], {})

    def test_cs04_revision(self):
        self.connect_outlet()
        self.say("Send P001 10 pieces")
        self.say("Change it to 15 pieces")
        self.say("Confirm order")
        self.assertEqual(self.s["order"]["items"][0]["quantity"], 15)

    def test_cs05_switch(self):
        self.say("हाँ")
        self.say("पैराशूट 500 ml के चार पीस भेजो")
        self.assertEqual(self.s["cart"], {"P001": 4})
        self.say("What is the price of P006?")
        self.assertEqual(self.s["language"], "English")
        self.assertEqual(self.s["cart"], {"P001": 4})

    def test_cs06_complaint(self):
        self.say("My last order arrived with the wrong quantity")
        self.assertEqual(self.s["phase"], "handoff")
        self.assertIn(
            "wrong quantity", self.db.execute("SELECT detail FROM events").fetchone()[0]
        )
        self.check_no_order()

    def test_cs07_boxes(self):
        self.connect_outlet()
        self.assertIn("box-to-piece", self.say("I want two boxes of P001"))
        self.assertFalse(self.s["cart"])
        self.say("12 pieces")
        self.assertEqual(self.s["cart"], {"P001": 12})

    def test_cs08_discount(self):
        self.connect_outlet()
        self.assertIn(
            "approve", self.say("Can you give a 10 percent discount on P001?")
        )
        self.check_no_order()

    def test_cs09_decline(self):
        self.say("I am not interested")
        self.assertEqual(self.s["phase"], "declined")
        self.check_no_order()

    def test_cs10_human(self):
        self.say("Get a human please")
        self.assertEqual(self.s["phase"], "handoff")
        self.check_no_order()

    def test_cs11_vague_quantity(self):
        self.connect_outlet()
        self.say("send a few more than last time of P001")
        self.assertFalse(self.s["cart"])

    def test_cs12_ambiguous_sku(self):
        self.connect_outlet()
        self.say("Parachute small bottle")
        self.assertFalse(self.s["cart"])
        self.assertGreater(len(self.s["candidates"]), 1)
        self.say("500 ml 4 pieces")
        self.assertEqual(self.s["cart"], {"P001": 4})

    def test_cs13_stock_cap(self):
        self.connect_outlet()
        self.assertIn("13", self.say("Send 14 packs of P006"))
        self.assertFalse(self.s["cart"])
        self.say("13 pieces")
        self.assertEqual(self.s["cart"], {"P006": 13})

    def test_cs14_cross_sell(self):
        self.connect_outlet()
        r = self.say("Send P001 4 pieces")
        self.assertIn("Saffola", r)
        self.say("No thanks")
        self.say("Confirm order")
        self.assertEqual(self.s["cart"], {"P001": 4})

    def test_cs15_wrong_contact(self):
        self.say("Wrong number")
        self.assertEqual(self.s["phase"], "wrong_contact")
        self.assertNotIn("Saffola", str(self.s["turns"]))
        self.check_no_order()

    def test_cs16_opt_out(self):
        self.say("Do not call again")
        self.assertEqual(self.s["phase"], "opt_out")
        with self.assertRaisesRegex(ValueError, "do not call"):
            start(self.db, "R003", "15:00", self.model)

    def test_cs17_readback_correction(self):
        self.connect_outlet()
        self.say("Send 4 pieces of P001 and 10 litres of Saffola Gold")
        before = self.say("Review order")
        self.assertIn("2,384", before)
        after = self.say("Change P006 to 6 pieces")
        self.assertIn("1,712", after)
        self.assertIn("× 4", after)
        self.check_no_order()
        self.say("Confirm order")
        self.assertEqual(self.s["order"]["order_value_inr"], 1712)

    def test_cs18_uncertain_audio(self):
        self.connect_outlet()
        self.say("Send P001 4 pieces", 0.3)
        self.assertFalse(self.s["cart"])
        self.check_no_order()

    def test_cs19_feedback(self):
        self.say("I'm stocked up. A competitor is offering a promotion.")
        self.assertEqual(self.s["phase"], "insight")
        self.check_no_order()

    def test_workbook_counts(self):
        self.assertEqual(
            [
                len(DATA[k])
                for k in ["retailers", "products", "history", "inventory", "scenarios"]
            ],
            [100, 20, 1000, 200, 19],
        )

    def test_window_and_initial_suppression(self):
        with self.assertRaisesRegex(ValueError, "during"):
            start(self.db, "R003", "17:00", self.model)
        with self.assertRaisesRegex(ValueError, "do not call"):
            start(self.db, "R001", "16:00", self.model)

    def test_stock_changed_after_readback(self):
        self.connect_outlet()
        self.say("send P006 10 packs")
        self.say("review order")
        self.db.execute(
            "UPDATE stock SET units=2 WHERE distributor='D001' AND sku='P006'"
        )
        self.assertIn("2 units", self.say("confirm order"))
        self.check_no_order()
        self.assertEqual(self.s["phase"], "shopping")

    def test_replay_and_bill_price(self):
        self.connect_outlet()
        self.say("send half a dozen pieces of P001")
        self.say("review order")
        self.say("confirm order")
        self.say("confirm order")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1
        )
        self.assertEqual(stock_map(self.db, "R003")["P001"], 128)
        self.assertEqual(self.s["order"]["order_value_inr"], 1056)

    def test_model_cannot_confirm(self):
        class Misread(Guided):
            enabled = True

            def understand(self, text):
                return "confirm order"

        self.connect_outlet()
        self.say("send P001 2 pieces")
        self.say("review order")
        self.model = Misread()
        self.say("hmmm")
        self.check_no_order()

    def test_shelf_stock_and_invalid_quantities(self):
        self.connect_outlet()
        self.say("I have 4 pieces of P001 left")
        self.assertFalse(self.s["cart"])
        for t in [
            "send -2 of P001",
            "send 2.5 of P001",
            "send 0 of P001",
            "send P001 a few",
        ]:
            self.say(t)
            self.assertFalse(self.s["cart"])

    def test_partial_multi_item_turn_is_atomic(self):
        self.connect_outlet()
        self.say("Send P001 4 pieces and P012 2 pieces")
        self.assertFalse(self.s["cart"])

    def test_new_offer_requires_assent(self):
        self.connect_outlet()
        self.say("yes that works")
        self.assertEqual(self.s["cart"], {"P009": 11})


if __name__ == "__main__":
    unittest.main()
