"""Phase-5 cutover helper — migrate resale certs from TaxCloud's Odoo module into us_sales_tax.

The reseller permit #s are NOT lost in TaxCloud's cloud: the `sale_account_taxcloud_exemption_tc`
module stores them in Odoo on the `taxcloud.exemption` model. This scrapes the ACTIVE certs
(state in {export, draft}; 'cancel' rows are superseded) and writes the equivalent
`res.partner` exemption fields, which the engine then syncs to Midas's cert vault on write.

Field mapping (taxcloud.exemption -> res.partner):
    partner_id              -> the buyer
    id_number               -> resale_certificate_number  (the permit #; may be blank/placeholder)
    state_ids (or issue st) -> us_tax_exemption_state_ids  (FAT Chips: all CA)
    certificate_reason.code -> entity_use_code             (Resale -> RESELLER; see _MAP)
    expiration_date         -> us_tax_exemption_expiry
                            -> is_tax_exempt = True

Run on chip (read prod, dry-run first):
    sudo -u odoo /odoo/odoo-server/odoo-bin shell -c /etc/odoo-server.conf -d eatfatchips --no-http
    >>> from odoo.addons.us_sales_tax.tools.migrate_taxcloud_certs import migrate
    >>> migrate(env)                 # DRY RUN: prints the plan, writes nothing
    >>> migrate(env, commit=True)    # APPLY: writes res.partner fields + cert.commit()

Idempotent: re-running updates the same partners. Blank/placeholder permit #s are migrated as-is
but listed in the report so the owner can chase the real number (the exemption still applies; the
permit is audit evidence the merchant is responsible for — see the partner-form warning).
"""
import logging

_logger = logging.getLogger(__name__)

# TaxCloud certificate_reason.code -> our entity_use_code.
_MAP = {
    "Resale": "RESELLER",
    "AgriculturalProduction": "AG",
    "IndustrialProductionOrManufacturing": "MFG",
    "CharitableOrganization": "NONPROFIT",
    "ReligiousOrganization": "NONPROFIT",
    "EducationalOrganization": "NONPROFIT",
    "FederalGovernmentDepartment": "GOV",
    "StateOrLocalGovernmentName": "GOV",
    "TribalGovernmentName": "GOV",
}


def migrate(env, commit=False):
    """Scrape active taxcloud.exemption rows into res.partner exemption fields.

    commit=False (default) prints the plan and writes nothing. commit=True applies + commits.
    Returns a summary dict.
    """
    Tc = env.get("taxcloud.exemption")
    if Tc is None:
        print("taxcloud.exemption model not found — is the TaxCloud exemption module installed?")
        return {"error": "no taxcloud.exemption model"}

    active = env["taxcloud.exemption"].search([("state", "in", ("export", "draft"))])
    # Collapse to one plan per partner; merge certificate states; keep a non-blank permit if any,
    # and the latest expiry. (FAT Chips has ~1 active cert/partner, all CA/Resale.)
    plan = {}
    for ex in active:
        p = ex.partner_id
        if not p:
            continue
        states = ex.state_ids or ex.state_of_issue_id
        slot = plan.setdefault(p.id, {
            "partner": p, "permit": "", "states": env["res.country.state"],
            "use_code": "RESELLER", "expiry": False, "sources": 0,
        })
        slot["sources"] += 1
        slot["states"] |= states
        if ex.id_number and not _is_placeholder(ex.id_number) and not slot["permit"]:
            slot["permit"] = ex.id_number
        elif ex.id_number and not slot["permit"]:
            slot["permit"] = ex.id_number  # placeholder is better than nothing; flagged below
        reason = ex.certificate_reason.code if ex.certificate_reason else "Resale"
        slot["use_code"] = _MAP.get(reason, "OTHER")
        if ex.expiration_date and (not slot["expiry"] or ex.expiration_date > slot["expiry"]):
            slot["expiry"] = ex.expiration_date

    blanks, placeholders, written = [], [], 0
    for pid, slot in sorted(plan.items()):
        p = slot["partner"]
        permit = slot["permit"]
        if not permit:
            blanks.append(p.name)
        elif _is_placeholder(permit):
            placeholders.append("%s (%s)" % (p.name, permit))
        vals = {
            "is_tax_exempt": True,
            "resale_certificate_number": permit or "",
            "us_tax_exemption_state_ids": [(6, 0, slot["states"].ids)],
            "entity_use_code": slot["use_code"],
            "us_tax_exemption_expiry": slot["expiry"] or False,
        }
        print("%-40s permit=%-18s states=%-6s use=%s exp=%s" % (
            p.name[:40], permit or "(BLANK)", ",".join(slot["states"].mapped("code")),
            slot["use_code"], slot["expiry"] or "-"))
        if commit:
            p.write(vals)
            written += 1

    print("\n--- summary ---")
    print("active certs:", len(active), "| distinct partners:", len(plan))
    print("BLANK permit (chase the number):", len(blanks), blanks or "")
    print("PLACEHOLDER permit (looks fake — verify):", len(placeholders), placeholders or "")
    if commit:
        env.cr.commit()
        print("APPLIED to", written, "partners (committed). res.partner.write auto-synced to Midas "
              "if the company is on the hosted provider.")
    else:
        print("DRY RUN — nothing written. Re-run migrate(env, commit=True) to apply.")
    return {"active": len(active), "partners": len(plan), "written": written if commit else 0,
            "blanks": blanks, "placeholders": placeholders}


def _is_placeholder(num):
    """Heuristic: obvious junk like '111111', '2222222222', all-same-digit, or <=6 repeated chars."""
    digits = "".join(ch for ch in (num or "") if ch.isdigit())
    return bool(digits) and len(set(digits)) == 1
