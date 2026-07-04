from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    us_tax_enabled = fields.Boolean(
        related="company_id.us_tax_enabled", readonly=False)
    us_tax_provider = fields.Selection(
        related="company_id.us_tax_provider", readonly=False)
    us_tax_api_url = fields.Char(
        related="company_id.us_tax_api_url", readonly=False)
    us_tax_api_key = fields.Char(
        related="company_id.us_tax_api_key", readonly=False)
    us_tax_default_category_id = fields.Many2one(
        related="company_id.us_tax_default_category_id", readonly=False)
    us_tax_fail_mode = fields.Selection(
        related="company_id.us_tax_fail_mode", readonly=False)
    us_tax_unknown_taxable = fields.Boolean(
        related="company_id.us_tax_unknown_taxable", readonly=False)
    us_tax_nexus_state_ids = fields.Many2many(
        related="company_id.us_tax_nexus_state_ids", readonly=False)

    # TaxCloud comparison fields
    us_taxcloud_api_login_id = fields.Char(
        related="company_id.us_taxcloud_api_login_id", readonly=False)
    us_taxcloud_api_key = fields.Char(
        related="company_id.us_taxcloud_api_key", readonly=False)
    us_tax_comparison_enabled = fields.Boolean(
        related="company_id.us_tax_comparison_enabled", readonly=False)
