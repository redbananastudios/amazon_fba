---
name: keepa-product-finder
description: Use Keepa Product Finder to run Amazon product searches from plain-language filters such as marketplace, category, subcategory, sales rank, price, seller count, FBA or Amazon conditions, and text or select fields, then save the generated URL and exported results to a requested local path.
---

# Keepa Product Finder

Use this skill when the user wants a Keepa Product Finder search, export, or saved query URL from plain-language filters.

The basic goal is:

1. translate user wording into the actual Amazon category or subcategory path used by Keepa
2. use Keepa `Category Tree` to confirm the canonical Amazon taxonomy when the category path is not already cached
3. build the Keepa finder URL for all URL-safe fields
4. apply the remaining fields with JS after page load
5. export the results to files

Read [references/keepa-finder-values.md](references/keepa-finder-values.md) before building the query when the request depends on category IDs, subcategory IDs, field keys, operators, or any Keepa enum-like value.

When the request is for a configured niche such as `educational-toys`, prefer the niche-specific cached category map from the reference file over live category discovery.

## Capture Inputs

Accept user wording directly when present. Normalize it into Keepa filters.

Common inputs:

- `marketplace`
- `user category phrase`
- `root category`
- `subcategory`
- `sales rank min`
- `sales rank max`
- `price field`
- `price min`
- `price max`
- `seller count min`
- `seller count max`
- `FBA / FBM / Amazon conditions`
- `brand`
- `manufacturer`
- `model`
- `title`
- `languages`
- `binding`
- `display group`
- `shipping country`
- `rows per export`
- `export all results`
- `output folder`
- `file format`
- `sort`

Reasonable defaults:

- Infer marketplace from currency or wording when possible.
- Use `New: Current` unless the user clearly asks for another price field.
- Use `Current Sales Rank` unless the user clearly asks for an average or historical rank.
- Export `CSV` unless the user asks for another format.
- Prefer one exported page sized to the requested count when Keepa supports that page size.

## Recipes (Named Filter Sets)

Recipes are pre-canned filter sets stored as JSON in `recipes/{name}.json`.
Each recipe encodes one sourcing thesis (Amazon-OOS wholesale, brand scan,
no-rank long-tail, stable-price low-volatility, etc.). Cowork and human
operators invoke recipes by name instead of restating filters every run.

Available recipes:

- `amazon_oos_wholesale` — Amazon abandoned + 2-10 FBA + selling
- `brand_wholesale_scan` — paste brand list, scan their full catalogue
- `no_rank_hidden_gem` — no-rank long-tail with review velocity
- `stable_price_low_volatility` — ±10% Buy Box stability over 90d

### Invocation

```
use $keepa-product-finder recipe=amazon_oos_wholesale category="Toys & Games" output=./output/{run_id}/keepa_amazon_oos.csv
use $keepa-product-finder recipe=brand_wholesale_scan brands="LEGO,Hasbro,Mattel" output=./output/{run_id}/keepa_brand_scan.csv
```

The recipe supplies `url_filters`, `ui_filters`, `sort`, and
`rows_per_page`. The caller must supply:

- `category` if the recipe declares `requires_category: true` (everything
  except `brand_wholesale_scan`)
- `brands` if the recipe declares `requires_brands: true`
  (`brand_wholesale_scan` only)
- `output` — absolute path or `./output/{run_id}/keepa_{recipe}.csv` style
  relative path. The skill creates the parent directory.

If a required input is missing, refuse to run and ask the caller for it.
Do not invent a default category — Keepa's "All categories" returns a
random 10k subset and silently corrupts results.

### Recipe JSON shape

```json
{
  "name": "<recipe_id>",
  "description": "...",
  "thesis": "...",
  "marketplace": "GB",
  "requires_category": true,
  "requires_brands": false,

  "url_filters": {
    "<KeepaFieldKey>": {"filterType": "number", "type": "inRange", "filter": 20, "filterTo": 40}
  },

  "ui_filters": {
    "boolean_no": ["isAdultProduct"],
    "checkboxes": ["AMAZON_outOfStock"],
    "brands_include": "${brands}"
  },

  "sort": [{"colId": "SALES_current", "sort": "asc"}],
  "rows_per_page": 5000,

  "global_exclusions": "auto",

  "calculate_config": {"compute_stability_score": true},
  "decide_overrides": {"min_sales_shortlist": 5}
}
```

- `url_filters` go into the URL hash `f` block per the existing URL-First
  Hybrid Workflow. Keys must be verified Keepa field names — if a key is
  not in `references/keepa-finder-values.md`, resolve it via UI inspection
  on first run and save back.
- `ui_filters` are applied after page load via the JS workflow already
  documented below.
- `${placeholder}` syntax in `ui_filters` (e.g. `"brands_include": "${brands}"`)
  is substituted from invocation args at render time.
- `calculate_config` and `decide_overrides` are NOT applied by this skill —
  they are forwarded to the engine YAML strategy when Cowork dispatches the
  engine task. The skill writes them into `recipe_metadata.json` next to
  the export CSV so the engine can pick them up.

### Global exclusions auto-merge

When `global_exclusions: "auto"` (default in every shipped recipe), the
skill MUST read `shared/config/global_exclusions.yaml` at render time and
merge:

1. If `hazmat_strict: true` → append `"isHazMat"` to `ui_filters.boolean_no`.
2. For each entry in `categories_excluded` → append to a synthesised
   `ui_filters.categories_exclude` list, applied via the
   `autocomplete-categories_exclude` field with `isNoneOf` operator.
3. `title_keywords_excluded` is NOT applied here — it is a post-export
   filter inside the engine's `keepa_finder_csv` step (this skill produces
   the raw export; the engine does the keyword pruning).

If `global_exclusions` is `"none"`, skip the merge entirely (rare —
intended for diagnostic reruns).

### Save-back rule (recipes)

If a recipe references a Keepa field key that is not yet verified in
`references/keepa-finder-values.md`, the skill MUST:

1. Resolve the key via UI inspection on first use (find the matching
   form field, capture its `id`/`name` attribute).
2. Confirm filter behaviour (apply, observe result count change).
3. Save the verified mapping back to `keepa-finder-values.md` per the
   existing save-back rule.

Recipes flag unverified keys in the `notes[]` array — treat any note
mentioning "TBC" or "save-back" as a triggered save-back obligation.

### Output sidecar

Alongside the export CSV, write `recipe_metadata.json` containing:

```json
{
  "recipe": "amazon_oos_wholesale",
  "recipe_path": "fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/amazon_oos_wholesale.json",
  "category": "Toys & Games",
  "brands": null,
  "rendered_url": "https://keepa.com/#!finder/<encoded>",
  "rows_exported": 1234,
  "exported_at": "2026-05-02T10:15:23Z",
  "calculate_config": {"compute_stability_score": true},
  "decide_overrides": null,
  "global_exclusions_applied": {
    "hazmat_strict": true,
    "categories_excluded": ["Clothing, Shoes & Jewellery"]
  }
}
```

The downstream engine step (`keepa_finder_csv`) reads this sidecar to know
which strategy_tag to apply and which engine config to forward.

### Cowork orchestration

When Cowork drives this end-to-end as one workflow, it dispatches two
tasks (see `orchestration/runs/keepa_finder.yaml` for the full definition):

**Task 1 — Discovery prompt:**
> Use `$keepa-product-finder` with `recipe={recipe}` and `category={category}`
> (or `brands={brands}`) to export to `./output/{run_id}/keepa_{recipe}.csv`.
> Write the recipe sidecar metadata. Report rows exported.

**Task 2 — Engine prompt:**
> Run `python -m fba_engine.strategies.runner --strategy fba_engine/strategies/keepa_finder.yaml --context csv_path=./output/{run_id}/keepa_{recipe}.csv recipe={recipe} output_dir=./output/{run_id}/`.
> Report SHORTLIST/REVIEW/REJECT counts.

Each task is independently retryable. Task 2 fails fast if Task 1's output
is missing or empty.

## Field Handling Model

Treat each requested filter as one of three classes:

1. `static select`
2. `plain text`
3. `dynamic lookup`

Use the reference file first. If a needed value is missing, resolve it from the live Keepa UI and then save it back into the reference.

For lookup-backed fields, store enough information to support both URL building and UI population after page load.

Examples:

- `root category` and `subcategory` are `dynamic lookup`
- operator dropdowns are `static select`
- `title` is `plain text`
- fields like `brand`, `manufacturer`, `languages`, `binding`, `item type`, `display group`, and `shipping country` are `dynamic lookup`

Do not guess hidden IDs or enum values.

## Category Translation Layer

Do not treat user category phrases as direct Keepa values.

Translate them in this order:

1. user wording
2. canonical Amazon category path
3. Keepa `Category Tree` confirmation when the path is not already cached
4. Keepa field to populate
5. resolved Keepa value or ID
6. UI search text and expected dropdown match

If Amazon search results or a product breadcrumb provide the canonical category path before Keepa does, use that Amazon path as the source of truth for the lookup entry and then resolve the matching Keepa autocomplete selection from it.

Examples:

- `Toys` -> `Toys & Games`
- `pens and pencils` -> `Stationery & Office Supplies > Pens, Pencils & Writing Supplies`
- niche phrases like `afro hair products` may need a broader root category plus a narrower subcategory or text filter

Save the translated mapping in the reference file so future runs can skip rediscovery.

For `educational-toys`, use the cached include-category set from the reference file directly unless the caller explicitly asks to widen or replace it.

Keepa may normalize the final saved finder URL to resolved values that differ from the counts shown in the autocomplete dropdown text. When that happens, save the post-selection normalized values from the final URL or hidden `autocompleteReal-*` fields.

Do not treat raw Amazon node IDs as reliable Keepa URL values for autocomplete fields. For category and subcategory filters, resolve the Keepa autocomplete selection first and then save the Keepa-resolved value.

### Use Category Tree First For Taxonomy Discovery

When the user gives a loose niche phrase and does not provide the primary category, use `https://keepa.com/#!categorytree` to resolve the real Amazon taxonomy before using Product Finder.

Use `Category Tree` to:

- confirm the canonical Amazon category path
- avoid guessing the wrong root category
- identify the category branch that should be represented in the lookup file

Then use Product Finder to:

- populate the resolved root category and category filters
- apply the rest of the search filters
- run the search and export the results

## URL-First Hybrid Workflow

The Keepa Product Finder URL hash supports only these field classes:

- number filters
- boolean `Yes` values with `filter: 1`
- text or month values like `srAvgMonth`

Everything else must be applied after page load with JavaScript.

### What goes in the URL hash

| Field type | URL hash support | Format |
| --- | --- | --- |
| Number filters | Yes | `{"filterType":"number","type":"inRange|greaterThanOrEqual|lessThanOrEqual","filter":N,"filterTo":N|null}` |
| Boolean = Yes | Yes | `{"filterType":"number","type":"equals","filter":1}` |
| Text/month | Yes | `{"filterType":"text","type":"equals","filter":"202602"}` |

### What needs JS fallback after page load

| Field type | Why URL load fails | JS method |
| --- | --- | --- |
| Boolean = No | Keepa writes `filter: 0` to the hash but silently drops it on load | `document.getElementById('booleanNo-{fieldName}-radio').click()` |
| Checkboxes | Not applied from URL | `document.getElementById('boolean-{TYPE}_{suffix}-checkbox').click()` |
| Product Type checkbox group | Not applied from URL | Uncheck unwanted `productType-{N}-checkbox` values |
| Single Variation checkbox | Keepa writes `hasVariations` but does not read it back | `document.getElementById('boolean-singleVariation-checkbox').click()` |
| Autocomplete fields | Creates unresolved literal chips and returns 0 results | Use jQuery input plus click the dropdown match |
| Dynamic radio | Not written to the hash at all | Click the specific radio |

### Step 1 - Build the URL hash

Build a JSON object with exactly three top-level keys:

```json
{
  "f": {},
  "s": [],
  "t": "f"
}
```

Rules:

- `s` must contain sort objects like `{"colId":"FIELD","sort":"asc"}`
- `t` must be the string `"f"`
- prices use display units, so `20` means `GBP 20`

Include in `f`:

- all number filters
- boolean fields set to Yes with `filter: 1`
- text or month filters

Do not include in `f`:

- boolean No values
- checkbox fields
- product type selections
- single variation
- autocomplete fields
- dynamic radios

Use `JSON.stringify`, then `encodeURIComponent`.

### Step 2 - Trigger Keepa's SPA router correctly

Keepa is a single-page app. A direct hash-only navigation does not reliably trigger the finder reload. Use this sequence:

1. Navigate away with `window.location.href = 'https://keepa.com/#!tracking'`
2. Wait 3 seconds
3. Navigate to `window.location.href = 'https://keepa.com/#!finder/' + encoded`

### Step 3 - Apply all non-URL fields with JavaScript

After the page loads and shows a result count, apply the remaining filters.

#### 3a. Boolean No radios

```javascript
document.getElementById('booleanNo-isHazMat-radio').click();
document.getElementById('booleanNo-batteriesRequired-radio').click();
```

Pattern:

```javascript
document.getElementById('booleanNo-{fieldName}-radio').click();
```

#### 3b. Checkbox fields

Pattern:

```javascript
document.getElementById('boolean-{TYPE}_{suffix}-checkbox').click();
```

Examples:

```javascript
document.getElementById('boolean-AMAZON_outOfStock-checkbox').click();
document.getElementById('boolean-BUY_BOX_SHIPPING_isLowest-checkbox').click();
```

#### 3c. Product Type checkbox group

All three are checked by default. Uncheck unwanted values.

```javascript
document.getElementById('productType-1-checkbox').click();
document.getElementById('productType-2-checkbox').click();
```

That leaves Physical Product only.

#### 3d. Single Variation checkbox

```javascript
document.getElementById('boolean-singleVariation-checkbox').click();
```

#### 3e. Autocomplete fields

Use jQuery to type into the field, wait for the menu, then click the correct item.

```javascript
const input = document.getElementById('autocomplete-rootCategory');
input.focus();
jQuery(input).val('Toys').trigger('input').trigger('keydown');

const items = document.querySelectorAll('.ui-menu-item, li');
for (const item of items) {
  if (item.textContent.includes('Toys & Games')) {
    item.click();
    break;
  }
}
```

Use the same pattern for:

- `rootCategory`
- `categories`
- `categories_exclude`
- `brand`
- `manufacturer`
- all other autocomplete-backed fields listed in the reference file

#### 3f. Dynamic radios

```javascript
document.getElementById('dynamicAmazon-buyBoxSellerIdHistory-radio').click();
document.getElementById('dynamic3rd-buyBoxSellerIdHistory-radio').click();
```

Wait briefly after each JS step so Keepa can update the result count.

## Resolution Workflow

When the caller passes plain text for a value that may need a Keepa ID:

1. Check [references/keepa-finder-values.md](references/keepa-finder-values.md) for an exact or alias match.
2. If no verified mapping exists, translate the user wording into the nearest canonical Amazon category path.
3. Open Product Finder and locate the matching Keepa control.
4. For autocomplete-backed fields, type the translated value and inspect the suggestion text.
5. Apply the field with the verified dropdown match.
6. Record the Amazon path, resolved field key, operator, and value in the reference file.
7. Reuse that resolved value in future runs.

Use this workflow for:

- root categories
- subcategories
- display groups
- website display groups
- brands
- manufacturers
- models
- bindings
- languages
- shipping countries
- any other autocomplete-backed field

If multiple plausible matches exist, choose the closest Keepa match and state the assumption.

When saving lookup data, keep:

- plain-language input
- canonical Amazon category path when relevant
- resolved field key
- operator
- resolved value or ID
- UI search text
- expected dropdown match text when relevant

## Finder JSON Structure

The finder URL hash decodes to:

```json
{
  "f": { "...": "..." },
  "s": [ { "colId": "FIELD", "sort": "asc" } ],
  "t": "f"
}
```

Filter shapes:

Number range:

```json
"SALES_current": {"filterType":"number","type":"inRange","filter":1111,"filterTo":2222}
```

Number minimum:

```json
"NEW_avg90": {"filterType":"number","type":"greaterThanOrEqual","filter":2,"filterTo":null}
```

Number maximum:

```json
"SALES_avg90": {"filterType":"number","type":"lessThanOrEqual","filter":100000,"filterTo":null}
```

Boolean Yes:

```json
"hasAPlus": {"filterType":"number","type":"equals","filter":1}
```

Text or month:

```json
"srAvgMonth": {"filterType":"text","type":"equals","filter":"202602"}
```

## Export And Capture Workflow

Prefer file export over scraping. The final output should be export files, not just a populated page.

1. Build or open the encoded finder URL.
2. Wait for the result grid to settle.
3. Apply any JS fallback fields.
4. Wait for the final result count to settle again.
5. Set rows per page to the requested export size or the closest larger supported size.
6. Export the current page.
7. Save or move the downloaded file to the requested destination.
8. Also save the encoded finder URL to a text file when the user wants a reusable query.

Keepa exports only the currently displayed page. If the user wants more than one page or any result count larger than one page can hold:

- export multiple pages when practical
- name the files clearly
- tell the user whether the output is one page or multiple pages
- continue page by page until the requested count is satisfied or Keepa has no more pages

Use Playwright table capture only when:

- export is unavailable
- export fails
- the user explicitly asks for scraped rows

When falling back to capture:

- capture the visible table rows after the grid settles
- save them as CSV or JSON locally
- state that the output is a fallback capture, not the native Keepa export

## Save-Back Rule

Any newly verified Keepa mapping discovered during a task must be written back to [references/keepa-finder-values.md](references/keepa-finder-values.md).

Save back:

- root category IDs
- subcategory IDs
- normalized field keys
- operator values
- static select values
- dynamic lookup mappings
- aliases that helped plain-language matching

Do not leave newly discovered Keepa values only in conversation history.

## Output Expectations

When returning a successful run, include:

- the generated encoded Keepa URL
- the key filters used
- the exported row count
- the total matching result count when visible
- the saved file path
- any assumptions or closest-match category resolution

When the skill performs a lookup-only run, include:

- the plain-language input
- the resolved Keepa field key
- the resolved ID or value
- the reference file update location

## Keepa-Specific Notes

- A plain website POST endpoint for finder results was not verified.
- The stable non-API path is the finder hash URL inside the logged-in browser session plus JS fallback after page load.
- Native Keepa export is preferred over scraping the visible grid.

## Example Prompts

- Use `$keepa-product-finder` to interpret `Kids Toys`, resolve the correct category or subcategory IDs, build the finder URL, apply the remaining UI-only fields, and export 1000 results to `./exports`.
- Use `$keepa-product-finder` to search Amazon.co.uk for products with price GBP 20 to GBP 30, 1 to 5 sellers, FBA only, no Amazon, then save the query URL and CSV to `./test`.
- Use `$keepa-product-finder` to resolve plain-text values for `Brand`, `Languages`, and `Binding`, add the verified mappings to the reference file, and then run the export.

### Recipe-driven (Cowork)

- Use `$keepa-product-finder` with `recipe=amazon_oos_wholesale` and `category="Toys & Games"`, export to `./output/2026-05-02/keepa_amazon_oos.csv`. Apply global exclusions auto-merge. Report rows exported and write `recipe_metadata.json` next to the CSV.
- Use `$keepa-product-finder` with `recipe=brand_wholesale_scan` and `brands="LEGO,Hasbro,Mattel,Schleich"`, export to `./output/2026-05-02/keepa_brand_scan.csv`.
- Use `$keepa-product-finder` with `recipe=stable_price_low_volatility` and `category="Pet Supplies"`, export to `./output/2026-05-02/keepa_stable.csv`.
