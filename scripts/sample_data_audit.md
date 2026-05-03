# Decision-Data Audit — 20260503_231605

**9** actionable row(s) audited against decision-stage data requirements.

## Coverage summary

| Band | Count | % | Meaning |
|---|---|---|---|
| **FULL_DATA** | 0 | 0% | Every required + most optional signals present |
| **LOW_CONFIDENCE** | 0 | 0% | All required present; some optional gaps |
| **PROBE_ONLY** | 9 | 100% | All required present; many optional gaps — probe only |
| **INSUFFICIENT_DATA** | 0 | 0% | MISSING REQUIRED — verdict not trustworthy |

## Per-row audit

| ASIN | verdict | score | overall | stages BLOCK | optional gaps | title |
|---|---|---|---|---|---|---|
| B0DT4SVDYV | WAIT | 82 | **PROBE_ONLY** | - | 12 | TUBBZ XL: How To Train Your Dragon - Toothless Giant Cosplay |
| B01BZ20FE2 | WAIT | 73 | **PROBE_ONLY** | - | 14 | Britains John Deere 6195M Tractor, Multicoloured, 43150 |
| B0FBMJ5D3Z | WAIT | 71 | **PROBE_ONLY** | - | 12 | TUBBZ First Edition: Halloween - Skeleton Glow in the Dark C |
| B0DQQ8WGC8 | NEGOTIATE | 63 | **PROBE_ONLY** | - | 12 | Ravensburger Happy Days No. 8 Holidays - 4x 500 Piece Jigsaw |
| B0FH5HZKSZ | NEGOTIATE | 61 | **PROBE_ONLY** | - | 12 | TUBBZ First Edition: Cyberpunk 2077 - Jackie Welles Cosplayi |
| B0FH5HTQPC | NEGOTIATE | 55 | **PROBE_ONLY** | - | 20 | TUBBZ First Edition: Cyberpunk 2077 - Johnny Silverhand Cosp |
| B0CH1HK3FH | WAIT | 51 | **PROBE_ONLY** | - | 20 | SCHLEICH 14876 Hawskbill Sea Turtle, from 3 years WILD LIFE  |
| B0FH5H6K3B | WAIT | 44 | **PROBE_ONLY** | - | 20 | TUBBZ First Edition: The Witcher - Ciri Cosplaying Rubber Du |
| B0FH5JNDTS | WAIT | 44 | **PROBE_ONLY** | - | 20 | TUBBZ First Edition: The Witcher - Geralt of Rivia Cosplayin |

## Field-coverage rollup

Most-missing **required** fields (gates the verdict):

_All required fields populated on every row._

Most-missing **optional** fields (refines confidence):

| field | missing on N rows | % of total |
|---|---|---|
| `review_velocity_90d` | 9 | 100% |
| `new_fba_price` | 9 | 100% |
| `amazon_price` | 9 | 100% |
| `sales_rank_cv_90d` | 9 | 100% |
| `predicted_velocity_mid` | 9 | 100% |
| `referral_fee_pct` | 9 | 100% |
| `fba_pick_pack_fee` | 9 | 100% |
| `amazon_bb_pct_90` | 9 | 100% |
| `buy_box_seller_stats` | 9 | 100% |
| `bb_drop_pct_90` | 4 | 44% |
| `buy_box_oos_pct_90` | 4 | 44% |
| `buy_box_min_365d` | 4 | 44% |
| `price_volatility_90d` | 4 | 44% |
| `restriction_status` | 1 | 11% |
| `fba_eligible` | 1 | 11% |

## Flagged rows — detail

### B01BZ20FE2 — Britains John Deere 6195M Tractor, Multicoloured, 43150
  - **PROBE_ONLY** | analyst verdict: WAIT | score: 73
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`
  - **validate_opportunity (BUY/SOURCE/NEGOTIATE/WATCH/KILL)** — missing optional: `amazon_bb_pct_90, restriction_status, fba_eligible`

### B0DQQ8WGC8 — Ravensburger Happy Days No. 8 Holidays - 4x 500 Piece Jigsaw
  - **PROBE_ONLY** | analyst verdict: NEGOTIATE | score: 63
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`

### B0CH1HK3FH — SCHLEICH 14876 Hawskbill Sea Turtle, from 3 years WILD LIFE 
  - **PROBE_ONLY** | analyst verdict: WAIT | score: 51
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`
  - **validate_opportunity (BUY/SOURCE/NEGOTIATE/WATCH/KILL)** — missing optional: `amazon_bb_pct_90, buy_box_oos_pct_90, price_volatility_90d`
  - **buyer report (analyst dimensions)** — missing optional: `buy_box_oos_pct_90, price_volatility_90d, buy_box_min_365d, amazon_bb_pct_90, bb_drop_pct_90, buy_box_seller_stats`

### B0FH5HZKSZ — TUBBZ First Edition: Cyberpunk 2077 - Jackie Welles Cosplayi
  - **PROBE_ONLY** | analyst verdict: NEGOTIATE | score: 61
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`

### B0FH5HTQPC — TUBBZ First Edition: Cyberpunk 2077 - Johnny Silverhand Cosp
  - **PROBE_ONLY** | analyst verdict: NEGOTIATE | score: 55
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`
  - **validate_opportunity (BUY/SOURCE/NEGOTIATE/WATCH/KILL)** — missing optional: `amazon_bb_pct_90, buy_box_oos_pct_90, price_volatility_90d`
  - **buyer report (analyst dimensions)** — missing optional: `buy_box_oos_pct_90, price_volatility_90d, buy_box_min_365d, amazon_bb_pct_90, bb_drop_pct_90, buy_box_seller_stats`

### B0FBMJ5D3Z — TUBBZ First Edition: Halloween - Skeleton Glow in the Dark C
  - **PROBE_ONLY** | analyst verdict: WAIT | score: 71
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`

### B0FH5H6K3B — TUBBZ First Edition: The Witcher - Ciri Cosplaying Rubber Du
  - **PROBE_ONLY** | analyst verdict: WAIT | score: 44
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`
  - **validate_opportunity (BUY/SOURCE/NEGOTIATE/WATCH/KILL)** — missing optional: `amazon_bb_pct_90, buy_box_oos_pct_90, price_volatility_90d`
  - **buyer report (analyst dimensions)** — missing optional: `buy_box_oos_pct_90, price_volatility_90d, buy_box_min_365d, amazon_bb_pct_90, bb_drop_pct_90, buy_box_seller_stats`

### B0FH5JNDTS — TUBBZ First Edition: The Witcher - Geralt of Rivia Cosplayin
  - **PROBE_ONLY** | analyst verdict: WAIT | score: 44
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`
  - **validate_opportunity (BUY/SOURCE/NEGOTIATE/WATCH/KILL)** — missing optional: `amazon_bb_pct_90, buy_box_oos_pct_90, price_volatility_90d`
  - **buyer report (analyst dimensions)** — missing optional: `buy_box_oos_pct_90, price_volatility_90d, buy_box_min_365d, amazon_bb_pct_90, bb_drop_pct_90, buy_box_seller_stats`

### B0DT4SVDYV — TUBBZ XL: How To Train Your Dragon - Toothless Giant Cosplay
  - **PROBE_ONLY** | analyst verdict: WAIT | score: 82
  - **calculate (economics)** — missing optional: `new_fba_price, amazon_price, fba_pick_pack_fee, referral_fee_pct`
