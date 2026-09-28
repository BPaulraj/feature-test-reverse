# Conversion report

Generated 2026-09-28T23:59:15 by `ado_testcases.py build`. The CSV has **not** been imported into ADO by this tool.

## Settings

- ADO_PROJECT: `StockBroker`
- AREA_ROOT: `Regression`
- FEATURES_ROOT: `samples/stock-broker/features`
- OUTPUT_DIR: `samples/stock-broker/expected`
- ASSIGNED_TO: `qa.lead@broker.example`
- ID map: `samples/_none.csv`

## Counts

- Feature files: 8 (CSV written: 8)
- Test cases: 24 - scenarios: 15, parameterized outlines: 9
- Iterations (Examples rows): 20
- Step rows: 69
- Test cases with an ADO ID (will UPDATE existing items): 1
- Test cases without an ADO ID (will CREATE new items): 23
- Test types: UI 17, API 6, Hybrid 1 - separate import files per type: `_all-<type>-test-cases.csv`
- Secret masking: on

| Feature file | Area Path | Test cases | Outlines | Iterations | Steps |
|---|---|---|---|---|---|
| `samples/stock-broker/features/api/auth_token.feature` | `StockBroker\Regression\api\auth_token` | 2 | 1 | 2 | 3 |
| `samples/stock-broker/features/api/orders_api.feature` | `StockBroker\Regression\api\orders_api` | 3 | 1 | 3 | 12 |
| `samples/stock-broker/features/auth/login.feature` | `StockBroker\Regression\auth\login` | 3 | 1 | 3 | 6 |
| `samples/stock-broker/features/market-data/quotes.feature` | `StockBroker\Regression\market-data\quotes` | 2 | 0 | 0 | 4 |
| `samples/stock-broker/features/market-data/watch#list.feature` | `StockBroker\Regression\market-data\watch-list` | 4 | 2 | 2 | 9 |
| `samples/stock-broker/features/portfolio/holdings.feature` | `StockBroker\Regression\portfolio\holdings` | 3 | 2 | 5 | 9 |
| `samples/stock-broker/features/trading/orders/cancel_order.feature` | `StockBroker\Regression\trading\orders\cancel_order` | 3 | 0 | 0 | 11 |
| `samples/stock-broker/features/trading/orders/place_order.feature` | `StockBroker\Regression\trading\orders\place_order` | 4 | 2 | 5 | 15 |

## Before importing

1. Create every Area Path in `_area-paths.txt` (Project Settings > Boards > Project configuration > Areas).
2. Import one feature CSV first and check it, then `_all-test-cases.csv`.
3. Rows with an ID update existing test cases and **replace all their steps**. Rows without an ID create new test cases - importing the same file twice creates duplicates.
4. After importing new test cases: export them from ADO to CSV and run `python .github/scripts/ado_testcases.py sync-ids --export <file>` so the next run updates instead of duplicating.

## Parameter paste checklist

After the import, open each test case below, go to **Parameter values**, and paste the header + rows from its values file (open it in Excel, copy, paste). Check with the first one that ADO lists the `@parameters` from the imported steps. ADO shows one outcome per test case: if any iteration fails, the test case shows Failed.

| Title | Area Path | Values file | Iterations |
|---|---|---|---|
| Token request for partner [partner] | `StockBroker\Regression\api\auth_token` | `api/auth_token.parameters/01-token-request-for-partner-partner.csv` | 2 |
| Order validation returns [statusCode] for [case] | `StockBroker\Regression\api\orders_api` | `api/orders_api.parameters/01-order-validation-returns-statuscode-for-case.csv` | 3 |
| Login is rejected for [reason] | `StockBroker\Regression\auth\login` | `auth/login.parameters/01-login-is-rejected-for-reason.csv` | 3 |
| Price alert for [symbol] | `StockBroker\Regression\market-data\watch-list` | `market-data/watch#list.parameters/01-price-alert-for-symbol.csv` | 1 |
| Watchlist sorting by [column] | `StockBroker\Regression\market-data\watch-list` | `market-data/watch#list.parameters/02-watchlist-sorting-by-column.csv` | 1 |
| Unrealised P&L for [symbol] | `StockBroker\Regression\portfolio\holdings` | `portfolio/holdings.parameters/01-unrealised-p-l-for-symbol.csv` | 3 |
| Export holdings as [format] | `StockBroker\Regression\portfolio\holdings` | `portfolio/holdings.parameters/02-export-holdings-as-format.csv` | 2 |
| Place a market [side] order for [symbol] | `StockBroker\Regression\trading\orders\place_order` | `trading/orders/place_order.parameters/01-place-a-market-side-order-for-symbol.csv` | 3 |
| Place a limit order with [order type] validity | `StockBroker\Regression\trading\orders\place_order` | `trading/orders/place_order.parameters/02-place-a-limit-order-with-order-type-validity.csv` | 2 |

## Test types (API / hybrid / tagged)

Check these, especially the ones detected from the step wording. To fix one, add an API/UI tag to the scenario or an `apiPaths` rule to the config.

| Title | Type | Reason |
|---|---|---|
| Token request for partner [partner] | api | folder rule api/** |
| Legacy trade confirmation in XML | api | folder rule api/** |
| Create a market order | api | tag @api |
| Order validation returns [statusCode] for [case] | api | tag @api |
| Get order by ID | api | tag @api |
| Quote endpoint returns the last traded price | api | detected from steps: 3 API |
| Price alert created via API is shown in the web watchlist | hybrid | detected from steps: 1 API, 2 UI |

## Needs review

- `samples/stock-broker/features/api/auth_token.feature:3` - Examples column 'clientSecret' looks like a secret - values masked
- `samples/stock-broker/features/api/auth_token.feature:3` - Examples column 'partner' is title-only - not a parameter
- `samples/stock-broker/features/api/auth_token.feature:15` - Secret-like value masked in step at line 16 (1x)
- `samples/stock-broker/features/api/auth_token.feature:15` - Step at line 17 contains XML/HTML markup - ADO steps are HTML; check it shows correctly after the import
- `samples/stock-broker/features/api/orders_api.feature:11` - Secret-like value masked in step at line 7 (1x)
- `samples/stock-broker/features/api/orders_api.feature:11` - Step at line 24 refers to file 'schemas/order-response.json' from the automation repo - the tester needs access to it
- `samples/stock-broker/features/api/orders_api.feature:26` - Secret-like value masked in step at line 7 (1x)
- `samples/stock-broker/features/api/orders_api.feature:26` - Examples column 'case' is title-only - not a parameter
- `samples/stock-broker/features/api/orders_api.feature:40` - Secret-like value masked in step at line 7 (1x)
- `samples/stock-broker/features/auth/login.feature:15` - Examples blocks have different tags - see Description for which rows came from which block
- `samples/stock-broker/features/auth/login.feature:15` - Password-like Examples column 'password' is copied to ADO (not masked)
- `samples/stock-broker/features/auth/login.feature:15` - Examples column 'reason' is title-only - not a parameter
- `samples/stock-broker/features/auth/login.feature:31` - Literal '@broker' in step at line 32 - ADO will treat it as a parameter
- `samples/stock-broker/features/auth/login.feature:31` - Literal '@broker' in step at line 33 - ADO will treat it as a parameter
- `samples/stock-broker/features/market-data/quotes.feature:3` - Detected as API from 3 step(s) without an API tag or folder rule - check the test type
- `samples/stock-broker/features/market-data/quotes.feature:8` - Hybrid: 1 API step(s) and 2 UI step(s) - check the test type
- `samples/stock-broker/features/market-data/watch#list.feature:11` - Duplicate title in Area Path, renamed to 'Add a symbol to the watchlist (2)'
- `samples/stock-broker/features/market-data/watch#list.feature:24` - <direction> was directly followed by text - a space was inserted after @direction
- `samples/stock-broker/features/portfolio/holdings.feature:16` - Examples blocks have different columns - merged, missing cells left empty
- `samples/stock-broker/features/portfolio/holdings.feature:16` - Parameter <avg price> renamed to @avg_price
- `samples/stock-broker/features/portfolio/holdings.feature:16` - Examples column 'account-id' is unused - not a parameter
- `samples/stock-broker/features/portfolio/holdings.feature:31` - Parameter <file name> renamed to @file_name
- `samples/stock-broker/features/trading/orders/cancel_order.feature:14` - No expected result (no Then step)
- `samples/stock-broker/features/trading/orders/place_order.feature:15` - <qty> was directly followed by text - a space was inserted after @qty
- `samples/stock-broker/features/trading/orders/place_order.feature:15` - Examples column 'comment' is unused - not a parameter
- `samples/stock-broker/features/trading/orders/place_order.feature:35` - Parameter <limit price> renamed to @limit_price
- `samples/stock-broker/features/trading/orders/place_order.feature:35` - Parameter <order type> renamed to @order_type
- `samples/stock-broker/features/market-data/watch#list.feature` - Area Path node renamed: 'watch#list' -> 'watch-list'
- `samples/stock-broker/features/auth/login.feature:35` - Commented-out scenario - not converted: # Scenario: Login with biometrics

## Agent notes

_The agent adds its review notes below this line._
