from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    us_tax_category_id = fields.Many2one(
        "us.tax.category",
        string="US Tax Category",
        help="Taxability class used by US Sales Tax. If empty, the company's "
             "default tax category is used.",
    )

    def _get_us_tax_category(self):
        """Resolve the effective taxability category for this product."""
        self.ensure_one()
        return self.us_tax_category_id or self.env.company.us_tax_default_category_id
