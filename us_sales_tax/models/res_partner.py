import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

HOSTED_TIMEOUT = 8

# Avalara-style entity/use codes (subset). RESELLER is the common one for resale certs.
ENTITY_USE_CODES = [
    ("RESELLER", "Reseller / resale"),
    ("GOV", "Government"),
    ("NONPROFIT", "Charitable / non-profit"),
    ("AG", "Agricultural"),
    ("MFG", "Industrial / manufacturing"),
    ("OTHER", "Other (see certificate)"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    # --- Buyer-level resale/reseller exemption (the compliance backbone) -------
    # The cert lives here (operational entry + local mirror) and is synced to Midas (the cloud
    # system-of-record). Midas holds the reseller *business* identity only -- never retail PII.
    is_tax_exempt = fields.Boolean(
        "Tax Exempt (resale/reseller)",
        help="This buyer holds a valid resale/reseller certificate. When the ship-to state matches "
             "one of the certificate states (and it isn't expired), the US Sales Tax engine charges "
             "$0 and tags the sale exemption_reason='resale_certificate' (reported, not omitted).",
    )
    resale_certificate_number = fields.Char("Resale Certificate / Permit #")
    us_tax_exemption_state_ids = fields.Many2many(
        "res.country.state", "us_tax_partner_exemption_state_rel", "partner_id", "state_id",
        string="Certificate States",
        help="States this resale certificate is valid for (where the buyer resells).",
    )
    entity_use_code = fields.Selection(
        ENTITY_USE_CODES, string="Entity/Use Code", default="RESELLER")
    us_tax_exemption_expiry = fields.Date(
        "Certificate Expiry",
        help="Leave empty if the certificate does not expire. After this date the buyer is taxed normally.")
    us_tax_exemption_doc = fields.Binary("Certificate Document")
    us_tax_exemption_doc_name = fields.Char("Certificate Filename")

    @api.onchange("is_tax_exempt")
    def _onchange_is_tax_exempt_warn(self):
        """Self-asserting exemption is allowed, but the merchant is responsible for holding a valid
        certificate on file (Midas records the exemption from this flag; it can't verify the permit).
        Warn whenever the flag is turned on so it's a deliberate act, not an accidental tick."""
        if self.is_tax_exempt:
            return {"warning": {
                "title": _("Confirm resale exemption"),
                "message": _("Please make sure there is a valid Reseller's Exemption for your "
                             "records before exempting this customer. You are responsible for "
                             "retaining the certificate; sales will be recorded as exempt (resale)."),
            }}

    # ----------------------------------------------------------------- validity
    def _us_valid_resale_cert(self, ship_state):
        """Return cert facts {certificate_number, exemption_state, entity_use_code} if this buyer
        holds a VALID resale cert for `ship_state` (exempt flag + state match + not expired), else None.

        Validity is intentionally strict: a cert with no states, or a ship to a non-listed state, or
        an expired cert -> taxed normally. This is the gate the engine uses before zeroing tax.
        """
        self.ensure_one()
        if not self.is_tax_exempt:
            return None
        ship_state = (ship_state or "").strip().upper()
        if not ship_state:
            return None  # no resolved ship state -> never assume exempt
        states = self.us_tax_exemption_state_ids.mapped("code")
        if not states or ship_state not in states:
            return None
        expiry = self.us_tax_exemption_expiry
        if expiry and expiry < fields.Date.context_today(self):
            return None  # expired
        return {
            "certificate_number": self.resale_certificate_number or "",
            "exemption_state": ship_state,
            "entity_use_code": self.entity_use_code or "RESELLER",
        }

    # ----------------------------------------------------- sync cert -> Midas
    _US_CERT_FIELDS = {
        "is_tax_exempt", "resale_certificate_number", "us_tax_exemption_state_ids",
        "entity_use_code", "us_tax_exemption_expiry",
    }

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners.filtered("is_tax_exempt")._sync_exemption_to_midas()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if self._US_CERT_FIELDS & set(vals):
            self._sync_exemption_to_midas()
        return res

    def _sync_exemption_to_midas(self):
        """Upsert each buyer's certificate to Midas (one row per certificate state).

        Pushes status='active' when exempt and status='revoked' when the exemption is turned off —
        so un-checking "Tax Exempt" actually propagates the revocation to the cloud system-of-record
        (previously a `not is_tax_exempt` guard here swallowed it). Best-effort: a network/API
        failure is logged, never blocks the partner save. Only runs on the hosted provider.
        buyer_ref is the OPAQUE partner id -- no retail PII.
        """
        for partner in self:
            company = partner.company_id or self.env.company
            if company.us_tax_provider != "hosted" or not company.us_tax_api_url or not company.us_tax_api_key:
                continue
            # Sync whenever the buyer has cert states on file — exempt -> active, not -> revoked.
            if not partner.us_tax_exemption_state_ids:
                continue
            import requests  # local import: only on the hosted path
            base = company.us_tax_api_url.rstrip("/")
            headers = {"Content-Type": "application/json",
                       "Authorization": "Bearer %s" % company.us_tax_api_key}
            for state in partner.us_tax_exemption_state_ids:
                payload = {
                    "buyer_ref": str(partner.id),
                    "exemption_state": state.code,
                    "certificate_number": partner.resale_certificate_number or "",
                    "business_name": partner.commercial_company_name or partner.name or "",
                    "entity_use_code": partner.entity_use_code or "RESELLER",
                    "expiry": partner.us_tax_exemption_expiry.isoformat() if partner.us_tax_exemption_expiry else "",
                    "status": "active" if partner.is_tax_exempt else "revoked",
                }
                try:
                    resp = requests.post(base + "/v1/exemption_certificates",
                                         data=json.dumps(payload), headers=headers, timeout=HOSTED_TIMEOUT)
                    resp.raise_for_status()
                except Exception as e:  # noqa: BLE001 - best-effort sync, never block save
                    _logger.warning("US Sales Tax: cert sync to Midas failed for partner %s/%s: %s",
                                    partner.id, state.code, e)
        return True
