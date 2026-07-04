from odoo import api, fields, models


class UsTaxRule(models.Model):
    """Taxability of a category in a state -- the exemption matrix.

    This is the core IP: (category x state) -> taxable?  A ``state_code`` of
    ``*`` is a catch-all default for the category across states. The local
    provider reads these records directly; the hosted provider serves the same
    shape from a maintained, national dataset.
    """

    _name = "us.tax.rule"
    _description = "US Tax Rule (taxability of a category in a state)"
    _order = "state_code, category_id"

    category_id = fields.Many2one(
        "us.tax.category", required=True, ondelete="cascade", index=True,
    )
    state_code = fields.Char(
        "State", required=True, size=2, index=True,
        help="USPS 2-letter state code, e.g. CA. Use '*' for a default that "
             "applies to every state for this category.",
    )
    taxable = fields.Boolean(
        default=True,
        help="If unchecked, this category is exempt (0%%) in this state.",
    )
    note = fields.Char(
        help="Citation / reason, e.g. 'CA Prop 163 (1992): snack foods exempt'.",
    )

    _sql_constraints = [
        ("cat_state_uniq", "unique(category_id, state_code)",
         "There is already a rule for this category in this state."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("state_code"):
                vals["state_code"] = vals["state_code"].strip().upper()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("state_code"):
            vals["state_code"] = vals["state_code"].strip().upper()
        return super().write(vals)

    @api.constrains("state_code")
    def _check_state_code(self):
        for rule in self:
            code = (rule.state_code or "").strip()
            if code != "*" and (len(code) != 2 or not code.isalpha()):
                from odoo.exceptions import ValidationError
                raise ValidationError(
                    "State must be a 2-letter USPS code or '*' (got %r)." % rule.state_code
                )
