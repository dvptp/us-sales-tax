#!/usr/bin/env bash
# Generate the Odoo-18 variant of us_sales_tax from this (Odoo-17) source.
# The entire 17->18 delta is two mechanical changes (see PORTING-17-18.md):
#   1. manifest version 17.0.x -> 18.0.x
#   2. list views: <tree>/</tree> -> <list>/</list> and view_mode tree[,form] -> list[,form]
#
# Usage:
#   tools/port_to_18.sh            # dry-run: show what would change
#   tools/port_to_18.sh --apply    # write changes IN PLACE (run on a copy / the 18.0 branch)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"   # the module root
APPLY=""
[ "${1:-}" = "--apply" ] && APPLY=1

VIEWS=(views/us_tax_rate_views.xml views/us_tax_rule_views.xml
       views/us_tax_category_views.xml views/us_tax_transaction_log_views.xml)

cd "$HERE"
echo "module: $HERE"
echo "1) __manifest__.py version 17.0.* -> 18.0.*"
echo "2) ${VIEWS[*]}: <tree>-><list>, view_mode tree->list"

if [ -z "$APPLY" ]; then
  echo "(dry-run; pass --apply to write)"; exit 0
fi

sed -i 's/"version": "17\./"version": "18./' __manifest__.py
for f in "${VIEWS[@]}"; do
  sed -i \
    -e 's|<tree|<list|g' \
    -e 's|</tree>|</list>|g' \
    -e 's|<field name="view_mode">tree</field>|<field name="view_mode">list</field>|g' \
    -e 's|<field name="view_mode">tree,form</field>|<field name="view_mode">list,form</field>|g' \
    "$f"
done
echo "applied. version now: $(grep -m1 version __manifest__.py)"
