# Niche Config -- Sports Goods (Tennis and Badminton Only)

niche_id: sports-goods
sheet_tab: Sports Goods

## SCOPE: Tennis and Badminton ONLY
## All other sports are out of scope -- reject on sight.

## Keepa Product Finder Filters (Skill 1)

bsr_min: 5000
bsr_max: 60000
velocity_min: 50
  (lower floor -- seasonal demand means off-peak products
   may show lower velocity but are still valid targets)
root_category: Sports & Outdoors
include_subcategory: Tennis, Badminton
exclude_subcategory: (none needed -- title keyword handles scope)
title_keyword: tennis###badminton
  (### = OR logic in Keepa -- returns products with tennis OR badminton
   in title. This is the primary scope restriction.)
heat_sensitive: (default)
batteries: (default)

## Hard Exclusion Keywords (Skill 2 pre-filter)

out_of_scope:
  Any product without tennis or badminton in the title that
  somehow passes the keyword filter -- reject immediately.

heavy_keywords:
  goal post, net post, court equipment, large, heavy,
  (check actual product weight for rackets, bags, nets)

## Weight Check -- Critical for This Niche

  Rackets:        check weight -- most are fine (200-300g)
  Racket bags:    check dimensions -- some trigger oversized FBA fees
  Badminton nets: full-size nets can be heavy -- verify FBA fee in Skill 2
  Shuttlecocks:   always fine
  Strings:        always fine
  Grips/overgrips: always fine
  Tennis balls:   fine unless buying very large multipacks

## Seasonal Risk -- High

  This niche peaks strongly in summer (April-August UK).
  A product with BSR 10,000 in July may be 100,000+ in January.

  In Skill 3 scoring:
  - Always check 12-month BSR trend not just 90-day
  - Apply seasonal flag to any product showing summer-only demand
  - Seasonal products score lower on demand (treat as MAYBE not YES)
  - Products with consistent 12-month demand score higher

## Focus Products

Good products to find:
  Tennis rackets (all levels), badminton rackets,
  tennis balls (3/4/6 packs), shuttlecocks (tube packs),
  racket grip tape, overgrips, replacement strings,
  racket bags and holdalls, tennis training aids,
  ball machines (check weight carefully),
  court equipment accessories (towels, wristbands, dampeners)

Avoid even if they pass filters:
  All non-tennis/badminton sports products,
  Heavy court equipment (net posts, fixed equipment),
  Very large racket bags that trigger oversized FBA fees

## Supplier Directories (Skill 4 Route 2)

  Newitts: newitts.com (wholesale sports)
  Continental Sports: continentalsports.co.uk
  Slazenger / Dunlop: search for UK trade contact
  Talbot-Torro UK: talbottorro.com
  Yonex UK: yonex.co.uk (trade enquiries)
  Wilson UK: wilson.com/en-gb (trade)
  Babolat UK: babolat.com (trade)
  Head UK: head.com/en-GB (trade)

## Key Brands to Look For

  Wilson, Babolat, Head, Yonex, Dunlop, Prince,
  Tecnifibre, Talbot-Torro, Victor Badminton, Li-Ning,
  Ashaway, Tourna, Gamma Sports, Slazenger,
  Carlton (badminton), RSL (shuttlecocks)

## Notes

  Velocity floor is 50 (not 100) because off-season products
  genuinely sell slower but are still worth stocking.
  The scoring in Skill 3 will flag seasonal risk -- the lower
  velocity floor just ensures we see them in the first place.
