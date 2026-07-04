import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# How long to wait on the hosted lookup service before giving up and applying the
# company's fail mode. Kept short so a slow service never stalls checkout.
HOSTED_TIMEOUT = 8


class UsTaxEngine(models.AbstractModel):
    """Stateless compute engine: resolves a tax rate for (state, category) and
    applies the resulting tax to document lines.

    Providers
    ---------
    * ``local``  -- reads ``us.tax.rule`` (taxability) + ``us.tax.rate`` (rate).
    * ``hosted`` -- POSTs the line set to a lookup service; expects per-line rates.

    Callers (``sale.order`` / ``account.move``) wrap ``apply`` in their own
    fail-safe handler, so this engine may raise freely.
    """

    _name = "us.tax.engine"
    _description = "US Sales Tax compute engine"

    # ------------------------------------------------------------------ rates
    @api.model
    def compute_rate(self, company, state_code, category, county=None, city=None):
        """Return the tax rate (percent) for a category shipped to a state (+ county/city).

        0.0 means exempt. Raises only on genuine provider failure.

        county/city enrich the hosted lookup so CA returns the FULL COMBINED rate (state + county
        + district), not just the 7.25% state floor -- the difference that matters for the taxable
        FAT Hat. The local provider ignores them (it only carries state-level rates).

        Hosted path requires a valid us_tax_api_key (the lookup DB is paywalled;
        the OSS module itself works fully with provider=local using bundled data).
        """
        # Normalize the lookup key. A stray lowercase/whitespace state would
        # otherwise miss every rule and silently resolve to 0% (under-collection).
        state_code = (state_code or "").strip().upper()
        if company.us_tax_provider == "hosted":
            rate = self._compute_rate_hosted(company, state_code, category, county=county, city=city)
            if rate is not None:
                return rate
            # Hosted unavailable (no key / unlicensed / unreachable): fall back to the
            # bundled-local table so checkout never bricks (WARN never brick). CA stays
            # rooftop-accurate; other states use whatever local rows the merchant added.
            return self._compute_rate_local(company, state_code, category, county=county, city=city)
        return self._compute_rate_local(company, state_code, category, county=county, city=city)

    @api.model
    def _resolve_taxable(self, company, state_code, category):
        """Look up taxability: exact (category, state) rule, then the category's
        '*' default, then the company's unknown-handling policy."""
        Rule = self.env["us.tax.rule"].sudo()
        rule = Rule.search(
            [("category_id", "=", category.id), ("state_code", "=", state_code)],
            limit=1,
        )
        if not rule:
            rule = Rule.search(
                [("category_id", "=", category.id), ("state_code", "=", "*")],
                limit=1,
            )
        if not rule:
            return company.us_tax_unknown_taxable
        return rule.taxable

    @api.model
    def _compute_rate_local(self, company, state_code, category, county=None, city=None):
        """Rooftop-accurate combined rate from the bundled jurisdiction table.

        Ships with all of California (state + county + city); merchants may hand-add
        other states as single-state rows. county/city resolve most-specific-first.
        """
        Rate = self.env["us.tax.rate"].sudo()
        if not category:
            # No category at all -> defer to the company's unknown policy.
            if not company.us_tax_unknown_taxable:
                return 0.0
            return Rate.get_combined_rate(state_code, county=county, city=city)
        if not self._resolve_taxable(company, state_code, category):
            return 0.0
        return Rate.get_combined_rate(state_code, county=county, city=city)

    @api.model
    def _compute_rate_hosted(self, company, state_code, category, county=None, city=None):
        """Rooftop / multi-state rate via PBM's unified hosted exec tool (§4.2).

        Returns the rate (percent), or None when hosted is unavailable so the caller falls
        back to the bundled-local table (WARN never brick). The rate database + taxability
        engine run server-side and never ship -- the installed module is only a thin client.
        """
        result = self._pbm_exec(company, "tax_rate_lookup", {
            "state": state_code,
            "category": category.code if category else None,
            "county": county or None,
            "city": city or None,
        })
        if result is None:
            return None
        return float(result.get("rate", 0.0))

    @api.model
    def _pbm_exec(self, company, tool, payload):
        """POST a minimal payload to PBM's hosted exec endpoint; return the tool's result
        dict, or None on any unavailability (unconfigured / unreachable / unlicensed).

        Never raises -- degradation is the caller's job. Only minimal/derived jurisdiction
        descriptors leave the instance (never the cart or ledger), so a rate lookup discloses
        no customer data.
        """
        base = (company.us_tax_api_url or "").rstrip("/")
        key = company.us_tax_api_key or ""
        if not base or not key:
            return None  # unconfigured -> husk (falls back to bundled-local data)
        import requests  # local import: only needed on the hosted path
        uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid") or ""
        try:
            resp = requests.post(
                "%s/v1/exec/%s" % (base, tool),
                json={"instance_uuid": uuid, "payload": payload},
                headers={"Authorization": "Bearer %s" % key},
                timeout=HOSTED_TIMEOUT,
            )
        except requests.RequestException as err:
            _logger.warning("PBM hosted '%s' unreachable (%s) -- using local data.", tool, err)
            return None
        if resp.status_code == 200:
            body = resp.json()
            if body.get("warning"):
                _logger.info("PBM hosted '%s': %s", tool, body["warning"])
            return body.get("result") or {}
        if resp.status_code in (401, 403):
            _logger.warning(
                "PBM hosted '%s': key rejected/expired (HTTP %s) -- using local data.",
                tool, resp.status_code)
            return None
        _logger.warning("PBM hosted '%s' error (HTTP %s) -- using local data.", tool, resp.status_code)
        return None

    @api.model
    def _compute_full_cart_hosted(self, company, ship_state, lines_data):
        """Full cart lookup using /v1/tax (preferred for accuracy with mixed categories like digital goods).
        lines_data: list of dicts { 'category': code_or_None, 'amount': , 'qty': }
        Returns list of per-line results (rate, tax, taxable, jurisdiction, exemption_reason).
        """
        if not company.us_tax_api_url:
            raise UserError(_("US Sales Tax: hosted provider selected but no API URL configured."))
        # Key presence already enforced by public entry points.
        import requests
        payload = {
            "destination": {"state": ship_state},
            "lines": lines_data,
        }
        resp = requests.post(
            company.us_tax_api_url.rstrip("/") + "/v1/tax",
            data=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % (company.us_tax_api_key or ""),
            },
            timeout=HOSTED_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("lines", [])

    # Our category code -> TaxCloud TIC (Taxability Information Code). Approximate;
    # the whole point of the comparison is to discover where these should differ
    # per state and refine our own taxability DB accordingly.
    _TAXCLOUD_TIC = {
        "GENERAL": "00000",     # general tangible personal property
        "FOOD": "10010",        # food / groceries
        "FOOD_SNACK": "10010",  # snack/candy -> treat as food, refine per state
        "CLOTHING": "20010",    # apparel
        "DIGITAL": "31000",     # digital goods / SaaS
        "SHIPPING": "11010",    # shipping & handling
        "EXEMPT": "00000",
    }

    @api.model
    def _compute_taxcloud(self, company, doc, ship_state, line):
        """Observe-only: ask TaxCloud what tax it would charge for this line.

        Used purely to validate/refine our own taxability DB against TaxCloud --
        never to set the tax we apply. Returns {rate, tax_amount, taxable, raw}
        or {error}. Requires us_taxcloud_api_login_id + us_taxcloud_api_key.
        """
        if not company.us_taxcloud_api_login_id or not company.us_taxcloud_api_key:
            return {"error": "TaxCloud credentials not configured"}

        import requests
        category = self._line_category(line, company)
        tic = self._TAXCLOUD_TIC.get(category.code, "00000") if category else "00000"

        ship = doc.partner_shipping_id or doc.partner_id
        origin = company.partner_id
        # Line shape differs: sale.order.line uses product_uom_qty, account.move.line uses quantity.
        qty = getattr(line, "product_uom_qty", None)
        if qty is None:
            qty = getattr(line, "quantity", 1.0)
        product = line.product_id if line.product_id else False

        payload = {
            "apiLoginID": company.us_taxcloud_api_login_id,
            "apiKey": company.us_taxcloud_api_key,
            "customerID": str(getattr(ship, "id", "") or "anon"),
            "cartID": str(getattr(line, "id", "") or "cart"),
            "origin": {
                "Address1": origin.street or "",
                "City": origin.city or "",
                "State": origin.state_id.code or "",
                "Zip5": (origin.zip or "")[:5],
            },
            "destination": {
                "Address1": ship.street or "",
                "City": ship.city or "",
                "State": ship.state_id.code or ship_state or "",
                "Zip5": (ship.zip or "")[:5],
            },
            "cartItems": [{
                "Index": 0,
                "ItemID": (product.default_code or str(product.id)) if product else "sku",
                "TIC": tic,
                "Price": float(getattr(line, "price_unit", 0.0) or 0.0),
                "Qty": float(qty or 1.0),
            }],
            "deliveredBySeller": False,
        }

        try:
            resp = requests.post(
                "https://api.taxcloud.net/1.0/TaxCloud/Lookup",
                json=payload,
                timeout=HOSTED_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            item = (data.get("CartItems") or [{}])[0]
            tax_amount = float(item.get("TaxAmount", 0) or 0)
            rate = float(item.get("Rate", 0) or 0) * 100
            return {
                "rate": rate,
                "tax_amount": tax_amount,
                "taxable": tax_amount > 0 or rate > 0,
                "raw": data,
            }
        except Exception as e:
            _logger.exception("TaxCloud comparison call failed")
            return {"error": str(e)}

    # ------------------------------------------------------------------ taxes
    @api.model
    def _get_or_create_tax(self, company, rate):
        """Find (or create) a sale ``account.tax`` of the given percent rate.

        Returns an empty recordset for a 0 rate so exempt lines carry no tax at
        all (cleaner than a 0%% tax line).
        """
        if not rate:
            return self.env["account.tax"]
        Tax = self.env["account.tax"].sudo()
        tax = Tax.search([
            ("company_id", "=", company.id),
            ("type_tax_use", "=", "sale"),
            ("amount_type", "=", "percent"),
            ("amount", "=", rate),
            ("us_tax_managed", "=", True),
        ], limit=1)
        if tax:
            return tax
        return Tax.create({
            "name": "US Sales Tax %.4g%%" % rate,
            "amount": rate,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "company_id": company.id,
            "tax_group_id": self._us_tax_group(company).id,
            "us_tax_managed": True,
            "description": "Sales Tax",
        })

    @api.model
    def _us_tax_group(self, company):
        """Find/create a clearly-named 'US Sales Tax' tax group so the order/invoice
        subtotal reads 'US Sales Tax', not the CoA's generic default (e.g. 'Tax 15%')."""
        Group = self.env["account.tax.group"].sudo()
        grp = Group.search(
            [("company_id", "=", company.id), ("name", "=", "US Sales Tax")], limit=1)
        if not grp:
            grp = Group.create({"name": "US Sales Tax", "company_id": company.id})
        return grp

    @api.model
    def _ship_state(self, doc):
        """USPS state code we ship to, from the doc's delivery (then billing) partner.

        Single source of truth for both sale.order and account.move (they share the
        same partner_shipping_id / partner_id shape), so the resolution can't drift.
        """
        partner = doc.partner_shipping_id or doc.partner_id
        return (partner.state_id.code or "") if partner else ""

    @api.model
    def _ship_locality(self, doc):
        """(county, city) of the ship-to partner for combined-rate enrichment.

        Odoo's res.partner has no county field, so county is None; the hosted service resolves
        city -> county -> state itself. city lets CA return the full combined rate for the FAT Hat.
        """
        partner = doc.partner_shipping_id or doc.partner_id
        if not partner:
            return None, None
        return None, (partner.city or None)

    @api.model
    def _outside_nexus(self, company, ship_state):
        """True if a nexus list is configured AND the ship-state isn't in it (-> charge $0).

        Empty nexus list = gate disabled (collect per the rules everywhere) for backward compat.
        An empty ship-state with a configured nexus is treated as OUTSIDE -> $0 (never wrong-state;
        a guest with no resolved state is blocked/zeroed, per plan, not taxed at a guessed rate).
        """
        nexus = company.us_tax_nexus_state_ids.mapped("code")
        if not nexus:
            return False
        return (ship_state or "").strip().upper() not in nexus

    @api.model
    def _apply_zero(self, lines):
        """Assign no tax to every line (exempt: resale cert, no nexus, or category-exempt)."""
        empty = self.env["account.tax"]
        for line in lines:
            self._assign_tax(line, empty)

    @api.model
    def apply_safe(self, doc):
        """Fail-safe entry point: resolve ship-state, apply tax, honor fail mode.

        Wraps :meth:`apply` so a tax error never 500s checkout unless the company
        opted into fail-closed. Deduplicated from sale.order/account.move (the two
        wrappers were verbatim copies of tax-critical logic).
        """
        company = doc.company_id or self.env.company
        if not company.us_tax_enabled:
            return True
        noun = "invoice" if doc._name == "account.move" else "order"
        try:
            self.apply(doc, self._ship_state(doc))
        except Exception as err:  # noqa: BLE001 - deliberately broad; see fail mode
            _logger.exception("US Sales Tax: compute failed for %s", doc.display_name)
            if company.us_tax_fail_mode == "closed":
                raise UserError(_(
                    "Sales tax could not be computed for this %s. "
                    "Please try again.") % noun)
            doc.message_post(body=_(
                "US Sales Tax: tax compute failed; applied $0 (fail-open). "
                "Please review. Error: %s") % err)
        return True

    @api.model
    def apply(self, doc, ship_state):
        """Compute and assign per-line taxes on a sale.order / account.move.

        Reward/discount lines are LEFT ALONE on the normal path: Odoo's loyalty module sets a
        discount line's tax to match the line(s) it discounts (and a free-shipping reward to the
        delivery line), and it re-syncs that on confirm. If this engine also taxed the reward line
        (by its empty product category -> exempt), the quote total would differ from the confirmed
        total by the tax-on-discount (a coupon+tax mismatch -- the FCS01096 class). On the EXEMPT
        paths (resale cert / no nexus) every line incl. rewards is zeroed.

        If us_tax_comparison_enabled and TaxCloud creds are set, also runs an
        observe-only TaxCloud comparison (logs divergences for DB refinement;
        never changes the tax actually applied).
        """
        company = doc.company_id or self.env.company
        ship_state = (ship_state or "").strip().upper()
        lines = self._taxable_lines(doc)

        # (1) Buyer-level resale-certificate exemption (highest priority). A valid, non-expired,
        # ship-state-matching cert -> $0 on every line, tagged distinctly from food/no-nexus.
        cert = doc.partner_id._us_valid_resale_cert(ship_state) if doc.partner_id else None
        if cert:
            self._apply_zero(lines)
            doc._us_set_exemption("resale_certificate", cert.get("certificate_number"))
            return True

        # (2) Nexus gate: ship-to outside our nexus -> $0 (no out-of-state over-collection).
        # The sale is still logged (no_nexus) on confirm for threshold monitoring.
        if self._outside_nexus(company, ship_state):
            self._apply_zero(lines)
            doc._us_set_exemption("no_nexus", None)
            return True

        # (3) Normal path: category x jurisdiction rates. Clear any stale exemption stamp.
        doc._us_set_exemption(False, None)
        county, city = self._ship_locality(doc)
        # Cache rate per category so a multi-line cart hits the provider once per
        # distinct taxability class, not once per line. (county/city are constant per doc.)
        rate_cache = {}
        for line in lines:
            # Leave reward/discount lines to Odoo's loyalty (it taxes them to match the discounted
            # lines, consistently at quote AND confirm). Touching them here desyncs the totals.
            if getattr(line, "is_reward_line", False):
                continue
            category = self._line_category(line, company)
            key = category.id if category else False
            if key not in rate_cache:
                rate_cache[key] = self.compute_rate(company, ship_state, category, county=county, city=city)
            tax = self._get_or_create_tax(company, rate_cache[key])
            self._assign_tax(line, tax)

        if (company.us_tax_comparison_enabled
                and company.us_taxcloud_api_login_id
                and company.us_taxcloud_api_key):
            self._log_taxcloud_comparison(doc, company, ship_state, lines, rate_cache)
        return True

    @api.model
    def _log_taxcloud_comparison(self, doc, company, ship_state, lines, rate_cache):
        """Observe-only: diff our engine's tax against TaxCloud per line and log it.

        This mirrors the planned Shopify app -- a store running TaxCloud, our code
        doing nothing but logging both numbers and the gap -- so we can validate
        and refine our taxability DB. It never alters the tax we apply.
        """
        for line in lines:
            # Reward/discount lines aren't engine-taxed (loyalty owns them) -> skip the comparison
            # too (negative-amount reward lines would just produce noise / spurious diffs).
            if getattr(line, "is_reward_line", False):
                continue
            category = self._line_category(line, company)
            our_rate = rate_cache.get(category.id if category else False, 0.0)
            # price_subtotal is the post-discount taxable base on both line models.
            base = getattr(line, "price_subtotal", 0.0) or 0.0
            our_tax = base * (our_rate / 100.0)
            tc = self._compute_taxcloud(company, doc, ship_state, line)
            if "error" in tc:
                doc.message_post(body=_(
                    "US Sales Tax: TaxCloud comparison error: %s") % tc["error"])
                continue
            tc_tax = tc.get("tax_amount", 0.0)
            tc_rate = tc.get("rate", 0.0)
            if abs(our_tax - tc_tax) > 0.01:
                doc.message_post(body=_(
                    "US Sales Tax comparison (observe-only, for DB refinement):<br/>"
                    "Ours: %.4f%% ($%.2f) vs TaxCloud: %.4f%% ($%.2f) | diff $%.2f<br/>"
                    "Category: %s | Ship-to: %s"
                ) % (our_rate, our_tax, tc_rate, tc_tax, abs(our_tax - tc_tax),
                     category.code if category else "N/A", ship_state))
        return True

    @api.model
    def _taxable_lines(self, doc):
        """The lines we should set taxes on (skip notes/sections)."""
        if doc._name == "sale.order":
            return doc.order_line.filtered(lambda l: not l.display_type)
        return doc.invoice_line_ids.filtered(
            lambda l: l.display_type in (False, "product"))

    @api.model
    def _line_category(self, line, company):
        product = line.product_id.product_tmpl_id if line.product_id else False
        if product and product.us_tax_category_id:
            return product.us_tax_category_id
        return company.us_tax_default_category_id

    @api.model
    def _assign_tax(self, line, tax):
        field = "tax_id" if (line._name == "sale.order.line" and "tax_id" in line._fields) else "tax_ids"
        line[field] = [(6, 0, tax.ids)]

    @api.model
    def _line_has_tax(self, line):
        field = "tax_id" if (line._name == "sale.order.line" and "tax_id" in line._fields) else "tax_ids"
        return bool(line[field])

    # --------------------------------------------------- ledger (record-of-tax)
    @api.model
    def build_ledger_jurisdictions(self, doc):
        """Per-ship-to-state breakdown for the Midas ledger (single ship state for FAT Chips).

        Reports gross with exempt deductions split by reason -- resale (RESELLER), no-nexus
        (NO_NEXUS), or category-exempt (FOOD) -- never omitting exempt sales. tax_collected is the
        doc's actual tax so it reconciles with the confirmed order / invoice.
        """
        ship_state = self._ship_state(doc)
        lines = self._taxable_lines(doc)
        gross = sum((getattr(l, "price_subtotal", 0.0) or 0.0) for l in lines)
        tax_collected = doc.amount_tax or 0.0
        reason = doc.us_tax_exempt_reason or ""
        cert_ref = doc.us_tax_exempt_certificate or ""

        if reason in ("resale_certificate", "no_nexus"):
            taxable, exempt = 0.0, gross
            code = "RESELLER" if reason == "resale_certificate" else "NO_NEXUS"
            breakdown = [{"entity_use_code": code, "count": 1, "amount": round(exempt, 2)}]
        else:
            taxable = sum((getattr(l, "price_subtotal", 0.0) or 0.0)
                          for l in lines if self._line_has_tax(l))
            exempt = round(gross - taxable, 2)
            breakdown = [{"entity_use_code": "FOOD", "count": 1, "amount": exempt}] if exempt > 0 else []
            if exempt > 0 and taxable == 0:
                reason = "category_exempt"

        # A credit note posts NEGATIVE amounts so a refund nets out the original sale in the ledger
        # (the Midas ledger accepts signed amounts; the calc endpoints don't). Odoo carries refund
        # magnitudes as positive on the move, so we flip the sign here for out_refund.
        sign = -1.0 if (doc._name == "account.move" and doc.move_type == "out_refund") else 1.0
        for b in breakdown:
            b["amount"] = round(sign * b["amount"], 2)
        return [{
            "state": ship_state, "gross": round(sign * gross, 2), "taxable": round(sign * taxable, 2),
            "exempt": round(sign * exempt, 2), "tax_collected": round(sign * tax_collected, 2),
            "exempt_reason": reason, "exempt_breakdown": breakdown, "certificate_ref": cert_ref,
        }]

    @api.model
    def push_transaction(self, doc):
        """Write the local mirror + push the append-only ledger record to Midas (best-effort).

        Out-of-state $0 sales push too (no_nexus) so nexus monitoring sees them. Never blocks
        confirm: a network/API failure is recorded on the local mirror and logged, not raised.
        """
        company = doc.company_id or self.env.company
        if not company.us_tax_enabled:
            return True
        jurisdictions = self.build_ledger_jurisdictions(doc)
        order_ref = doc.name or ("%s-%s" % (doc._name, doc.id))
        occurred = fields.Datetime.now()
        Log = self.env["us.tax.transaction.log"].sudo()
        # Replace-set the local mirror too (match Midas): clear any prior rows for this order_ref so
        # a re-push restates rather than duplicates the mirror.
        Log.search([("company_id", "=", company.id), ("order_ref", "=", order_ref)]).unlink()
        logs = self.env["us.tax.transaction.log"].sudo()
        for j in jurisdictions:
            logs |= Log.create({
                "order_ref": order_ref,
                "company_id": company.id,
                "sale_order_id": doc.id if doc._name == "sale.order" else False,
                "occurred_at": occurred,
                "state_code": j["state"],
                "gross": j["gross"], "taxable": j["taxable"], "exempt": j["exempt"],
                "tax_collected": j["tax_collected"], "exempt_reason": j["exempt_reason"] or "",
                "certificate_ref": j["certificate_ref"],
            })

        if company.us_tax_provider == "hosted" and company.us_tax_api_url and company.us_tax_api_key:
            import requests  # local import: only on the hosted path
            payload = {"order_ref": order_ref, "occurred_at": occurred.isoformat(),
                       "jurisdictions": jurisdictions}
            try:
                resp = requests.post(
                    company.us_tax_api_url.rstrip("/") + "/v1/transactions",
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer %s" % company.us_tax_api_key},
                    timeout=HOSTED_TIMEOUT)
                resp.raise_for_status()
                logs.write({"midas_synced": True})
            except Exception as e:  # noqa: BLE001 - best-effort; never block confirm
                _logger.warning("US Sales Tax: ledger push to Midas failed for %s: %s", order_ref, e)
                logs.write({"midas_error": str(e)[:200]})
        return True
