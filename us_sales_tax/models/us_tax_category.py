from odoo import api, fields, models


class UsTaxCategory(models.Model):
    """A product taxability class (the open analogue of an Avalara TIC).

    Categories are intentionally coarse and stable. The taxability of a category
    in a given state lives in ``us.tax.rule`` -- the same shape the hosted lookup
    service serves -- so the exemption logic is data, not code.
    """

    _name = "us.tax.category"
    _description = "US Tax Category (product taxability class)"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Stable identifier used by tax rules and the hosted lookup API, "
             "e.g. FOOD_SNACK. Do not rename once rules reference it.",
    )
    description = fields.Text()
    active = fields.Boolean(default=True)
    rule_ids = fields.One2many("us.tax.rule", "category_id", string="Rules")

    _sql_constraints = [
        ("code_uniq", "unique(code)", "Tax category code must be unique."),
    ]

    @api.depends("name", "code")
    def _compute_display_name(self):
        # Odoo 18+ removed `name_get`; the display string is now computed here.
        # Overriding `_compute_display_name` is dual-version safe (works on 17 too).
        for rec in self:
            rec.display_name = "%s (%s)" % (rec.name, rec.code)
