# Stock broker sample (golden-file test)

These are sample feature files for an imaginary StockBroker trading platform. Together they cover the cases that are
hard to convert correctly. `expected/` holds the approved output. Use this sample to check that the agent still works
after you change the instructions, the script or the agent.

| File | What it tests |
|---|---|
| `auth/login.feature` | Feature description as Context. Outline with two Examples blocks that have different tags. A title-only column (`reason`). A literal `@` in step text (flagged). A commented-out scenario. |
| `trading/orders/place_order.feature` | Two `Rule`s, one with its own Background. `<qty>x` running into text. An unused column (`comment`). Placeholders inside a data table. Placeholder names with spaces (`<limit price>`). A doc string in an expected result. |
| `trading/orders/cancel_order.feature` | An existing `@TC_48213` tag, so that row updates instead of creating. A scenario with no Then. A Given after the Background. |
| `portfolio/holdings.feature` | A multi-column data table. Examples blocks with different columns. An empty cell. Naming variants `<accountId>` and `account-id`. |
| `market-data/watch#list.feature` | An invalid Area Path character (`#` becomes `-`). A duplicate title (becomes ` (2)`). `<direction>order` running into text. |
| `api/orders_api.feature` | API tests found by the `@api` tag. A Bearer JWT in the Background (masked). JSON bodies, including one with placeholders. A schema file reference (flagged). A response data table. |
| `api/auth_token.feature` | API tests found by the folder rule (`apiPaths: api/**`). A `clientSecret` Examples column (masked). An `x-api-key` header value (masked). An XML body (flagged). |
| `market-data/quotes.feature` | An API test found from its step wording only (flagged for review). A **hybrid** test that sets up through the API and checks in the UI. |

## Run the check

1. In Copilot Chat, pick **Gherkin to ADO Test Cases** and say:
   *"Convert using config samples/stock-broker/gherkin-to-ado.config.yml"*
   (output goes to `samples/stock-broker/actual/`).
2. Compare the result with the approved output:
   ```
   python .github/scripts/ado_testcases.py compare --expected samples/stock-broker/expected --actual samples/stock-broker/actual
   ```
   `compare` ignores the wording itself but checks everything else. The result must be `0 difference(s)`.

## Try the ADO ID workflow

`ado-export-sample.csv` looks like an ADO "Export test cases to CSV" file. It has one item whose State is `Ready` and one
item that matches no scenario. To see the ID workflow work without touching `expected/`:
```
python .github/scripts/ado_testcases.py --config samples/stock-broker/gherkin-to-ado.config.yml sync-ids --export samples/stock-broker/ado-export-sample.csv
python .github/scripts/ado_testcases.py --config samples/stock-broker/gherkin-to-ado.config.yml build
python .github/scripts/ado_testcases.py --config samples/stock-broker/gherkin-to-ado.config.yml tag-features
```
The last command is a dry run. Don't add `--write` here, or the sample feature files will change.
Delete `samples/stock-broker/ado-id-map.csv` afterwards, or later `compare` runs will show the IDs as differences.

## Updating `expected/`

Only update it after you have reviewed the change on purpose. Build into `expected/` from its own `_work/` wording:
```
python .github/scripts/ado_testcases.py --config samples/stock-broker/gherkin-to-ado.config.yml --output-dir samples/stock-broker/expected plan
python .github/scripts/ado_testcases.py --config samples/stock-broker/gherkin-to-ado.config.yml --output-dir samples/stock-broker/expected build
python .github/scripts/ado_testcases.py --config samples/stock-broker/gherkin-to-ado.config.yml --output-dir samples/stock-broker/expected validate
```
