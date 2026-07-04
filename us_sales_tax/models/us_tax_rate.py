from odoo import api, fields, models


class UsTaxRate(models.Model):
    """Combined sales-tax rate by jurisdiction, used by the built-in *local* provider.

    Ships with the full rooftop **California** table (state + every county + every
    incorporated city, from the quarterly CDTFA snapshot) so a California seller gets
    the correct combined rate offline, at zero cost. Merchants can hand-add single-state
    rows for other states (an approximation); rooftop accuracy outside CA and
    always-current rates come from the paid hosted provider.

    Resolution is most-specific-first: city -> county -> state.
    """

    _name = "us.tax.rate"
    _description = "US Sales Tax Rate (local provider, by jurisdiction)"
    _order = "state_code, county, city"

    state_code = fields.Char("State", required=True, size=2, index=True)
    county = fields.Char("County", index=True, help="Blank for a state-level row.")
    city = fields.Char("City", index=True, help="Blank for state/county-level rows.")
    level = fields.Selection(
        [("state", "State"), ("county", "County"), ("city", "City")],
        required=True, default="state", index=True)
    jurisdiction_name = fields.Char("Jurisdiction")
    rate = fields.Float(
        "Combined Rate (%)", required=True, digits=(7, 4),
        help="State + county + district combined rate for this jurisdiction.")
    note = fields.Char()

    _sql_constraints = [
        ("juris_uniq", "unique(state_code, county, city)",
         "There is already a rate for this jurisdiction."),
    ]

    @staticmethod
    def _norm(v):
        return (v or "").strip().upper()

    def _normalize_vals(self, vals):
        if vals.get("state_code"):
            vals["state_code"] = vals["state_code"].strip().upper()
        for k in ("county", "city"):
            if vals.get(k):
                vals[k] = vals[k].strip().upper()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_vals(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._normalize_vals(vals)
        return super().write(vals)

    @api.model
    def get_combined_rate(self, state_code, county=None, city=None):
        """Most-specific-first combined rate (percent): city -> county -> state.

        Returns 0.0 when the state isn't in the bundled table (documented
        under-collection the merchant resolves by adding a row or upgrading to hosted).
        """
        state = self._norm(state_code)
        if not state:
            return 0.0
        city = self._norm(city)
        county = self._norm(county)
        if county.endswith("COUNTY"):
            county = county[:-6].strip()
        domains = []
        if city:
            domains.append([("state_code", "=", state), ("level", "=", "city"), ("city", "=", city)])
        if county:
            domains.append([("state_code", "=", state), ("level", "=", "county"), ("county", "=", county)])
        domains.append([("state_code", "=", state), ("level", "=", "state")])
        for dom in domains:
            rec = self.search(dom, limit=1)
            if rec:
                return rec.rate
        return 0.0

    @api.model
    def get_rate(self, state_code):
        """State-level combined rate (backward-compatible helper)."""
        return self.get_combined_rate(state_code)
