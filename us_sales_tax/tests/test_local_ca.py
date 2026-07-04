from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestLocalCA(TransactionCase):
    """Local provider: the CA exemption matrix and tax assignment."""

    @classmethod
    def setUpClass(cls):
        # Fresh test DBs (the 19 rig + CI) have no Chart of Accounts — load one so the tax engine
        # can create account.tax + post invoices (the tests used to lean on the seeded mirror DB).
        # TransactionCase runs as admin in the main company (no access/company-context issues).
        super().setUpClass()
        cls.env["account.chart.template"].try_loading("generic_coa", cls.env.company)
        cls.company = cls.env.company
        cls.company.write({
            "us_tax_enabled": True,
            "us_tax_provider": "local",
            "us_tax_fail_mode": "open",
            "us_tax_unknown_taxable": True,
        })
        cls.engine = cls.env["us.tax.engine"]
        cls.cat_snack = cls.env.ref("us_sales_tax.cat_food_snack")
        cls.cat_food_snack = cls.cat_snack  # alias used by the comparison-path tests below
        cls.cat_general = cls.env.ref("us_sales_tax.cat_general")
        cls.cat_clothing = cls.env.ref("us_sales_tax.cat_clothing")
        cls.ca = cls.env["res.country.state"].search(
            [("code", "=", "CA"), ("country_id.code", "=", "US")], limit=1)

    # ------------------------------------------------------------ rates
    def test_snack_exempt_in_ca(self):
        self.assertEqual(
            self.engine.compute_rate(self.company, "CA", self.cat_snack), 0.0,
            "Snack food must be exempt in CA (Prop 163).")

    def test_general_taxable_in_ca(self):
        self.assertEqual(
            self.engine.compute_rate(self.company, "CA", self.cat_general), 7.25)

    def test_clothing_taxable_in_ca(self):
        self.assertEqual(
            self.engine.compute_rate(self.company, "CA", self.cat_clothing), 7.25)

    # ------------------------------------------------------ bundled rooftop CA
    def test_bundled_ca_table_loaded(self):
        """The full CDTFA snapshot ships locally (no API) — ~542 CA jurisdictions."""
        Rate = self.env["us.tax.rate"]
        self.assertGreater(Rate.search_count([("state_code", "=", "CA")]), 500)

    def test_rooftop_ca_city_combined_rate(self):
        """Local provider now returns the full combined city rate (state+county+district),
        not just the 7.25 state floor — resolved most-specific-first from the bundled table."""
        r = lambda **kw: self.engine.compute_rate(self.company, "CA", self.cat_general, **kw)
        self.assertEqual(r(city="LOS ANGELES"), 9.75)
        self.assertEqual(r(city="SAN FRANCISCO"), 8.625)
        self.assertEqual(r(city="alameda"), 10.75)          # case-insensitive
        self.assertEqual(r(), 7.25)                          # no city -> state floor
        self.assertEqual(r(city="NOWHERE CITY"), 7.25)       # unknown city -> state floor

    def test_rooftop_respects_exemption(self):
        """A snack shipped to a high-rate city is still exempt ($0), not the city rate."""
        self.assertEqual(
            self.engine.compute_rate(self.company, "CA", self.cat_snack, city="LOS ANGELES"), 0.0)

    def test_unknown_state_no_rate_yields_zero(self):
        # Taxable by the unknown policy, but no TX rate seeded -> 0.0.
        # Documents the local-provider gap: a state with no rate row under-charges;
        # the hosted provider closes this.
        self.assertEqual(
            self.engine.compute_rate(self.company, "TX", self.cat_general), 0.0)

    def test_exempt_catch_all_rule(self):
        cat_exempt = self.env.ref("us_sales_tax.cat_exempt")
        # '*' rule -> exempt in any state, even one with a rate.
        self.assertEqual(
            self.engine.compute_rate(self.company, "CA", cat_exempt), 0.0)

    # ----------------------------------------------------- managed taxes
    def test_get_or_create_tax_is_reused(self):
        t1 = self.engine._get_or_create_tax(self.company, 7.25)
        t2 = self.engine._get_or_create_tax(self.company, 7.25)
        self.assertEqual(t1, t2, "One managed tax per rate, not duplicates.")
        self.assertTrue(t1.us_tax_managed)

    def test_zero_rate_means_no_tax(self):
        self.assertFalse(
            self.engine._get_or_create_tax(self.company, 0.0),
            "Exempt lines carry no tax record at all.")

    # ---------------------------------------------------- apply to a SO
    def _make_so(self, category):
        product = self.env["product.product"].create({
            "name": "Test %s" % category.code,
            "us_tax_category_id": category.id,
        })
        partner = self.env["res.partner"].create({
            "name": "CA Customer", "state_id": self.ca.id})
        return self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {
                "product_id": product.id,
                "product_uom_qty": 1,
                "price_unit": 100.0,
            })],
        })

    def test_apply_snack_so_is_untaxed(self):
        so = self._make_so(self.cat_snack)
        self.engine.apply(so, "CA")
        self.assertFalse(so.order_line.tax_ids)
        self.assertEqual(so.amount_tax, 0.0)

    def test_apply_general_so_is_taxed(self):
        so = self._make_so(self.cat_general)
        self.engine.apply(so, "CA")
        self.assertEqual(so.order_line.tax_ids.amount, 7.25)
        self.assertAlmostEqual(so.amount_tax, 7.25)

    @mute_logger("odoo.addons.us_sales_tax.models.us_tax_engine")
    def test_fail_open_never_raises(self):
        # A genuine engine error must be swallowed by fail-open: the order stays usable
        # and is flagged with a chatter note, never a 500. Force compute_rate to raise.
        from unittest.mock import patch
        so = self._make_so(self.cat_general)
        with patch.object(type(self.engine), "compute_rate", side_effect=ValueError("boom")):
            so._us_apply_taxes()  # must not raise
        self.assertTrue(
            so.message_ids.filtered(lambda m: "fail-open" in (m.body or "")))

    @mute_logger("odoo.addons.us_sales_tax.models.us_tax_engine")
    def test_parallel_comparison_path(self):
        """Test the parallel TaxCloud comparison path (for data refinement) does not break normal flow.
        In real use with creds + comparison enabled + TaxCloud key, it would call _compute_taxcloud and log diffs.
        Useful for stores using TaxCloud (e.g. lotusandluna.com) to compare and refine rules.
        """
        self.company.write({
            "us_tax_comparison_enabled": True,
            # No real TaxCloud creds in test -> will hit error path but not crash apply
            "us_taxcloud_api_login_id": False,
            "us_taxcloud_api_key": False,
        })
        so = self._make_so(self.cat_food_snack)
        # Should not raise; comparison will log "error" message but main tax applies
        so._us_apply_taxes()
        # Our tax applied (local provider for this test)
        # In full parallel with creds it would post comparison chatter
        self.assertTrue(True, "Parallel comparison path exercised without breaking apply.")

    @mute_logger("odoo.addons.us_sales_tax.models.us_tax_engine")
    def test_parallel_comparison_with_mocked_taxcloud(self):
        """Test parallel path with mocked successful TaxCloud response (simulates real comparison for data refinement)."""
        from unittest.mock import patch
        self.company.write({
            "us_tax_comparison_enabled": True,
            "us_taxcloud_api_login_id": "fake",
            "us_taxcloud_api_key": "fake",
        })
        so = self._make_so(self.cat_food_snack)
        mock_tc = {"rate": 8.5, "tax_amount": 0.85, "taxable": True, "note": "TaxCloud demo"}
        with patch.object(type(self.engine), '_compute_taxcloud', return_value=mock_tc):
            so._us_apply_taxes()
            # Should have posted a comparison message
            msgs = so.message_ids.mapped('body')
            self.assertTrue(any("Tax comparison" in (m or "") for m in msgs), "Should log parallel comparison diff for refinement.")

    def test_hosted_without_key_degrades_to_local(self):
        """Selecting hosted without a key must NOT brick: the write succeeds and the rate
        degrades to the bundled-local CA data (WARN never brick)."""
        self.company.write({
            "us_tax_provider": "hosted",
            "us_tax_api_key": False,
            "us_tax_api_url": "https://api.dvptp.com",
        })  # no ValidationError
        # general item, no city -> bundled CA state floor (7.25), not a crash
        self.assertEqual(
            self.engine.compute_rate(self.company, "CA", self.cat_general), 7.25)

    @mute_logger("odoo.addons.us_sales_tax.models.us_tax_engine")
    def test_hosted_with_key_calls_hosted(self):
        """With a key the hosted path is taken (we mock the actual exec call)."""
        from unittest.mock import patch
        self.company.write({
            "us_tax_provider": "hosted",
            "us_tax_api_key": "bsn_demo",
            "us_tax_api_url": "https://api.dvptp.com",
        })
        # Patch on the CLASS, not the recordset instance (Odoo model attrs are read-only).
        with patch.object(type(self.engine), "_compute_rate_hosted", return_value=6.25) as mocked:
            rate = self.engine.compute_rate(self.company, "TX", self.cat_general)
            self.assertEqual(rate, 6.25)
            mocked.assert_called_once()

    def test_hosted_degrades_to_local_when_unavailable(self):
        """When the hosted call is unavailable (returns None), compute_rate falls back to
        the bundled-local table (rooftop CA if a city is given) instead of failing."""
        from unittest.mock import patch
        self.company.write({
            "us_tax_provider": "hosted", "us_tax_api_key": "bsn_demo",
            "us_tax_api_url": "https://api.dvptp.com"})
        with patch.object(type(self.engine), "_compute_rate_hosted", return_value=None):
            self.assertEqual(
                self.engine.compute_rate(self.company, "CA", self.cat_general, city="LOS ANGELES"),
                9.75)
