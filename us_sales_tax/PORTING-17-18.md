# us_sales_tax — Odoo 17 ↔ 18 porting notes

We maintain this module for **both Odoo 17 and Odoo 18** (FAT Chips/chip is on 17; 18 is the
forward target). Verified 2026-06-13 on the s76t rigs: **21/21 tests pass on BOTH** `odoo:17`
(`bosun-odoo17`, :8078) and `odoo:18` (`bosun-odoo`, :8077).

**The entire delta between the two versions is exactly TWO mechanical changes.** All Python
(models, engine, res.partner exemption fields, ledger push, security) and the
`res.config.settings` + `res.partner` notebook views are **identical** across versions.

## The delta

### 1. Manifest `version` — series prefix is validated
Odoo refuses a manifest whose version starts with a *different* series:
- 17 branch: `"version": "17.0.0.2.0"`
- 18 branch: `"version": "18.0.0.2.0"`

(`ValueError: Invalid version '17.0.0.2.0'. Modules should have a version in format ... '18.0.x.y'`
on 18.) A series-less `x.y.z` is accepted by both, but we keep the explicit series per branch to
match the rest of the stack (e.g. Bosun).

### 2. List views — `<tree>` (17) vs `<list>` (18), mutually exclusive
Odoo 18 removed the `tree` view type; Odoo 17 doesn't yet know `list`:
- 17: `<tree>…</tree>` and `<field name="view_mode">tree[,form]</field>`
- 18: `<list>…</list>` and `<field name="view_mode">list[,form]</field>`

You CANNOT satisfy both from one static file (`18 → "Invalid view type: 'tree'"`;
`17 → "Wrong value for ir.ui.view.type: 'list'"`). Affected files (view tags + `view_mode` only;
the record `id="…_tree"`/`name` strings are just identifiers and don't matter):
`views/us_tax_rate_views.xml`, `views/us_tax_rule_views.xml`, `views/us_tax_category_views.xml`,
`views/us_tax_transaction_log_views.xml`.

> Form-view inheritances (`res_config_settings_views.xml`, `res_partner_views.xml`) use no
> tree/list and need **no** change. Chatter (`<div class="oe_chatter">` → `<chatter/>` in 18) is
> NOT used here, so it's a non-issue for this module.

## How we keep both versions

This branch is the **Odoo 17** source of truth (`<tree>`, `17.0.x`). Generate the 18 variant
mechanically rather than hand-maintaining a fork:

```bash
tools/port_to_18.sh          # prints the 2 edits and (with --apply) writes them into a copy
```

When mirroring to a long-lived `18.0` branch (Bosun-style): apply the script's two edits, commit,
done. Because the delta is only these two mechanical changes, the branches never diverge on logic —
keep ALL real changes on 17 and re-port.

## Odoo 19 (branch `19.0`, off `18.0`)

The `<tree>`→`<list>` change is already in the 18 line, so on top of `18.0` the 19 delta is small
and mechanical (grep-verified 2026-07-03 — the module has **no** `.mobile` / `uom` / `res.groups` /
`ir.cron` / `stock.move` / `product.type` / json-controller usage, so none of those guards apply):

1. **Manifest `version` → `19.0.x`** (19 sets `installable=False` unless the major matches).
2. **`name_get` → `_compute_display_name`** (`models/us_tax_category.py`) — 18+ removed `name_get`;
   overriding `_compute_display_name` is dual-version safe (works on 17 too).
3. **search-view `<group>`** (`views/us_tax_rule_views.xml`) — 19 RelaxNG rejects `expand`/`string`
   on a `<group>` inside `<search>`; make it a bare `<group>`. (Form `<group string=…>` is still valid,
   so `res_partner_views.xml` / `us_tax_category_views.xml` need no change.)

Left as-is (deliberate): `_sql_constraints` (3 models) — the list form still works on 19 (deprecated in
favor of `models.Constraint`, low urgency); and `menus.xml` parents on `account.menu_finance_configuration`,
a stable xmlid (NOT the removed `…_miscellaneous`) — rig should confirm it resolves on 19.

**§5 tax/fiscal is a runtime re-test, not a code change** — the checkout-tax engine
(`us_tax_engine.apply_safe`/`apply`, `sale_order.action_confirm`, `account_move.action_post`) must be
re-validated on the 19 rig against seeded carts. Not attempted blind here (no rig access).

## Re-verify on the rigs (s76t)
```bash
# copy module into the rig addons, then (throwaway DB, won't touch the running server):
docker exec bosun-odoo17 odoo -d t17 -i us_sales_tax --db_host bosun-pg --db_user odoo \
  --db_password odoo --test-enable --test-tags /us_sales_tax --stop-after-init \
  --http-port=8224 --gevent-port=8225 --max-cron-threads=0
docker exec bosun-odoo   odoo -d t18 -i us_sales_tax --db_host bosun-pg --db_user odoo \
  --db_password odoo --test-enable --test-tags /us_sales_tax --stop-after-init \
  --http-port=8226 --gevent-port=8227 --max-cron-threads=0
# expect: "0 failed, 0 error(s) of 21 tests". Drop the DBs + remove the module copy after.
```
