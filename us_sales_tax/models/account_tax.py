from odoo import fields, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    us_tax_managed = fields.Boolean(
        "Managed by US Sales Tax", default=False, copy=False,
        help="Auto-created and maintained by the US Sales Tax engine. "
             "These are matched by rate so the engine reuses one tax per rate "
             "instead of proliferating duplicates -- don't edit by hand.",
    )
