{
    "name": "US Sales Tax",
    "version": "19.0.1.0.0",
    "summary": "Automatic US sales tax with product-level taxability "
               "(food/exemption-aware) and a fail-safe, coupon-aware engine.",
    "description": """
US Sales Tax
============

A maintained, Odoo-CE-native replacement for the (removed) native TaxCloud
integration. Computes US sales tax with real **product-level taxability** -- the
piece that makes food/grocery exemptions correct -- via a data-driven
category x state rule matrix.

Design goals (the things the dead community ``*_taxcloud_tc`` modules get wrong):

* **Never crash checkout.** The compute path is fail-safe: on any engine error it
  falls back per a configurable mode (fail-open = $0 + flagged, recommended).
* **Coupon/discount aware.** Reward lines are taxed by their own taxability
  category, so a free-shipping or discount reward never breaks tax compute.
* **Correct exemptions.** Snack food is exempt in CA (Prop 163, 1992); the rule
  matrix encodes that instead of taxing everything as general tangible goods.

Free tier (bundled, local, no API): ships the **full rooftop California** rate table
(state + every county + city, from the quarterly CDTFA snapshot) plus the product
taxability matrix. A California seller gets correct combined rates offline, at no cost.
Merchants can hand-add other single-state rates.

Paid tier (hosted): point the module at Practical Business Machines' hosted engine with
an API key for **all other states, always-current rates, and the compliance tier**
(economic-nexus alerts, exemption-certificate vault, filing-ready reports).

**Best-effort data — not tax advice.** PBM refreshes rates quarterly on a best-effort
basis but is not a source of truth and makes no guarantee that rates are correct or
current. You remain responsible for the tax you collect, report and remit; verify with a
qualified tax professional. PBM is not liable for under/over-collection, penalties,
interest, or filing errors.
""",
    "author": "Practical Business Machines",
    "website": "https://practicalbusinessmachines.com",
    "license": "LGPL-3",
    "category": "Accounting/Taxes",
    "images": ["static/description/icon.png"],
    "depends": ["account", "sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/us_tax_category_data.xml",
        "data/us_tax_rule_ca_data.xml",
        "data/us.tax.rate.csv",
        "views/us_tax_category_views.xml",
        "views/us_tax_rule_views.xml",
        "views/us_tax_rate_views.xml",
        "views/product_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
        "views/menus.xml",
        "views/us_tax_transaction_log_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": True,
}
