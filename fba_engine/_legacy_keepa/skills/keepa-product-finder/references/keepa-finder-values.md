# Keepa Finder Values

Use this reference when building finder URLs from user filters and when resolving UI-backed IDs.

Any verified Keepa value that uses an internal ID or a Keepa-specific normalized field value should be added here after discovery. Do not leave newly discovered mappings only in conversation history.

## How To Maintain This File

When a live Keepa lookup resolves a new value, append it to the appropriate section below.

Store:

- plain-language input
- canonical Amazon category path when relevant
- resolved field key
- resolved Keepa value or ID
- operator when relevant
- UI search text when relevant
- expected dropdown match text when relevant
- marketplace scope when relevant
- short note if the mapping is alias-based

Use this file as a cache for future runs and to support both URL construction and UI population after page load.

## Category Translation Rule

Translate user category phrases into the actual Amazon category or subcategory path used on Amazon.co.uk before resolving the Keepa field.

Store both:

- the user's phrase
- the canonical Amazon path

Then store:

- the Keepa field used
- the resolved Keepa value or ID
- the UI search text
- the expected dropdown match

This allows the skill to map user language to Amazon taxonomy first and Keepa controls second.

Use `https://keepa.com/#!categorytree` as the primary discovery step when the category path is not already cached in this file.

## Verified URL Pattern

Direct finder navigation uses:

```text
https://keepa.com/#!finder/{url-encoded-json}
```

The logged-in browser session then loads the results inside Product Finder.

## Verified Finder JSON Structure

The finder URL hash decodes to JSON with three top-level keys:

```json
{
  "f": { "...": "..." },
  "s": [ {"colId": "FIELD", "sort": "asc|desc"} ],
  "t": "f"
}
```

Critical format rules:

- `s` entries are objects like `{"colId":"...","sort":"..."}`
- `t` is always the string `"f"`
- price values use display units, so `GBP 20` is `20`, not `2000`
- the API query view multiplies prices by 100, but the URL hash does not
- SPA navigation must go to `#!tracking`, wait 3 seconds, then load `#!finder/{hash}`

## Verified URL-Hash Compatibility

### Works via URL hash

| Type | Format | Notes |
| --- | --- | --- |
| Number filters | `{"filterType":"number","type":"inRange|greaterThanOrEqual|lessThanOrEqual","filter":N,"filterTo":N|null}` | All number fields work |
| Boolean = Yes | `{"filterType":"number","type":"equals","filter":1}` | Radio visually loads as Yes |
| Text or month | `{"filterType":"text","type":"equals","filter":"202602"}` | Loads correctly |

### Does not work via URL hash and needs JS fallback

| Type | Why URL load fails | JS fallback |
| --- | --- | --- |
| Boolean = No | Keepa writes `filter: 0` but silently drops it on load | `document.getElementById('booleanNo-{fieldName}-radio').click()` |
| Checkboxes | Not applied from URL | `document.getElementById('boolean-{TYPE}_{suffix}-checkbox').click()` |
| Product Type checkbox group | Not applied from URL | Uncheck unwanted `productType-{N}-checkbox` values |
| Single Variation checkbox | Keepa writes `hasVariations` but does not read it back | `document.getElementById('boolean-singleVariation-checkbox').click()` |
| Autocomplete fields | Creates unresolved literal chips and returns 0 results | Use jQuery input plus click the dropdown match |
| Dynamic radio | Not written to the hash at all | Click the specific radio |

## Verified Filter Keys

- `SALES_current`, `SALES_avg30`, `SALES_avg90`, `SALES_avg180`, `SALES_avg365`
- `BUY_BOX_SHIPPING_current`, `BUY_BOX_SHIPPING_avg30`, `BUY_BOX_SHIPPING_avg90`
- `NEW_current`, `NEW_avg30`, `NEW_avg90`
- `NEW_FBA_current`, `NEW_FBA_avg30`, `NEW_FBA_avg90`
- `AMAZON_current`, `AMAZON_avg90`
- `COUNT_NEW_current`, `COUNT_NEW_avg90`
- `COUNT_NEW_FBA_current`, `COUNT_NEW_FBA_avg90`
- `USED_NEW_SHIPPING_avg90`, `USED_ACCEPTABLE_SHIPPING_avg90`
- `BUY_BOX_USED_SHIPPING_avg90`
- `rootCategory`, `categories`, `categories_exclude`
- `srAvgMonth`
- `monthlySold`, `salesRankDrops30`, `salesRankDrops90`
- `AMAZON_outOfStock`
- `fbaFees`, `totalOfferCount`, `RATING_current`, `COUNT_REVIEWS_current`
- `imageCount`, `variationCount`, `videoCount`

## Verified Sort Columns

- `SALES_current`
- `monthlySold`

## Verified Filter Shapes

Number range:

```json
{"filterType":"number","type":"inRange","filter":1111,"filterTo":2222}
```

Number minimum:

```json
{"filterType":"number","type":"greaterThanOrEqual","filter":2,"filterTo":null}
```

Number maximum:

```json
{"filterType":"number","type":"lessThanOrEqual","filter":20000,"filterTo":null}
```

Boolean Yes:

```json
{"filterType":"number","type":"equals","filter":1}
```

Text or month:

```json
{"filterType":"text","type":"equals","filter":"202602"}
```

Autocomplete field shape used by Keepa:

```json
{"filterType":"autocomplete","filter":"8578740","type":"isOneOf"}
```
This shape is valid Keepa data, but autocomplete filters must be applied with JS after page load rather than relying on URL load.

## Verified Working URL Example

```text
https://keepa.com/#!finder/%7B%22f%22%3A%7B%22srAvgMonth%22%3A%7B%22filterType%22%3A%22text%22%2C%22type%22%3A%22equals%22%2C%22filter%22%3A%22202602%22%7D%2C%22SALES_current%22%3A%7B%22filterType%22%3A%22number%22%2C%22type%22%3A%22inRange%22%2C%22filter%22%3A1111%2C%22filterTo%22%3A2222%7D%7D%2C%22s%22%3A%5B%7B%22colId%22%3A%22SALES_current%22%2C%22sort%22%3A%22asc%22%7D%5D%2C%22t%22%3A%22f%22%7D
```

Decoded:

```json
{
  "f": {
    "srAvgMonth": {"filterType":"text","type":"equals","filter":"202602"},
    "SALES_current": {"filterType":"number","type":"inRange","filter":1111,"filterTo":2222}
  },
  "s": [{"colId":"SALES_current","sort":"asc"}],
  "t": "f"
}
```

## Verified IDs And Values

- `Toys & Games` root category autocomplete ID: `8578740`
- historical sales-rank month format: `YYYYMM`
- result page sizes: `5`, `20`, `100`, `500`, `1000`, `2000`, `5000`
- Amazon.co.uk domain ID: `2`
- Amazon.co.uk Buy Box seller IDs: `A3P5ROKL5A1OLE`, `AZH2GF8Z5J95G`

## Verified Plain-Language Mappings

- marketplace: `Amazon.co.uk`
  - plain text: `Kids Toys`
  - canonical Amazon category path: `Toys & Games`
  - resolved field: `rootCategory`
  - resolved value: `8578740`
  - operator: `isOneOf`
  - UI search text: `Toys`
  - expected dropdown match: `Toys & Games (8578740)`
  - note: mapped to `Toys & Games`

- marketplace: `Amazon.co.uk`
  - plain text: `Educational Toys`
  - canonical Amazon category path: `Toys & Games > Learning & Education`
  - resolved field: `rootCategory` + `categories`
  - resolved value: `8578740`
  - operator: `isOneOf`
  - UI search text: `Toys`
  - expected dropdown match: `Toys & Games (8578740)`
  - note: use the educational-toys specific include-category map below instead of a root-only match

- marketplace: `Amazon.co.uk`
  - plain text: `1 - 5 sellers`
  - resolved field: `COUNT_NEW_current`
  - resolved filter shape: number `inRange`
  - note: total current new offer count

- marketplace: `Amazon.co.uk`
  - plain text: `FBA`
  - resolved field: `COUNT_NEW_FBA_current`
  - resolved filter shape: number `inRange`
  - note: used to require at least one FBA seller and cap FBA seller count when requested

- marketplace: `Amazon.co.uk`
  - plain text: `No Amazon`
  - resolved field: `AMAZON_outOfStock`
  - note: apply with the `boolean-AMAZON_outOfStock-checkbox` JS fallback

- marketplace: `Amazon.co.uk`
  - plain text: `pens and pencils`
  - canonical Amazon category path: `Stationery & Office Supplies > Pens, Pencils & Writing Supplies`
  - resolved field: `rootCategory` + `categories`
  - operator: `isOneOf`
  - UI search text: `Stationery` for root category, `Pens, Pencils` for include categories
  - expected dropdown match: `Stationery & Office Supplies (7804929)` and `Stationery & Office Supplies » Pens, Pencils & Writing Supplies (727858)`
  - resolved value: `rootCategory = 192413031`, `categories = 197748031`
  - note: Keepa's final normalized finder URL used different resolved values than the dropdown display counts

- marketplace: `Amazon.co.uk`
  - plain text: `bicycle bell`
  - canonical Amazon category path: `Sports & Outdoors > Sports > Cycling > Accessories > Bells`
  - resolved field: `rootCategory` + `categories`
  - operator: `isOneOf`
  - UI search text: `Sports` for root category, `Bells` for include categories
  - expected dropdown match: `Sports & Outdoors (16071944)` and `Sports » Cycling » Accessories » Bells (38523)`
  - resolved value: `rootCategory = 192413031###318949011`, `categories = 197748031###550000011`
  - note: Keepa's final normalized finder URL used different resolved values than the dropdown display counts

- marketplace: `Amazon.co.uk`
  - plain text: `cluster eye lash trays`
  - canonical Amazon category path: `Beauty > Tools & Accessories > Make-up Brushes & Tools > Eyes > False Eyelashes & Adhesives > False Lashes`
  - resolved field: `rootCategory` + `categories`
  - operator: `isOneOf`
  - UI search text: `Beauty` for root category, `False Lashes` for include categories
  - expected dropdown match: resolve from Keepa autocomplete using the canonical Amazon path
  - note: Amazon product breadcrumbs confirmed the category path; raw Amazon node IDs should not be treated as reliable Keepa URL values for autocomplete fields

## Educational Toys Specific Map

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - root category display: `Toys & Games`
  - root category field: `rootCategory`
  - root category operator: `isOneOf`
  - root category UI search text: `Toys`
  - root category expected dropdown match: `Toys & Games (8578740)`
  - note: use this cached set directly for educational-toys runs instead of rediscovering subcategories

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning & Education`
  - canonical Amazon category path: `Toys & Games > Learning & Education`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `364115031`
  - UI search text: `Learning & Education`
  - expected dropdown match: `Toys & Games »Learning & Education (226011)`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning Resources Educational Toys`
  - canonical Amazon category path: `Toys & Games > Learning & Education > Learning Resources Educational Toys`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `12151363031`
  - UI search text: `Learning Resources Educational Toys`
  - expected dropdown match: `Learning Resources Educational Toys`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning Resources STEAM Toys`
  - canonical Amazon category path: `Toys & Games > Learning & Education > Learning Resources STEAM Toys`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `12725896031`
  - UI search text: `Learning Resources STEAM Toys`
  - expected dropdown match: `Learning Resources STEAM Toys`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning Resources--Numeracy`
  - canonical Amazon category path: `Toys & Games > Learning & Education > Learning Resources--Numeracy`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `2654947031`
  - UI search text: `Learning Resources`
  - expected dropdown match: `Learning Resources--Numeracy`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning Systems`
  - canonical Amazon category path: `Toys & Games > Learning & Education > Electronic Learning Toys > Learning Systems`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `26901079031`
  - UI search text: `Learning Systems`
  - expected dropdown match: `Toys & Games » Learning & Education » Electronic Learning Toys »Learning Systems (4273)`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning System Cartridges`
  - canonical Amazon category path: `Toys & Games > Learning & Education > Electronic Learning Toys > Learning System Cartridges`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `26901078031`
  - UI search text: `Learning System Cartridges`
  - expected dropdown match: `Toys & Games » Learning & Education » Electronic Learning Toys »Learning System Cartridges (318)`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning Computers`
  - canonical Amazon category path: `Toys & Games > Learning Computers`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `3591963031`
  - UI search text: `Learning Computers`
  - expected dropdown match: `Learning Computers`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Learning Areas`
  - canonical Amazon category path: `Toys & Games > Learning Areas`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `2043324031`
  - UI search text: `Learning Areas`
  - expected dropdown match: `Learning Areas`

- marketplace: `Amazon.co.uk`
  - niche: `educational-toys`
  - include category display: `Educational Games`
  - canonical Amazon category path: `Toys & Games > Educational Games`
  - resolved field: `categories`
  - operator: `isOneOf`
  - resolved value: `2097063031`
  - UI search text: `Educational Games`
  - expected dropdown match: `Educational Games`

## Boolean Radio Fields

HTML radio group name: `boolean-{fieldName}`

URL hash key: strip the `boolean-` prefix from the radio group name.

Radio IDs:

- `booleanAll-{fieldName}-radio`
- `booleanYes-{fieldName}-radio`
- `booleanNo-{fieldName}-radio`

Values:

- `-1` = All
- `1` = Yes
- `0` = No

URL hash behavior:

- `filter: 1` works
- `filter: 0` is silently dropped on load

JS fallback for No:

```javascript
document.getElementById('booleanNo-{fieldName}-radio').click();
```

### Complete boolean field list

| URL hash key | Radio group name | Label |
| --- | --- | --- |
| `buyBoxIsFBA` | `boolean-buyBoxIsFBA` | Is FBA |
| `buyBoxIsUnqualified` | `boolean-buyBoxIsUnqualified` | Unqualified |
| `buyBoxIsPreorder` | `boolean-buyBoxIsPreorder` | Pre-order |
| `buyBoxIsBackorder` | `boolean-buyBoxIsBackorder` | Back-order |
| `buyBoxIsPrimeExclusive` | `boolean-buyBoxIsPrimeExclusive` | Prime exclusive |
| `buyBoxIsPrimeEligible` | `boolean-buyBoxIsPrimeEligible` | Prime Eligible |
| `isSNS` | `boolean-isSNS` | Subscribe & Save |
| `buyBoxUsed.isFBA` | `boolean-buyBoxUsed.isFBA` | Buy Box Used Is FBA |
| `hasParentASIN` | `boolean-hasParentASIN` | Has Parent ASIN / Is Variation |
| `isAmazonRenewed` | `boolean-isAmazonRenewed` | Amazon Renewed |
| `hasMainVideo` | `boolean-hasMainVideo` | Has Main Video |
| `hasAPlus` | `boolean-hasAPlus` | Has A+ Content |
| `hasAPlusFromManufacturer` | `boolean-hasAPlusFromManufacturer` | A+ From Manufacturer |
| `batteriesRequired` | `boolean-batteriesRequired` | Batteries Required |
| `batteriesIncluded` | `boolean-batteriesIncluded` | Batteries Included |
| `isHazMat` | `boolean-isHazMat` | Is HazMat |
| `isHeatSensitive` | `boolean-isHeatSensitive` | Is heat sensitive |
| `isAdultProduct` | `boolean-isAdultProduct` | Adult Product |
| `isMerchOnDemand` | `boolean-isMerchOnDemand` | Is Merch on Demand |
| `isEligibleForTradeIn` | `boolean-isEligibleForTradeIn` | Trade-In Eligible |

## Checkbox Fields By Price Type

HTML checkbox ID pattern:

```text
boolean-{TYPE}_{suffix}-checkbox
```

HTML checkbox name pattern:

```text
boolean-{TYPE}_{suffix}
```

Suffixes:

- `outOfStock`
- `isLowest`
- `isLowest90`

URL hash behavior:

- does not work

JS fallback:

```javascript
document.getElementById('boolean-{TYPE}_{suffix}-checkbox').click();
```

Verified checkbox names:

- `boolean-SALES_outOfStock`, `boolean-SALES_isLowest`, `boolean-SALES_isLowest90`
- `boolean-BUY_BOX_SHIPPING_outOfStock`, `boolean-BUY_BOX_SHIPPING_isLowest`, `boolean-BUY_BOX_SHIPPING_isLowest90`
- `boolean-AMAZON_outOfStock`, `boolean-AMAZON_isLowest`, `boolean-AMAZON_isLowest90`
- `boolean-NEW_outOfStock`, `boolean-NEW_isLowest`, `boolean-NEW_isLowest90`
- `boolean-NEW_FBA_outOfStock`, `boolean-NEW_FBA_isLowest`, `boolean-NEW_FBA_isLowest90`
- `boolean-NEW_FBM_SHIPPING_outOfStock`, `boolean-NEW_FBM_SHIPPING_isLowest`, `boolean-NEW_FBM_SHIPPING_isLowest90`
- `boolean-PRIME_EXCL_outOfStock`, `boolean-PRIME_EXCL_isLowest`, `boolean-PRIME_EXCL_isLowest90`

## Product Type Checkbox Group

Checkbox IDs:

- `productType-0-checkbox` = Physical Product
- `productType-1-checkbox` = Digital Product
- `productType-2-checkbox` = eBooks

All are checked by default.

URL hash behavior:

- does not work

JS fallback:

```javascript
document.getElementById('productType-1-checkbox').click();
document.getElementById('productType-2-checkbox').click();
```

That leaves Physical Product only.

## Single Variation Checkbox

- HTML checkbox ID: `boolean-singleVariation-checkbox`
- HTML checkbox name: `boolean-singleVariation`
- label: `Show only one variation per product`
- URL hash key written by Keepa: `hasVariations`

URL hash behavior:

- does not work on load

JS fallback:

```javascript
document.getElementById('boolean-singleVariation-checkbox').click();
```

## Buy Box Seller Dynamic Radio

HTML radio name:

```text
dynamic-buyBoxSellerIdHistory
```

Radio IDs and values:

| Radio ID | Value | Meaning |
| --- | --- | --- |
| `dynamicAll-buyBoxSellerIdHistory-radio` | `-1` | All |
| `dynamicAmazon-buyBoxSellerIdHistory-radio` | `A3P5ROKL5A1OLE,AZH2GF8Z5J95G` | Amazon |
| `dynamic3rd-buyBoxSellerIdHistory-radio` | `-A3P5ROKL5A1OLE,-AZH2GF8Z5J95G` | 3rd Party |
| `dynamicAnyOf-buyBoxSellerIdHistory-radio` | `1` | Any of |

Text input for `Any of`:

```text
dynamicAnyOfDetail-buyBoxSellerIdHistory
```

URL hash behavior:

- not written to the hash

JS fallback:

```javascript
document.getElementById('dynamicAmazon-buyBoxSellerIdHistory-radio').click();
```

## Buy Box Used Seller Dynamic Radio

HTML radio name:

```text
dynamicUsed-buyBoxUsed.seller
```

This follows the same dynamic-radio pattern as `buyBoxSellerIdHistory`.

## Availability Amazon Checkbox Group

Checkbox name:

```text
set-availabilityAmazon
```

All are checked by default.

| Checkbox ID | Value | Label |
| --- | --- | --- |
| `availabilityAmazon--1-checkbox` | `-1` | no Amazon offer exists |
| `availabilityAmazon-0-checkbox` | `0` | Amazon offer is in stock and shippable |
| `availabilityAmazon-1-checkbox` | `1` | Amazon offer is a pre-order |
| `availabilityAmazon-2-checkbox` | `2` | Amazon offer availability is unknown |
| `availabilityAmazon-3-checkbox` | `3` | Amazon offer is back-ordered |
| `availabilityAmazon-4-checkbox` | `4` | Amazon offer shipping is delayed |

JS fallback:

```javascript
document.getElementById('availabilityAmazon-0-checkbox').click();
```

Use the same pattern for the other availability values.

## Buy Box Used Condition Checkbox Group

Checkbox name:

```text
set-buyBoxUsed.condition
```

Checkbox IDs:

- `buyBoxUsed.condition-2-checkbox`
- `buyBoxUsed.condition-3-checkbox`
- `buyBoxUsed.condition-4-checkbox`
- `buyBoxUsed.condition-5-checkbox`

## Verified Select IDs

- `singleChoice-srAvgMonth`
- `autocompleteType-rootCategory`
- `autocompleteType-salesRankDisplayGroup`
- `autocompleteType-websiteDisplayGroupName`
- `autocompleteType-websiteDisplayGroup`
- `autocompleteType-type`
- `autocompleteType-manufacturer`
- `autocompleteType-brand`
- `autocompleteType-brandStoreName`
- `autocompleteType-brandStoreUrlName`
- `autocompleteType-model`
- `autocompleteType-color`
- `autocompleteType-size`
- `autocompleteType-unitCount.unitType`
- `autocompleteType-scent`
- `autocompleteType-itemForm`
- `autocompleteType-pattern`
- `autocompleteType-style`
- `autocompleteType-material`
- `autocompleteType-itemTypeKeyword`
- `autocompleteType-targetAudienceKeyword`
- `autocompleteType-edition`
- `autocompleteType-format`
- `autocompleteType-author`
- `autocompleteType-binding`
- `autocompleteType-languages`
- `textArrayType-partNumber`
- `autocompleteType-buyBoxShippingCountry`
- `textArrayType-sellerIds`
- `textArrayType-sellerIdsFBA`
- `textArrayType-sellerIdsFBM`
- `textArrayType-sellerIdsLowestFBA`
- `textArrayType-sellerIdsLowestFBM`

## Autocomplete Fields - jQuery Method

Autocomplete fields cannot be set via the URL hash. They must be set after page load using jQuery.

```javascript
const input = document.getElementById('autocomplete-{fieldName}');
input.focus();
jQuery(input).val('{search text}').trigger('input').trigger('keydown');

const items = document.querySelectorAll('.ui-menu-item, li');
for (const item of items) {
  if (item.textContent.includes('{expected match}')) {
    item.click();
    break;
  }
}
```

Verified autocomplete field IDs:

- `autocomplete-rootCategory`
- `autocomplete-categories`
- `autocomplete-categories_exclude`
- `autocomplete-brand`
- `autocomplete-manufacturer`

Verified autocomplete values:

- `Toys` -> `Toys & Games (8578740)`

## Static Select Values

### `singleChoice-srAvgMonth`

- `202602` -> `Feb 2026`
- `202601` -> `Jan 2026`
- `202512` -> `Dec 2025`
- `202511` -> `Nov 2025`
- `202510` -> `Oct 2025`
- `202509` -> `Sep 2025`
- `202508` -> `Aug 2025`
- `202507` -> `Jul 2025`
- `202506` -> `Jun 2025`
- `202505` -> `May 2025`
- `202504` -> `Apr 2025`
- `202503` -> `Mar 2025`
- `202502` -> `Feb 2025`
- `202501` -> `Jan 2025`
- `202412` -> `Dec 2024`
- `202411` -> `Nov 2024`
- `202410` -> `Oct 2024`
- `202409` -> `Sep 2024`
- `202408` -> `Aug 2024`
- `202407` -> `Jul 2024`
- `202406` -> `Jun 2024`
- `202405` -> `May 2024`
- `202404` -> `Apr 2024`
- `202403` -> `Mar 2024`
- `202402` -> `Feb 2024`
- `202401` -> `Jan 2024`
- `202312` -> `Dec 2023`
- `202311` -> `Nov 2023`
- `202310` -> `Oct 2023`
- `202309` -> `Sep 2023`
- `202308` -> `Aug 2023`
- `202307` -> `Jul 2023`
- `202306` -> `Jun 2023`
- `202305` -> `May 2023`
- `202304` -> `Apr 2023`
- `202303` -> `Mar 2023`

### Common autocomplete operator selects

- `autocompleteType-rootCategory`
- `autocompleteType-salesRankDisplayGroup`
- `autocompleteType-websiteDisplayGroupName`
- `autocompleteType-websiteDisplayGroup`
- `autocompleteType-type`
- `autocompleteType-manufacturer`
- `autocompleteType-brand`
- `autocompleteType-brandStoreName`
- `autocompleteType-brandStoreUrlName`
- `autocompleteType-model`
- `autocompleteType-color`
- `autocompleteType-size`
- `autocompleteType-unitCount.unitType`
- `autocompleteType-scent`
- `autocompleteType-itemForm`
- `autocompleteType-pattern`
- `autocompleteType-style`
- `autocompleteType-material`
- `autocompleteType-itemTypeKeyword`
- `autocompleteType-targetAudienceKeyword`
- `autocompleteType-edition`
- `autocompleteType-format`
- `autocompleteType-author`
- `autocompleteType-binding`
- `autocompleteType-languages`
- `textArrayType-partNumber`
- `autocompleteType-buyBoxShippingCountry`

Shared values:

- `isOneOf` -> `Is one of`
- `isNoneOf` -> `Is none of`

### Seller array operator selects

- `textArrayType-sellerIds`
- `textArrayType-sellerIdsFBA`
- `textArrayType-sellerIdsFBM`
- `textArrayType-sellerIdsLowestFBA`
- `textArrayType-sellerIdsLowestFBM`

Shared values:

- `is50Of` -> `Is all of`
- `isOneOf` -> `Is one of`
- `is2Of` -> `Is 2 of`
- `is3Of` -> `Is 3 of`
- `is4Of` -> `Is 4 of`
- `is5Of` -> `Is 5 of`
- `is6Of` -> `Is 6 of`
- `is7Of` -> `Is 7 of`
- `is8Of` -> `Is 8 of`
- `is9Of` -> `Is 9 of`
- `is10Of` -> `Is 10 of`
- `is11Of` -> `Is 11 of`
- `is12Of` -> `Is 12 of`
- `is13Of` -> `Is 13 of`
- `is14Of` -> `Is 14 of`
- `is15Of` -> `Is 15 of`
- `is16Of` -> `Is 16 of`
- `is17Of` -> `Is 17 of`
- `is18Of` -> `Is 18 of`
- `is19Of` -> `Is 19 of`
- `is20Of` -> `Is 20 of`
- `is21Of` -> `Is 21 of`
- `is22Of` -> `Is 22 of`
- `is23Of` -> `Is 23 of`
- `is24Of` -> `Is 24 of`
- `is25Of` -> `Is 25 of`
- `is26Of` -> `Is 26 of`
- `is27Of` -> `Is 27 of`
- `is28Of` -> `Is 28 of`
- `is29Of` -> `Is 29 of`
- `is30Of` -> `Is 30 of`
- `is31Of` -> `Is 31 of`
- `is32Of` -> `Is 32 of`
- `is33Of` -> `Is 33 of`
- `is34Of` -> `Is 34 of`
- `is35Of` -> `Is 35 of`
- `is36Of` -> `Is 36 of`
- `is37Of` -> `Is 37 of`
- `is38Of` -> `Is 38 of`
- `is39Of` -> `Is 39 of`
- `is40Of` -> `Is 40 of`
- `is41Of` -> `Is 41 of`
- `is42Of` -> `Is 42 of`
- `is43Of` -> `Is 43 of`
- `is44Of` -> `Is 44 of`
- `is45Of` -> `Is 45 of`
- `is46Of` -> `Is 46 of`
- `is47Of` -> `Is 47 of`
- `is48Of` -> `Is 48 of`
- `is49Of` -> `Is 49 of`

## Discovery Rules For Unknown IDs

When the user gives a category or dropdown value that is not yet mapped:

1. Type the human-readable value into the matching Keepa control.
2. Read the suggestion text or applied chip.
3. Capture the normalized field key and resolved ID or value.
4. Add the verified mapping to this file.
5. Reuse that value in future runs.

Examples:

- category names often resolve to numeric IDs
- subcategory names often resolve to numeric IDs
- month labels resolve to `YYYYMM`
- operator labels resolve to internal values like `isOneOf`

## Export Preference

Prefer native Keepa export over Playwright row capture.

Use Playwright capture only when:

- export is unavailable
- export fails
- the user explicitly asks for scraped rows

If you use capture fallback, note that in the result.

## Save And Export Rules

- Build the finder URL first.
- Open it in the logged-in session.
- Wait for Keepa to finish loading the row count.
- Apply the JS fallback fields.
- Wait for the final row count.
- Set rows per page.
- Export the current page.
- If the user requests more rows than one page can hold, continue exporting page by page until the requested count is met or Keepa has no more pages.
- Move the file to the user-requested path.
