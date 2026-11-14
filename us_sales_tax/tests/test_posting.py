from unittest.mock import MagicMock, patch

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestPostingAndKeys(TransactionCase):
    """Where collected tax is booked, which posting routes reach the engine, and who can
    read the provider keys."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["account.chart.template"].try_loading("generic_coa", cls.env.company)
        cls.company = cls.env.company
        cls.ca = cls.env["res.country.state"].search(
            [("code", "=", "CA"), ("country_id.code", "=", "US")], limit=1)
        cls.company.write({
            "us_tax_enabled": True,
            "us_tax_provider": "local",
            "us_tax_fail_mode": "closed",
            "us_tax_unknown_taxable": False,
            "us_tax_comparison_enabled": False,
            "us_tax_nexus_state_ids": [Command.set(cls.ca.ids)],
        })
        # A liability account that exists nowhere else, set on the company's default sales tax.
        cls.payable = cls.env["account.account"].create({
            "name": "Zorblat Tax Payable", "code": "277310", "account_type": "liability_current"})
        default_tax = cls.company.account_sale_tax_id
        default_tax.repartition_line_ids.filtered(
            lambda r: r.repartition_type == "tax").account_id = cls.payable
        cls.hat = cls.env["product.product"].create({
            "name": "Zorblat Hat", "list_price": 30.0,
            "us_tax_category_id": cls.env.ref("us_sales_tax.cat_general").id})
        cls.buyer = cls.env["res.partner"].create({"name": "Zorblat Buyer", "state_id": cls.ca.id})
        cls.biller = new_test_user(
            cls.env, login="zorblat_biller", groups="account.group_account_invoice,base.group_partner_manager")

    def _invoice(self, move_type="out_invoice", user=None):
        Move = self.env["account.move"].with_user(user) if user else self.env["account.move"]
        return Move.create({
            "move_type": move_type, "partner_id": self.buyer.id,
            "invoice_line_ids": [Command.create(
                {"product_id": self.hat.id, "quantity": 1, "price_unit": 30.0})],
        })

    def _logs(self, move):
        return self.env["us.tax.transaction.log"].search([("order_ref", "=", move.name)])

    # ------------------------------------------------------- tax account
    def test_collected_tax_is_booked_to_the_tax_payable_account(self):
        inv = self._invoice()
        inv.action_post()
        tax_line = inv.line_ids.filtered("tax_line_id")
        self.assertEqual(len(tax_line), 1)
        self.assertAlmostEqual(tax_line.credit, 2.18, places=2)  # 7.25% of 30.00
        self.assertEqual(tax_line.account_id, self.payable)

    def test_a_managed_tax_without_an_account_gets_one_on_reuse(self):
        tax = self.env["account.tax"].create({
            "name": "Zorblat 3.1337", "amount": 3.1337, "amount_type": "percent",
            "type_tax_use": "sale", "us_tax_managed": True})
        tax.repartition_line_ids.account_id = False
        self.assertEqual(self.env["us.tax.engine"]._get_or_create_tax(self.company, 3.1337), tax)
        reps = tax.repartition_line_ids
        self.assertEqual(reps.filtered(lambda r: r.repartition_type == "tax").account_id,
                         self.payable)
        self.assertFalse(reps.filtered(lambda r: r.repartition_type == "base").account_id)

    # ---------------------------------------------------- posting routes
    def test_batch_validation_taxes_and_logs_each_invoice(self):
        invs = self._invoice() | self._invoice()
        self.env["validate.account.move"].create(
            {"move_ids": [Command.set(invs.ids)]}).validate_move()
        for inv in invs:
            self.assertEqual(inv.state, "posted")
            self.assertAlmostEqual(inv.amount_tax, 2.18, places=2)
            self.assertEqual(len(self._logs(inv)), 1)
            self.assertAlmostEqual(self._logs(inv).tax_collected, 2.18, places=2)

    def test_an_invoice_left_for_auto_post_is_not_logged_until_it_posts(self):
        inv = self._invoice()
        inv.invoice_date = inv.date = "2099-01-01"
        Log = self.env["us.tax.transaction.log"]
        before = Log.search_count([])
        inv._post(soft=True)
        self.assertEqual(inv.state, "draft")
        self.assertFalse(inv.us_tax_ledger_pushed)
        self.assertEqual(Log.search_count([]), before)
        # Its date arrives: it posts, and is logged once.
        inv.invoice_date = inv.date = fields.Date.context_today(inv)
        inv._post(soft=False)
        self.assertEqual(inv.state, "posted")
        self.assertAlmostEqual(self._logs(inv).tax_collected, 2.18, places=2)
        self.assertEqual(Log.search_count([]), before + 1)

    def test_a_reversal_keeps_the_tax_of_the_invoice_it_reverses(self):
        inv = self._invoice()
        inv.action_post()
        # The rate moves after the sale; the credit note must still mirror the invoice.
        self.env["us.tax.rate"].search([("state_code", "=", "CA")]).write({"rate": 1.0})
        refund = inv._reverse_moves([{"invoice_date": inv.invoice_date}], cancel=True)
        self.assertEqual(refund.state, "posted")
        self.assertAlmostEqual(refund.amount_tax, 2.18, places=2)
        self.assertAlmostEqual(self._logs(refund).tax_collected, -2.18, places=2)

    def test_a_reversal_of_a_resale_invoice_is_logged_as_resale(self):
        self.buyer.write({
            "is_tax_exempt": True, "resale_certificate_number": "ZORBLAT-CERT-1",
            "us_tax_exemption_state_ids": [Command.set(self.ca.ids)]})
        inv = self._invoice()
        inv.action_post()
        self.assertEqual(self._logs(inv).exempt_reason, "resale_certificate")
        refund = inv._reverse_moves([{"invoice_date": inv.invoice_date}], cancel=True)
        log = self._logs(refund)
        self.assertEqual(log.exempt_reason, "resale_certificate")
        self.assertEqual(refund.us_tax_exempt_certificate, "ZORBLAT-CERT-1")

    def test_a_draft_credit_note_is_taxed_when_it_is_posted(self):
        inv = self._invoice()
        inv.action_post()
        self.env["us.tax.rate"].search([("state_code", "=", "CA")]).write({"rate": 1.0})
        refund = inv._reverse_moves([{"invoice_date": inv.invoice_date}])
        self.assertEqual(refund.state, "draft")
        refund.action_post()
        self.assertAlmostEqual(refund.amount_tax, 0.30, places=2)  # today's 1% of 30.00

    # -------------------------------------------------------------- keys
    @mute_logger("odoo.addons.us_sales_tax.models.res_partner")
    def test_a_non_administrator_can_save_an_exemption_on_the_hosted_engine(self):
        self.company.write({
            "us_tax_provider": "hosted", "us_tax_api_key": "zorblat_key",
            "us_tax_api_url": "https://zorblat.invalid"})
        with patch("requests.post", return_value=MagicMock(status_code=200)) as post:
            self.buyer.with_user(self.biller).write({
                "is_tax_exempt": True, "resale_certificate_number": "ZORBLAT-CERT-2",
                "us_tax_exemption_state_ids": [Command.set(self.ca.ids)]})
        self.assertTrue(post.called)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer zorblat_key")

    def test_provider_keys_are_hidden_from_non_administrators(self):
        self.company.write({
            "us_tax_api_key": "zorblat_key", "us_taxcloud_api_key": "zorblat_tc_key",
            "us_taxcloud_api_login_id": "zorblat_login"})
        company = self.company.with_user(self.biller)
        for name in ("us_tax_api_key", "us_taxcloud_api_key", "us_taxcloud_api_login_id"):
            with self.subTest(field=name), self.assertRaises(AccessError):
                company.read([name])
        self.assertEqual(self.company.read(["us_tax_api_key"])[0]["us_tax_api_key"], "zorblat_key")

    @mute_logger("odoo.addons.us_sales_tax.models.us_tax_engine")
    def test_a_billing_user_can_post_through_the_hosted_engine(self):
        self.company.write({
            "us_tax_provider": "hosted", "us_tax_api_key": "zorblat_key",
            "us_tax_api_url": "https://zorblat.invalid"})
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"result": {"rate": 9.87}}
        inv = self._invoice(user=self.biller)
        with patch("requests.post", return_value=resp) as post:
            inv.action_post()
        self.assertEqual(inv.state, "posted")
        self.assertAlmostEqual(inv.amount_tax, 2.96, places=2)  # 9.87% of 30.00
        urls = [c.args[0] for c in post.call_args_list]
        self.assertEqual(urls, ["https://zorblat.invalid/v1/exec/tax_rate_lookup",
                                "https://zorblat.invalid/v1/transactions"])
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer zorblat_key")
        self.assertTrue(self._logs(inv).sudo().midas_synced)
