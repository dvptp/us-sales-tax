from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNexusAndCertificates(TransactionCase):
    """Nexus gate (Gap B), county/city contract, buyer resale-certificate exemption, and the
    record-of-tax ledger logging. Local provider (no network) so these run on a scratch DB.

    These prove the compliance backbone: out-of-nexus = $0 *but logged*; resale cert = $0 tagged
    distinctly and logged as gross-with-exempt-deduction (exempt != omitted); expired/wrong-state
    cert = taxed normally.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Fresh 19 DB has no CoA — load one so tax/invoice creation works (admin/main company).
        cls.env["account.chart.template"].try_loading("generic_coa", cls.env.company)
        cls.company = cls.env.company
        cls.company.write({
            "us_tax_enabled": True,
            "us_tax_provider": "local",
            "us_tax_fail_mode": "open",
            "us_tax_unknown_taxable": False,
        })
        cls.engine = cls.env["us.tax.engine"]
        cls.cat_snack = cls.env.ref("us_sales_tax.cat_food_snack")
        cls.cat_general = cls.env.ref("us_sales_tax.cat_general")
        US = cls.env["res.country"].search([("code", "=", "US")], limit=1)
        cls.ca = cls.env["res.country.state"].search(
            [("code", "=", "CA"), ("country_id", "=", US.id)], limit=1)
        cls.tx = cls.env["res.country.state"].search(
            [("code", "=", "TX"), ("country_id", "=", US.id)], limit=1)
        # FAT Chips runs nexus = {CA}.
        cls.company.us_tax_nexus_state_ids = [(6, 0, cls.ca.ids)]

        cls.chips = cls.env["product.product"].create({
            "name": "FAT Chips (Bag)", "list_price": 10.0,
            "us_tax_category_id": cls.cat_snack.id})
        cls.hat = cls.env["product.product"].create({
            "name": "FAT Hat", "list_price": 30.0,
            "us_tax_category_id": cls.cat_general.id})

    def _partner(self, name, state, **kw):
        return self.env["res.partner"].create(dict(
            {"name": name, "state_id": state.id if state else False}, **kw))

    def _order(self, partner, lines):
        return self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {
                "product_id": p.id, "product_uom_qty": q, "price_unit": pu,
            }) for (p, q, pu) in lines],
        })

    def _logs(self, order):
        return self.env["us.tax.transaction.log"].search(
            [("order_ref", "=", order.name)])

    # ----------------------------------------------------------- tax reconcile
    def test_checkout_tax_before_payment_reconciles(self):
        """CA cart: chips (FOOD_SNACK) untaxed, FAT Hat (GENERAL) taxed; total reconciles."""
        partner = self._partner("CA Retail", self.ca)
        order = self._order(partner, [(self.chips, 2, 10.0), (self.hat, 1, 30.0)])
        self.engine.apply_safe(order)

        chips_line = order.order_line.filtered(lambda l: l.product_id == self.chips)
        hat_line = order.order_line.filtered(lambda l: l.product_id == self.hat)
        self.assertFalse(chips_line.tax_ids, "Chips (snack) must be CA-exempt.")
        self.assertEqual(hat_line.tax_ids.amount, 7.25, "Hat is GENERAL/taxable in CA.")
        # 30 * 7.25% = 2.175, rounded to the currency's 2 dp -> 2.18.
        self.assertEqual(round(order.amount_tax, 2), 2.18)
        # The FCS01096 invariant: the charged total == subtotal + shipping + tax (no remainder).
        self.assertAlmostEqual(order.amount_total, order.amount_untaxed + order.amount_tax, places=2)
        self.assertFalse(order.us_tax_exempt_reason)

    # ------------------------------------------------------------- nexus gate
    def test_non_nexus_collects_zero(self):
        """TX ship-to (outside nexus): FAT Hat collects $0 even though GENERAL is taxable."""
        partner = self._partner("TX Retail", self.tx)
        order = self._order(partner, [(self.hat, 1, 30.0)])
        self.engine.apply_safe(order)
        self.assertFalse(order.order_line.tax_ids, "Out-of-nexus must collect $0.")
        self.assertEqual(order.amount_tax, 0.0)
        self.assertAlmostEqual(order.amount_total, order.amount_untaxed, places=2)
        self.assertEqual(order.us_tax_exempt_reason, "no_nexus")

    def test_out_of_state_sale_logged_for_nexus(self):
        """TX sale = $0 tax BUT logged (gross toward TX threshold) -- the gate tracks, not drops."""
        partner = self._partner("TX Retail 2", self.tx)
        order = self._order(partner, [(self.hat, 1, 30.0)])
        self.engine.apply_safe(order)
        order._us_push_ledger()
        logs = self._logs(order)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.state_code, "TX")
        self.assertEqual(logs.exempt_reason, "no_nexus")
        self.assertAlmostEqual(logs.gross, 30.0, places=2)
        self.assertAlmostEqual(logs.exempt, 30.0, places=2)
        self.assertEqual(logs.tax_collected, 0.0)

    # ----------------------------------------------------- resale certificate
    def test_resale_certificate_exempts_and_logs(self):
        """Wholesale buyer with a valid CA resale cert: FAT Hat is $0, tagged resale_certificate,
        and the sale is logged as gross with exempt-by-reason (exempt != omitted)."""
        wholesale = self._partner(
            "Reseller LLC", self.ca,
            is_tax_exempt=True, resale_certificate_number="SR-CH-12345678",
            entity_use_code="RESELLER",
            us_tax_exemption_state_ids=[(6, 0, self.ca.ids)])
        order = self._order(wholesale, [(self.hat, 1, 30.0)])
        self.engine.apply_safe(order)
        self.assertFalse(order.order_line.tax_ids, "Valid resale cert -> $0.")
        self.assertEqual(order.us_tax_exempt_reason, "resale_certificate")
        self.assertEqual(order.us_tax_exempt_certificate, "SR-CH-12345678")

        order._us_push_ledger()
        logs = self._logs(order)
        self.assertEqual(logs.exempt_reason, "resale_certificate")
        self.assertAlmostEqual(logs.gross, 30.0, places=2)
        self.assertAlmostEqual(logs.exempt, 30.0, places=2)
        self.assertEqual(logs.certificate_ref, "SR-CH-12345678")

    def test_expired_certificate_is_taxed(self):
        """An expired cert -> taxed normally (cert validity enforced)."""
        wholesale = self._partner(
            "Expired Reseller", self.ca,
            is_tax_exempt=True, resale_certificate_number="SR-OLD-1",
            us_tax_exemption_state_ids=[(6, 0, self.ca.ids)],
            us_tax_exemption_expiry=date.today() - timedelta(days=1))
        order = self._order(wholesale, [(self.hat, 1, 30.0)])
        self.engine.apply_safe(order)
        self.assertEqual(order.order_line.tax_ids.amount, 7.25, "Expired cert must not exempt.")
        self.assertFalse(order.us_tax_exempt_reason)

    def test_wrong_state_certificate_is_taxed(self):
        """A cert valid only for TX, but shipping to CA -> taxed normally."""
        wholesale = self._partner(
            "TX-only Reseller", self.ca,
            is_tax_exempt=True, resale_certificate_number="TX-1",
            us_tax_exemption_state_ids=[(6, 0, self.tx.ids)])
        order = self._order(wholesale, [(self.hat, 1, 30.0)])
        self.engine.apply_safe(order)
        self.assertEqual(order.order_line.tax_ids.amount, 7.25)
        self.assertFalse(order.us_tax_exempt_reason)

    def test_resale_cert_beats_nexus_and_category(self):
        """Priority: a resale cert zeroes even a mixed cart, tagged resale (not category/no-nexus)."""
        wholesale = self._partner(
            "Reseller Mixed", self.ca, is_tax_exempt=True,
            resale_certificate_number="SR-2", us_tax_exemption_state_ids=[(6, 0, self.ca.ids)])
        order = self._order(wholesale, [(self.chips, 1, 10.0), (self.hat, 1, 30.0)])
        self.engine.apply_safe(order)
        self.assertFalse(order.order_line.tax_ids)
        self.assertEqual(order.us_tax_exempt_reason, "resale_certificate")

    # ------------------------------------------------- direct invoices (account.move)
    def _invoice(self, partner, lines, move_type="out_invoice"):
        return self.env["account.move"].create({
            "move_type": move_type, "partner_id": partner.id,
            "invoice_line_ids": [(0, 0, {
                "product_id": p.id, "quantity": q, "price_unit": pu,
            }) for (p, q, pu) in lines],
        })

    def _inv_logs(self, move):
        return self.env["us.tax.transaction.log"].search([("order_ref", "=", move.name)])

    def test_direct_invoice_taxed_and_logged(self):
        """A direct (non-SO) CA invoice for a GENERAL item is taxed on post AND logged to the ledger."""
        partner = self._partner("CA Retail Direct", self.ca)
        inv = self._invoice(partner, [(self.hat, 1, 30.0)])
        self.assertTrue(inv._us_is_direct_customer_invoice())
        inv.action_post()
        self.assertGreater(inv.amount_tax, 0.0, "Direct CA GENERAL invoice must be taxed.")
        self.assertTrue(inv.us_tax_ledger_pushed)
        logs = self._inv_logs(inv)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.gross, 30.0)
        self.assertGreater(logs.tax_collected, 0.0)

    def test_direct_invoice_resale_exempt_is_logged(self):
        """A direct invoice to a valid CA resale buyer is $0, tagged resale_certificate, logged as
        gross-with-exempt (exempt != omitted)."""
        buyer = self._partner("Wholesale Direct", self.ca, is_tax_exempt=True,
                              resale_certificate_number="CA-RS-1",
                              us_tax_exemption_state_ids=[(6, 0, self.ca.ids)])
        inv = self._invoice(buyer, [(self.hat, 2, 30.0)])
        inv.action_post()
        self.assertEqual(inv.amount_tax, 0.0)
        self.assertEqual(inv.us_tax_exempt_reason, "resale_certificate")
        logs = self._inv_logs(inv)
        self.assertEqual(logs.gross, 60.0)
        self.assertEqual(logs.exempt, 60.0)
        self.assertEqual(logs.tax_collected, 0.0)

    def test_credit_note_nets_negative_in_ledger(self):
        """A direct credit note posts NEGATIVE gross/tax so a refund nets out the original sale."""
        partner = self._partner("CA Refund", self.ca)
        cn = self._invoice(partner, [(self.hat, 1, 30.0)], move_type="out_refund")
        cn.action_post()
        logs = self._inv_logs(cn)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.gross, -30.0, "Credit note gross must be negative (nets out).")
        self.assertLess(logs.tax_collected, 0.0)

    def test_so_driven_invoice_is_not_direct(self):
        """An invoice created from a confirmed SO is NOT 'direct' — the SO already applied tax and
        logged the sale on confirm, so the invoice must not double-tax/double-log."""
        partner = self._partner("CA SO Cust", self.ca)
        order = self._order(partner, [(self.hat, 1, 30.0)])
        order.action_confirm()
        order._create_invoices()
        inv = order.invoice_ids[:1]
        self.assertFalse(inv._us_is_direct_customer_invoice())
