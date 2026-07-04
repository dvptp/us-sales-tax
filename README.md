# US Sales Tax (Midas) for Odoo

Free, **rooftop-accurate California** sales tax for Odoo Community — a maintained, CE-native
replacement for the removed native TaxCloud integration.

- **Free (this module):** the full California rate table (state + every county + city, from the
  quarterly CDTFA snapshot) bundled locally — correct *combined* rates computed on your own server,
  zero API calls. Product-level taxability (food/exemption-aware), resale certificates, and a
  fail-safe engine that never breaks checkout.
- **Paid (hosted):** switch the provider to *Hosted* and add a Practical Business Machines API key
  for all other states, always-current rates, and the compliance tier (economic-nexus alerts,
  exemption-certificate vault, filing-ready reports). Flat monthly plans. Missing key → quietly
  falls back to the bundled California data.

**Best-effort data — not tax advice.** PBM makes no guarantee that rates are correct or current;
you remain responsible for the tax you collect and remit. Full disclaimer in the module.

License **LGPL-3**. A Practical Business Machines product · https://practicalbusinessmachines.com

> Odoo App Store registration uses the series branch: **`#19.0`**.
