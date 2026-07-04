# US Sales Tax (Odoo 17 CE)

A maintained, CE-native replacement for Odoo's removed TaxCloud integration, with
**correct product-level taxability** (food/exemption-aware) and a **fail-safe,
coupon-aware** compute path.

## Why

Odoo dropped native TaxCloud in 17.0. The community `*_taxcloud_tc` re-ports are
unmaintained: they crash checkout on coupon/promo orders and tax CA-exempt food at
the full rate (no taxability codes). This module fixes the class of problem:

- **Never crashes checkout.** The engine is wrapped in a fail-safe handler. On any
  error it applies the company's fail mode — *fail-open* ($0 + a flagged note on
  the order, recommended) or *fail-closed* (block). It never 500s the storefront.
- **Coupon/discount aware.** Reward lines are taxed by their own taxability
  category (a free-shipping reward → exempt → $0), so promotions never break tax.
- **Correct exemptions.** Taxability is data: a `category × state` rule matrix
  (`us.tax.rule`). Snack food is exempt in CA (Prop 163, 1992) — encoded, not
  hardcoded.

## Architecture (open-core)

| Layer | What |
|---|---|
| `us.tax.category` | Product taxability class (the open analogue of an Avalara TIC). |
| `us.tax.rule` | `(category, state) → taxable?` — the exemption matrix. `*` = catch-all. |
| `us.tax.rate` | State-level rate for the **local** provider (approximation). |
| `us.tax.engine` | Resolves a rate per (state, category) and assigns the tax. |
| Provider | `local` (built-in, free) or `hosted` (calls a lookup service). |

The **local** provider ships free and works standalone (CA seeded + any data you maintain yourself). The **hosted**
provider points at a Davenport Pacific lookup service for rooftop-accurate, nationally-maintained
rates + the full exemption matrix — the paid layer. Same data shape either way.

## For Clients (Fat Chips, etc.)

Davenport Pacific treats Fat Chips and other businesses as clients of DVPTP.

**How to install as a client:**

1. Pull the module **directly from the dvptp source** (not from any internal fatchips-odoo tree):
   - Clone the official repository published under the dvptp GitHub organization, or
   - `git clone <dvptp-us-sales-tax-repo-url>` and copy/symlink the `us_sales_tax` directory into your Odoo `custom_addons`.
   - Add the path to your `addons_path` and update the app list.

2. In Odoo:
   - Install the "US Sales Tax" module.
   - Go to *Accounting → Configuration → Settings → US Sales Tax*.
   - Set **Provider** to **Hosted**.
   - Enter the **API URL** provided by DVPTP.
   - Enter the **API Key** issued to your company (see below).

3. Assign US Tax Categories to your products (Accounting tab) and test with a sale order or invoice to a US destination.

The **local** provider remains available as a no-key fallback (limited accuracy, using only the data bundled with the module).

**API Keys**
- Keys are issued per client by Davenport Pacific.
- They grant access to the full paywalled national lookup database.
- Keys are sent with every hosted request (the module already does this via the Authorization header).
- For local testing on this machine, the hosted service accepts (among others):
  - `fatchips-eatfatchips-client-2026` (dedicated test key for Fat Chips)
- Example keys are also listed in `sales-tax-saas/hosted/app.py`.

The previous copy of this module that lived inside `fatchips-odoo/us_sales_tax/` is now ignored and should not be used. All future updates come from the dvptp-published source.

## Configuration

*Accounting → Configuration → Settings → US Sales Tax*: enable, pick a provider,
set a default tax category, choose the fail mode. Manage the matrix under
*Accounting → Configuration → US Sales Tax* (Categories / Rules / State Rates).
Set each product's **US Tax Category** on its form (Accounting tab).

## Known boundaries (v0.1)

- **Local rates are state-level**, not rooftop (city/county/district). Use the
  hosted provider for exact rates. A state that is *taxable* but has no
  `us.tax.rate` row computes **0%** (under-collection) — seed rates for every
  nexus state, or use hosted.
- **Auto-compute is hooked on `sale.order` confirmation only.** Invoices inherit
  tax from their order lines. Direct (non-order) invoices expose a manual
  *Recompute US Tax* action; auto-on-post for direct invoices is Phase 1.1
  (deliberately deferred — auto-recompute-on-post is where the legacy taxcloud
  modules created re-entrancy crashes).
- **Reward-line taxability** follows the reward product's own category. A
  percentage discount that should reduce a taxable base proportionally is a
  Phase 1.1 refinement.

## Tests

`tests/test_local_ca.py` covers the CA matrix (snack exempt, general/clothing
taxable), managed-tax reuse, SO application, and that fail-open never raises.

```
odoo-bin -d <db> -i us_sales_tax --test-enable --stop-after-init
```

## Hosted Service + Paywalled Lookup Protection (the commercial model)

The Odoo module is fully open source (LGPL). It ships with a complete "local" provider that works out of the box using the data and rules that are bundled in the module (CA matrix today; extensible with public data).

The secret sauce — the full national lookup database (every state + county + city + district, rooftop precision, and the rich product-category taxability/exemption matrix with citations and ongoing updates) — lives only in the hosted service and is paywalled.

### How keys work
- When `Provider = "local"`: no key, no network call. Uses the OSS data. Always available.
- When `Provider = "hosted"`: the module sends the configured API key on every lookup (Authorization: Bearer ... header, already wired in the engine).
- The hosted service enforces the key. Missing or bad key → 401/403. Valid demo keys for local testing on this machine:
  - `demo-local-test-key-123`
  - `midas-test-key-456`
- In production the hosted service will have real customer keys, usage tracking, rate limits, and billing.

### Running the hosted locally for testing
1. ```
   cd ../hosted
   pip install -r requirements.txt
   uvicorn app:app --reload --port 8000
   ```
2. In Odoo (Settings → US Sales Tax):
   - Provider: Hosted
   - API URL: http://localhost:8000
   - API Key: demo-local-test-key-123   # must be one of the DEMO_KEYS
3. Test flows (SO confirm, Recompute US Tax button on invoices, etc.) now require the key for hosted.

See hosted/test_hosted.py (includes auth tests) and the updated Odoo tests in `tests/test_local_ca.py` (key enforcement + mocked hosted path).

For the real national DB: see SCOPE.md (Jurisdictions hierarchy, ETL from CDTFA/TX Comptroller/SST Rate+Boundary+Taxability files, versioning, etc.).

## Parallel Calculations with TaxCloud (for Data Refinement)

For stores like lotusandluna.com (Shopify + TaxCloud user selling nationwide), install this module alongside your existing TaxCloud setup.

- Enable "US Sales Tax" + set provider (local or hosted) + "Enable TaxCloud Parallel Comparison" + provide your TaxCloud API Login ID + Key (in Settings).
- On order confirmation or invoice post, our engine computes tax using our categories/rules (e.g. for digital goods).
- In parallel, it calls TaxCloud API with mapped TICs for the same lines and logs differences in the chatter (e.g., "Our rate: 7.25% ($X) vs TaxCloud: 8.5% ($Y) | Category: DIGITAL | State: CA").
- Review the logs to refine our data (adjust rules for digital goods in specific states/counties, add exceptions for goods, etc.). Non-destructive — your TaxCloud taxes remain unless you switch the provider.
- Perfect for validating/refining our national DB (especially for digital goods, which have varying taxability: often taxable as SaaS/digital but exempt or reduced in some jurisdictions).

This lets you run "our module" calculations in parallel with TaxCloud, compare outputs, and iteratively improve the exemption matrix/rates without risk.

## Shopify Integration (for lotusandluna.com and similar)

Since many users (e.g. lotusandluna.com) run on Shopify + TaxCloud:
- Use the **hosted API** directly from Shopify (no Odoo required for the SaaS layer).
  - Call `POST /v1/tax` (or /v1/rate for simple) with destination state + lines (with our category codes, e.g. "DIGITAL" for digital products).
  - Use the returned rates/taxes to override or validate in your Shopify tax settings/scripts/apps.
- For full parallel: in your Shopify app or scripts, call both our hosted API and TaxCloud, compare, log diffs to refine data (feed back into our rules).
- The module (Odoo) is the reference implementation; the hosted SaaS is the reusable service for any platform (Shopify, custom, etc.).
- Scope full Shopify app/tax service integration after the dedicated Shopify project (per your note).

## v0.2 — nexus gate, resale exemptions, record-of-tax ledger (2026-06-13)

- **Nexus gate (`us_tax_nexus_state_ids` on the company).** Set the states where you have nexus;
  ship-to states *outside* the list collect **$0** (no out-of-state over-collection) and are tagged
  `no_nexus`. Empty list = collect everywhere (gate off). Pair with `us_tax_unknown_taxable=False`
  so an unmapped (category, state) *inside* nexus can't silently over-collect.
- **County/city enrichment.** The hosted `/v1/rate` lookup now passes the ship city so CA returns
  the full combined rate (state + county + district), not just the 7.25% floor.
- **Buyer resale-certificate exemption (`res.partner`).** `is_tax_exempt` + certificate # + states
  + entity/use code + expiry + document. A valid (non-expired, ship-state-matching) cert charges
  **$0** tagged `resale_certificate` (distinct from food/no-nexus) and stamps the cert on the order.
  Certs sync to the hosted service (cert vault) when the hosted provider is configured.
- **Record-of-tax ledger.** On confirm, every sale (incl. out-of-state $0) is logged — locally
  (`us.tax.transaction.log`, the reconciliation mirror) and pushed to the hosted append-only ledger
  (jurisdiction + gross/taxable/exempt-by-reason + cert ref + opaque order ref, **no retail PII**).
  Exempt sales are *reported with exempt deductions, not omitted* — so filing + nexus monitoring work.

## License

LGPL-3.
