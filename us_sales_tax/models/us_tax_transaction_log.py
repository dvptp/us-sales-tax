from odoo import fields, models

from .sale_order import US_TAX_EXEMPT_REASONS


class UsTaxTransactionLog(models.Model):
    """Local mirror of the per-sale records pushed to Midas's append-only ledger.

    Kept in Odoo for reconciliation + an audit trail of what we reported to the cloud
    system-of-record. Holds the same tax-relevant facts (jurisdiction + amounts + exempt reason +
    cert ref + opaque order ref) -- and, since it lives in Odoo (the PII owner), it can also link
    back to the order. The Midas copy carries NO retail PII.
    """

    _name = "us.tax.transaction.log"
    _description = "US Sales Tax — transaction ledger (local mirror)"
    _order = "create_date desc"

    order_ref = fields.Char("Order Ref", index=True, required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    sale_order_id = fields.Many2one("sale.order", ondelete="set null")
    occurred_at = fields.Datetime("Occurred At")

    state_code = fields.Char("Ship-to State", index=True)
    gross = fields.Float("Gross")
    taxable = fields.Float("Taxable")
    exempt = fields.Float("Exempt")
    tax_collected = fields.Float("Tax Collected")
    # Single source of truth for the reasons (shared with sale.order / account.move) + a sentinel
    # for plain taxable rows, so the two lists can't drift.
    exempt_reason = fields.Selection(
        US_TAX_EXEMPT_REASONS + [("", "None / taxable")],
        string="Exempt Reason", default="")
    certificate_ref = fields.Char("Certificate Ref")

    midas_synced = fields.Boolean("Synced to Midas", default=False)
    midas_error = fields.Char("Midas Sync Error")
