import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

US_TAX_EXEMPT_REASONS = [
    ("resale_certificate", "Resale certificate"),
    ("category_exempt", "Category exempt (e.g. food)"),
    ("no_nexus", "No nexus (out of state)"),
]


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # Exemption stamp set by the engine (distinct reasons: resale cert vs no-nexus vs category).
    us_tax_exempt_reason = fields.Selection(
        US_TAX_EXEMPT_REASONS, string="US Tax Exemption Reason", copy=False, readonly=True)
    us_tax_exempt_certificate = fields.Char(
        "US Tax Exemption Certificate", copy=False, readonly=True,
        help="Resale certificate number applied to this order (when exempt by resale certificate).")

    def _us_set_exemption(self, reason, certificate):
        """Stamp (or clear) the exemption reason + cert ref. Called by the engine."""
        self.us_tax_exempt_reason = reason or False
        self.us_tax_exempt_certificate = certificate or False

    def _us_apply_taxes(self):
        """Fail-safe tax compute on each order (state resolution + fail mode live
        in the engine so they can't drift from the invoice path)."""
        engine = self.env["us.tax.engine"]
        for order in self:
            engine.apply_safe(order)
        return True

    def _us_push_ledger(self):
        """Push each order's tax record to Midas's ledger + local mirror. Never blocks confirm."""
        engine = self.env["us.tax.engine"]
        for order in self:
            company = order.company_id or self.env.company
            if not company.us_tax_enabled:
                continue
            try:
                engine.push_transaction(order)
            except Exception as e:  # noqa: BLE001 - logging must never break confirmation
                _logger.exception("US Sales Tax: ledger push failed for %s: %s", order.display_name, e)
        return True

    def action_confirm(self):
        # Compute taxes BEFORE confirmation so the invoice inherits correct tax (last write).
        self._us_apply_taxes()
        res = super().action_confirm()
        # Log the sale to the record-of-tax ledger AFTER confirm (amounts final; incl out-of-state $0).
        self._us_push_ledger()
        return res

    def action_recompute_us_tax(self):
        """Manual 'Recompute US Tax' action (button / server action)."""
        self._us_apply_taxes()
        return True
