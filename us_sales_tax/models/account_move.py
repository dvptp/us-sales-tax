import logging

from odoo import fields, models

from .sale_order import US_TAX_EXEMPT_REASONS

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    us_tax_exempt_reason = fields.Selection(
        US_TAX_EXEMPT_REASONS, string="US Tax Exemption Reason", copy=False, readonly=True)
    us_tax_exempt_certificate = fields.Char(
        "US Tax Exemption Certificate", copy=False, readonly=True)
    # Idempotency guard: set once the posted move has been pushed to the record-of-tax ledger, so a
    # re-post / re-validate can't double-log it (copy=False so a duplicated invoice re-logs fresh).
    us_tax_ledger_pushed = fields.Boolean(
        "US Tax Logged to Ledger", copy=False, readonly=True, default=False)

    def _us_set_exemption(self, reason, certificate):
        """Stamp (or clear) the exemption reason + cert ref. Called by the engine."""
        self.us_tax_exempt_reason = reason or False
        self.us_tax_exempt_certificate = certificate or False

    def _us_is_direct_customer_invoice(self):
        """A customer invoice/credit note that did NOT originate from a sale order.

        SO-driven invoices already inherited the order's (engine-computed) tax and the order was
        logged to the ledger on confirm — re-applying/re-logging here would double-count. So we only
        own *direct* invoices (manual A/R, wholesale billing, the occasional retail fix)."""
        self.ensure_one()
        if self.move_type not in ("out_invoice", "out_refund"):
            return False
        return not any(self.invoice_line_ids.mapped("sale_line_ids"))

    def _us_apply_taxes(self):
        """Recompute US taxes on direct customer invoices/credit notes (engine owns state + fail
        mode, shared with sale.order). SO-driven invoices are left with their inherited SO tax."""
        engine = self.env["us.tax.engine"]
        for move in self:
            if move._us_is_direct_customer_invoice():
                engine.apply_safe(move)
        return True

    def _us_push_ledger(self):
        """Push each direct invoice's tax record to Midas's ledger + local mirror (once). A credit
        note nets out via signed amounts (handled in the engine). Never blocks posting."""
        engine = self.env["us.tax.engine"]
        for move in self:
            company = move.company_id or self.env.company
            if not company.us_tax_enabled or move.us_tax_ledger_pushed:
                continue
            try:
                engine.push_transaction(move)
                move.us_tax_ledger_pushed = True
            except Exception as e:  # noqa: BLE001 - logging must never break posting
                _logger.exception("US Sales Tax: ledger push failed for %s: %s", move.display_name, e)
        return True

    def action_post(self):
        # Tax LAST on direct invoices, in draft, BEFORE posting — so the posted move + its tax lines
        # are correct. We deliberately do NOT recompute inside the post transaction (that re-entrancy
        # is what crashed the legacy TaxCloud stack); applying on the draft is the safe seam.
        for move in self:
            if move.state == "draft" and move._us_is_direct_customer_invoice():
                self.env["us.tax.engine"].apply_safe(move)
        res = super().action_post()
        # Log the sale to the record-of-tax ledger after it's posted (amounts final; incl. $0 /
        # out-of-nexus / exempt, which must be reported not omitted). Idempotent via the flag.
        for move in self:
            if move._us_is_direct_customer_invoice():
                move._us_push_ledger()
        return res

    def action_recompute_us_tax(self):
        self._us_apply_taxes()
        return True
