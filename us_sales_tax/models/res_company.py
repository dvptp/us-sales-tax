from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    us_tax_enabled = fields.Boolean(
        "Enable US Sales Tax", default=False,
        help="When enabled, the US Sales Tax engine computes line taxes on "
             "confirmation/invoicing instead of relying on static tax mapping.",
    )
    us_tax_provider = fields.Selection(
        [("local", "Local (built-in)"), ("hosted", "Hosted lookup service")],
        string="Tax Provider", default="local", required=True,
    )
    us_tax_api_url = fields.Char("Hosted API URL", default="https://api.dvptp.com")
    us_tax_api_key = fields.Char(
        "Hosted API Key",
        help="Your PBM API key (bsn_...) for the paid hosted engine: all other states, "
             "always-current rates, and the compliance tier. The module is open source (LGPL) "
             "and the free 'local' provider always works without a key using the bundled "
             "rooftop-California data. If the key is missing or invalid, the hosted path degrades "
             "to the local data rather than blocking checkout.",
    )
    us_tax_default_category_id = fields.Many2one(
        "us.tax.category", string="Default Tax Category",
        help="Used for products that have no US Tax Category set.",
    )
    us_tax_fail_mode = fields.Selection(
        [("open", "Fail open ($0, flag the order)"),
         ("closed", "Fail closed (block the operation)")],
        string="On Engine Error", default="open", required=True,
        help="What to do when the tax engine errors. 'Fail open' never blocks "
             "checkout (recommended) -- the order is flagged for review instead.",
    )
    us_tax_unknown_taxable = fields.Boolean(
        "Treat unknown as taxable", default=True,
        help="When no rule matches a (category, state), assume taxable. Safer "
             "for compliance than silently exempting. NOTE: set this to FALSE alongside a "
             "configured nexus so an unmapped (category, state) INSIDE a nexus state can't "
             "silently over-collect (FAT Chips runs nexus={CA} + unknown_taxable=False).",
    )
    us_tax_nexus_state_ids = fields.Many2many(
        "res.country.state", "us_tax_company_nexus_state_rel", "company_id", "state_id",
        string="Nexus States",
        help="States where this company has tax nexus and must collect. When set, the engine "
             "charges $0 for ship-to states OUTSIDE this list (no out-of-state over-collection) "
             "and tags those sales exemption_reason='no_nexus' (still reported, for nexus "
             "monitoring). Leave EMPTY to disable the nexus gate (collect per the rules everywhere).",
    )

    # TaxCloud integration for parallel comparison (to refine our data vs TaxCloud, e.g. for digital goods)
    us_taxcloud_api_login_id = fields.Char(
        "TaxCloud API Login ID",
        help="For parallel computation and comparison with TaxCloud (e.g. on lotusandluna.com).",
    )
    us_taxcloud_api_key = fields.Char(
        "TaxCloud API Key",
        help="For parallel computation and comparison with TaxCloud.",
    )
    us_tax_comparison_enabled = fields.Boolean(
        "Enable TaxCloud Parallel Comparison",
        default=False,
        help="Run our US Sales Tax calculation in parallel with TaxCloud on the same orders/lines. "
             "Differences are logged (as chatter messages) for data refinement (e.g. digital goods rules across states). "
             "Does not affect tax applied unless you switch provider.",
    )

    # No hosted-requires-key constraint: selecting 'hosted' without a valid key now
    # degrades gracefully to the bundled-local data (WARN never brick) instead of blocking.
