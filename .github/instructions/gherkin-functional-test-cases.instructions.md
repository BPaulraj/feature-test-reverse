---
name: Gherkin to ADO Functional Test Cases
description: "Rules for reverse-engineering Gherkin .feature files into traditional functional test cases in an Azure DevOps (ADO) Test Plans import-ready CSV. Area Path = path relative to featuresRoot; Scenario Outlines become parameterized test cases."
applyTo: "ado-test-cases/**"
---
# Gherkin → ADO Functional Test Cases (single source of truth)

These rules are used by the GitHub Copilot agent `.github/agents/gherkin-to-ado.agent.md`.
Change the rules here, not in the agent.

**How the work is split:**
- **The script `.github/scripts/ado_testcases.py` does everything that must be exact.** That means parsing, Area Paths, titles,
  tags, `@parameters`, values files, step rows, Description, CSV layout, ADO IDs and validation. §2–§5 describe what it does.
- **The agent does the parts that need judgement.** It studies the placeholder patterns (§3a), writes the
  tester-friendly wording (§4), fixes what the script reports, and adds review notes.

Never hand-write or hand-edit the generated CSVs, values files, `_all-test-cases*.csv` or `_area-paths.txt`.
Change the wording files or the config, then run `build` again.

## 0. Commands

Run these from the workspace root. First-time setup: `pip install -r .github/scripts/requirements.txt` (Python 3.9+).

| Step | Command | What it does |
|---|---|---|
| 1 | `python .github/scripts/ado_testcases.py plan` | Parses the features. Writes `_work/<rel>.plan.json`, the wording templates `_work/<rel>.wording.json` and `_work/placeholder-inventory.md`. Wording that's already written is kept for any test case whose steps didn't change. |
| 2 | *(agent)* | Writes `_placeholder-analysis.md` (§3a) and fills `action`/`expected` in every wording file (§4). |
| 3 | `python .github/scripts/ado_testcases.py build` | Checks the wording and writes the per-feature CSVs, `.parameters/` files, `_all-test-cases*.csv`, `_area-paths.txt` and `_conversion-report.md`. A feature file with errors gets **no** CSV. |
| 4 | `python .github/scripts/ado_testcases.py validate` | Independently checks every output against the feature files. It must end with `0 error(s)`. |
| – | `... sync-ids --export <ado-export.csv>` | After an ADO import, records the ADO IDs in the ID map (§9). |
| – | `... tag-features [--write]` | Adds `@TC_<id>` tags to scenarios. Dry run unless `--write`. **This is the only command that edits `.feature` files. Run it only when the user asks.** |
| – | `... compare --expected <dir> --actual <dir>` | Golden-file regression check (§10). |
| 4b | `... review` | Builds the **human review pack** in `_review/`: one page per feature file and `review-sheet.csv` for decisions (§12). **A person approves; the agent never does.** |
| – | `... approve --reviewer "<name>" [--scope <file or folder>] [--title <text>] [--reject --comment "..."]` | **For humans only**: records decisions in the review sheet from the command line, instead of using Excel. |
| 5 | `... push [--limit N] [--scope <path>] [--create-areas]` | Pushes directly to ADO through the REST API (§11): suites for folders and files under the test plan, test cases **with parameter values**, and suite membership. **Dry run (read-only) unless `--apply`.** |

Global options, placed before the command: `--config <file>` (default `.github/gherkin-to-ado.config.yml`),
`--features-root`, `--output-dir`, `--ado-project`, `--id-map`. `plan`, `build`, `validate` and `tag-features` also accept
`--scope <folder or file>` to limit the run.
`build --use-source-text` fills any missing wording with the Gherkin text. Use it only for a quick draft, and say so.

If Python or the `gherkin-official` package isn't available, stop and tell the user how to install it. Only do a manual
conversion if the user explicitly asks for one. In that case, follow §2–§5 exactly and say the output was not validated.

## 1. Configuration

**All settings are read from `.github/gherkin-to-ado.config.yml`**, or from the file the user names with `--config`.
A gitignored `<config name>.local.yml` next to it (e.g. `.github/gherkin-to-ado.config.local.yml`) overrides any key. Personal
values such as the real `adoProject` or an absolute `featuresRoot` go there, so they are never committed.
The user's request can override a setting for one run with the global options above. Never edit the config file.

| Config key     | Used below as   | Meaning |
|----------------|-----------------|---------|
| `adoProject`   | `ADO_PROJECT`   | ADO project name. The first node of every Area Path. **Required.** |
| `areaRoot`     | `AREA_ROOT`     | Optional sub-area(s) between the project name and the feature path. Empty means none. |
| `featuresRoot` | `FEATURES_ROOT` | **Base folder** for Area Paths, e.g. `src/test/resources/features`. **Required.** |
| `outputDir`    | `OUTPUT_DIR`    | Folder where generated files are written (relative to the workspace root). |
| `state`        | `STATE`         | State for **new** test cases. Existing ones keep their ADO state from the ID map. |
| `assignedTo`   | `ASSIGNED_TO`   | User (email or display name) put in `Assigned To`. The ADO import wizard lists it as required. |
| `idMapFile`    | –               | Where ADO IDs are kept (§9). Default `<outputDir>/_ado-id-map.csv`. |
| `tcTagPrefix`  | –               | Prefix of ID tags in feature files. Default `TC_`, which gives `@TC_12345`. |
| `apiTags`      | –               | Comma-separated tags that mark API scenarios (§3b). Default `api`. |
| `uiTags`       | –               | Comma-separated tags that mark UI scenarios. Default `ui`. |
| `apiPaths`     | –               | Comma-separated folders/globs under featuresRoot that hold API features, e.g. `api/**`. Default empty. |
| `maskSecrets`  | –               | `true` (default): tokens, keys and client secrets are replaced by `***MASKED***` before anything is written. |
| `splitByTestType` | –            | `true`: also write `_all-api-test-cases.csv`, `_all-ui-…` and `_all-hybrid-…`, for import into separate suites. |

The script stops and reports the problem if:
- the config file is missing
- `adoProject` is empty or still a `<<placeholder>>`
- the `featuresRoot` folder doesn't exist

Don't guess `FEATURES_ROOT`. Only `*.feature` files under it are converted. Files outside it are listed in the report.

## 2. Area Path = relative path from `FEATURES_ROOT`

```
relative = <feature file path relative to FEATURES_ROOT, without ".feature">   e.g.  checkout/payment/card_payment
AreaPath = ADO_PROJECT [\ AREA_ROOT] \ relative (with / replaced by \)
```

The name of `FEATURES_ROOT` itself and every folder above it (`src`, `test`, `resources`, `features`) are **never** part of
the Area Path. ADO requires every Area Path to start with the project name.

| Feature file (`featuresRoot: src/test/resources/features`, `adoProject: ShopApp`) | Area Path (`areaRoot: ""`) | Area Path (`areaRoot: "Regression"`) |
|---|---|---|
| `src/test/resources/features/checkout/payment/card_payment.feature` | `ShopApp\checkout\payment\card_payment` | `ShopApp\Regression\checkout\payment\card_payment` |
| `src/test/resources/features/login.feature` | `ShopApp\login` | `ShopApp\Regression\login` |

Output files use the same relative path, with the original file name: `OUTPUT_DIR/checkout/payment/card_payment.csv`.

Node-name rules (ADO rejects invalid names):
- Keep the original names and casing.
- Keep only letters, digits, space, `-`, `_` and `.`. Replace any other character with `-`. Trim leading and trailing spaces and dots.
- Reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`9`, `LPT1`–`9`) get `_` appended.
- A path can have at most 14 levels. A deeper path is an error, never silently truncated. Every renamed node is reported.

**ADO does not create Area Paths when you import.** Create everything in `_area-paths.txt` first.

## 3. Mapping Gherkin to test cases

| Gherkin | Test case |
|---|---|
| `Scenario` / `Example` | 1 test case |
| `Scenario Outline` + `Examples` | **1 parameterized test case per outline** (§3a) |
| Feature `Background` | Steps added at the start of every scenario in that Feature |
| Rule `Background` | Steps added (after the Feature Background) only to scenarios inside that `Rule` |
| Feature/Rule description | Goes into Description as `Context:`. It never becomes a step. |
| Gherkin tags | Feature + Rule + Scenario + all Examples-block tags, listed in the Description as `Gherkin tags:`. They are **not** ADO tags (§5); ID tags (`@TC_<id>`) are excluded. |
| Commented-out scenarios | Not converted. Listed in the report. |

**Title**
- Use the scenario name, trimmed. If it's empty, use `<Feature name> - scenario at line N` and flag it.
- For outlines, replace `<x>` in the name with `[x]`. Never put example values or `@` parameters in a title.
- 255 chars max. If two titles are the same within one Area Path, the later ones get ` (2)`, ` (3)`, …

**Key.** Each test case has a stable identity: `<relative feature path>::[<Rule name> > ]<scenario name>`, plus ` #n`
if the same name appears more than once in the file. The key doesn't include line numbers, so moving a scenario keeps its ADO ID.
Renaming a scenario, or its Rule, gives it a new key (§9).

## 3a. Scenario Outlines → parameterized test cases

Each outline becomes **one** ADO test case with parameters, and each Examples row becomes one **iteration** in ADO.
That gives a 1:1 mapping: one outline = one ADO test case = one `@TC_<id>` tag.

### Phase 1: study the placeholder patterns before writing any wording (agent)

Read `_work/placeholder-inventory.md`, which `plan` generates with the facts: where each placeholder is used,
parameter names, Examples structure, naming variants, identical tables, and problems. Then read the outlines themselves
and write `OUTPUT_DIR/_placeholder-analysis.md` with your interpretation:
1. **Role of each placeholder** in each outline:
   - **input data** (`<clientId>`, `<qty>`)
   - **expected outcome** (`<error>`, `<status>`)
   - **selector / variant** (picks a path or option: `<side>`, `<format>`, `<order type>`)
   - **title-only** or **unused**
2. **What the Examples rows mean**: are they data variations of the same behaviour, or different test conditions
   (for example a negative block tagged `@negative`)? Name any outline whose rows are really separate test conditions.
   Those are candidates for the team to split in the automation later. Don't split them here.
3. **Same entity, different names**, e.g. `<accountId>` and `account-id`. Say whether they really are the same thing.
   **Never merge two different names into one parameter.** Report it only.
4. **Identical Examples tables** across outlines, which are candidates for ADO Shared Parameters.
5. For every item in the inventory's Problems list: what the tester or the automation team should do about it.
6. **Test types** (the inventory's "Test types" table, §3b): for every scenario detected as API or hybrid from its step
   wording, rather than from a tag or folder rule, say whether you agree. Where you disagree, recommend a tag or an
   `apiPaths` rule. Don't change the type yourself.

Use this understanding when you write the wording, for example to phrase an expected-outcome parameter as a result.

### What the script does with outlines
- Each `<name>` becomes `@name` in the steps: in step text, quoted text, data tables and doc strings.
- Parameter names keep only letters, digits and `_` (`<avg price>` → `@avg_price`). A name starting with a digit gets `p_`.
- A placeholder directly followed by a letter, digit or `_` gets a space after it (`<qty>x` → `@qty x`). This is flagged.
- A placeholder with no matching Examples column stays as literal text. This is flagged.
- **Literal `@`** anywhere in a test case's steps (e.g. `trader@broker.example`) is flagged. ADO will treat it as a parameter.
- All Examples blocks are merged into **one** values table in source order. ADO allows only one per test case.
  Missing cells are left empty. Title-only and unused columns aren't parameters. They're recorded in the Description.
- The values are written to `OUTPUT_DIR/<relative path>.parameters/<NN>-<slug>.csv` and also copied into the Description:
  - `NN` is the outline's position in the file.
  - `slug` is the title in lower case, with every non-alphanumeric character replaced by `-`, cut to 50 chars.
- The Description records which rows came from which Examples block, with their names and tags.

**In ADO:** the CSV import has no column for parameter values. After importing, paste each values file into the test case's
**Parameter values** grid. The report has a checklist. Check the first one: the import doc doesn't say whether ADO recognises
`@parameters` in step text that was imported. ADO shows one outcome per test case: if any iteration fails, the whole
test case shows Failed. Cucumber still reports each Examples row separately.

## 3b. API tests (and hybrid API + UI tests)

Feature files can hold REST/API scenarios (e.g. REST Assured steps) as well as Selenium UI scenarios. The script classifies
**every** test case as `api`, `ui` or `hybrid`, in this order of priority:

1. **Tags:** a tag in `apiTags` means api, a tag in `uiTags` means ui, and both means hybrid. Feature, Rule and Scenario tags all count.
2. **Folder:** the feature file matches an `apiPaths` glob, which means api.
3. **Step wording:** each step is checked for API evidence and UI evidence.
   - API evidence: an upper-case HTTP verb (`GET`/`POST`/`PUT`/`PATCH`/`DELETE`…), `status code`, `response body/header/field`,
     `request header`, `endpoint`, `payload`, `schema`, `JSON path`, `bearer`, a URL path such as `"/v1/orders"`, or a JSON/XML doc string.
   - UI evidence: `page`, `click`, `button`, `dialog`, `is displayed`, and similar.
   - API steps only means api, API and UI steps means hybrid, anything else means ui.

A type that comes from step wording alone is flagged for review. So is a tag or folder rule that contradicts the steps.

What changes for API and hybrid test cases:
- The Description gets `Test type: …` with the reason. ADO tags stay the breadcrumb only (§5). API and hybrid test cases also get a line saying the API steps
  are sent with an API client (e.g. Postman) against the environment under test.
- Each wording row has `stepType` (`api` / `ui` / `neutral`), plus `payload` (`json`, `xml`, …) when it contains a doc string body.
- With `splitByTestType: true`, there are separate import files per type, so API tests can go into their own ADO suite.

**Secrets (always on unless `maskSecrets: false`).** API steps often contain credentials. The script replaces these with
`***MASKED***` **before** the plan, wording, CSV or values files are written:
- JWTs (`eyJ…`) and the token after `Bearer`/`Basic`/`Token`
- the value of `api_key`, `x-api-key`, `client_secret`, `access_token`, `refresh_token`, `token`, `authorization`, … whether
  written as `name: value`, `name = value`, in JSON, in a two-column data table, or as `header "X-API-Key" is "…"`
- Examples columns whose name is a secret name (e.g. `clientSecret`): every value in that column

`<placeholders>` and `@parameters` are never masked. Password test data (a `password` column or row) is **not** masked,
because a manual tester needs it; it's flagged instead. `validate` fails if anything that looks like a secret appears
in any output. Never type a real secret into a wording file. Keep `***MASKED***` exactly as it is.

**Other API flags:**
- a step that refers to a file in the automation repo (e.g. `schemas/order-response.json`): the tester needs access to it
- XML/HTML markup in a step: ADO steps are HTML, so check the markup still shows after import
- a step longer than 4,000 characters, usually a large payload: suggest referencing a file instead

## 4. Writing the wording (agent)

Each wording file row has `source` (the Given/When step) and `sourceExpected` (the Then block attached to it). Both are
already grouped by the script. Fill in `action` and `expected`.

How the script groups steps:
- Every Given and every When (and the `And`/`But`/`*` after it) is its own row.
- A Then block (Then + its `And`/`But`) becomes the expected result of **the row just before it**, whether that row is a Given or a When.
- Only if a scenario *starts* with Then does the script add a row whose action is `Verify the result`. That action is pre-filled; keep it.

Wording rules:
1. Rewrite each step as an imperative instruction for a manual tester. Keep its meaning:
   - `Given the trader is logged in as "TRD1001"` → `Log in as trader "TRD1001"`
   - `Given the trader has available funds of "50000.00" USD` → `Ensure the trader account has available funds of "50000.00" USD`
   - `When the trader cancels order "ORD-5001"` → `Cancel order "ORD-5001"`
2. `expected`: one line per Then/And, written as what the tester should see. Drop the keyword.
   - `Then the order status is "CANCELLED"` → `The order status is "CANCELLED"`
   - `But no order appears in the order book` → `No order appears in the order book`
3. **Copy exactly**: every `@parameter`, every quoted value, and every data table and doc string line. `build` rejects
   missing or extra `@parameters` and warns about any quoted value or table line that wasn't copied exactly.
4. Leave `expected` empty when `sourceExpected` is empty. `build` rejects expected results that aren't in the source.
5. **Do not invent anything.** That includes test data, preconditions, UI element names and extra steps. Don't use Java step
   definitions or page objects as sources of behaviour.
6. Don't add, remove, merge or reorder rows. Don't change `source`, `sourceExpected` or `key`.
7. Use the same wording for the same source step everywhere. Background steps appear in many test cases and should read
   the same in each.
8. **API rows** (`stepType: api`) are instructions for a tester using an API client. Keep the method, path, headers,
   query/form parameters and body exactly as written. Add a short lead-in such as "with this body:" or "with this XML body:", then the
   payload lines unchanged. Examples:
   - `Given the API base URL is "https://api.stockbroker.test"` → `Set the API base URL to "https://api.stockbroker.test"`
   - `And the request header "Authorization" is "Bearer ***MASKED***"` → `Set the request header "Authorization" to "Bearer ***MASKED***"`
   - `When a POST request is sent to "/v1/orders" with body:` + JSON → `Send a POST request to "/v1/orders" with this body:` + the same JSON
   - `Then the response status code is 201` → expected `Response status code is 201`
   - `And the response body field "status" is "FILLED"` → expected `Response body field "status" is "FILLED"`

   Don't invent a base URL, auth method, header, status code or field that isn't in the source. A hybrid test keeps
   its UI rows as UI instructions and its API rows as API instructions, in the original order.

## 4a. Writing the test case title (agent, when `titleStyle: "verify"`)

Traditional test case titles follow this pattern: **`Verify that <expected result> when <test condition>`**.
Each test case in the wording file has:
- `scenarioTitle`: the scenario name. It's read-only and for reference.
- `title`: the title the agent writes.

`build` refuses to run while a title is missing or breaks the rules below.

How to build the title from **this scenario only**:
- **Expected result:** the main outcome from the Then lines. For several Then lines, name the one that is the purpose of
  the scenario, often the one echoed in the scenario name, or summarise them briefly. Don't list them all; they are in the steps.
- **Test condition:** the trigger or situation from the When lines, plus the Given lines when they are what makes this
  case different (for example "for a new account", "without an access token", "with a weak password").
- Join the two with `when`, `after`, `for` or `with`, whichever reads naturally. Put the condition first only if the
  sentence reads better that way.
- API test cases: name what the API returns, e.g. `Verify that the API rejects the registration with status 409 when the
  email is already in use`.
- If a scenario has no Then step, describe the action it completes, e.g. `Verify that the user can dismiss the
  cancel confirmation dialog`. Don't invent a result.

Examples:

| scenarioTitle | title |
|---|---|
| Dashboard shows the account summary and holdings | Verify that the dashboard shows the wallet balance and holdings table after the user logs in |
| Registering with a weak password is rejected | Verify that the API rejects registration with a password validation error when the password is too short |
| Fetching the current user without a token is rejected | Verify that the API returns 401 when the profile is requested without an access token |
| Login is rejected for [reason] (outline) | Verify that an error message is shown and the user stays on the login page when login fails for [reason] |

Rules, which `build` enforces:
1. Start with `Verify that ` and write a single line of at most 255 characters. Aim for under 150.
2. No `@`: parameters and literal `@` values belong in the steps. No `<placeholders>`. For outlines, name the varying
   thing generically, or as `[name]` using the Examples column name. Never put a concrete example value in the title.
3. Every title must be unique within its feature file / Area Path. Make it more specific rather than relying on ` (2)`.
4. **Do not invent anything:** no UI elements, buttons, pages, status codes or data that aren't in the scenario. The
   title may only rephrase what the steps and scenario name already say.
5. Keep the business language of the feature file, e.g. "wallet balance" or "holdings", and don't add implementation details.

The original scenario name always stays in the Description (`Scenario: …`), and the test case's identity doesn't depend
on the title. So changing a title never breaks the ADO ID mapping, but it does send an approved test case back to Pending (§12).

## 5. The CSV format (what `build` writes)

Columns follow Microsoft's documented import format
(https://learn.microsoft.com/azure/devops/test/bulk-import-export-test-cases):

```
ID,Work Item Type,Title,Test Step,Step Action,Step Expected,Area Path,Assigned To,State,Tags,Description
```

- Every row is one test step, and the first step is on the same row as the test case.
- Every test case field is repeated on each of its step rows. `Test Step` starts at 1 for each test case.
- `ID`: the ADO ID from the ID map or from a `@TC_<id>` tag, otherwise empty (creates a new item).
- `State` / `Assigned To`: taken from the ID map for existing items, otherwise from the config.
- `Tags` (separated by `; `): **only the breadcrumb after `featuresRoot`**, meaning each folder name plus the feature file
  name without `.feature`, using the same sanitized names as the Area Path. Nothing else goes in `Tags`.
  - `auth/login.feature` → `auth; login`
  - `trading/orders/place_order.feature` → `trading; orders; place_order`
  - `market-data/watch#list.feature` → `market-data; watch-list`

  Gherkin tags, the scenario type and the test type are kept in the Description instead.
- `Description`, as plain text lines:
  ```
  Source: <workspace-relative feature path>:<line>
  Feature: <name>
  Rule: <name>                     (if any)
  Scenario: <original name, with <placeholders> left as they are> (Scenario | Scenario Outline - parameterized)
  Gherkin tags: @smoke @P1 ...     (if any; ID tags @TC_<id> excluded)
  Context: <feature/rule description> (if any)
  Test type: API | UI | Hybrid (API + UI) (<reason>)
  Manual execution: ...            (API and hybrid only)
  Parameters: @a, @b               (outlines only, and the lines below too)
  Iterations: <n> | values file: <relative path>.parameters/NN-slug.csv
  Examples blocks: rows 1-2 "name" @tag; row 3 "name" @tag
  Not parameters (title-only/unused columns): <col>=<values>
  Values:
  a | b
  <one line per row, max 50>
  ```
- UTF-8 without BOM, RFC 4180 quoting, `\r\n` between rows. A line break inside a cell is `\n` inside a quoted field.

If the team's ADO process needs a different column layout, change `COLUMNS` in the script. Don't hand-edit the output.

## 6. Output files

```
<OUTPUT_DIR>/
  <relative path>.csv                    one import CSV per feature file
  <relative path>.parameters/NN-*.csv    one values file per outline (paste into ADO Parameter values)
  _all-test-cases.csv                    all features in one import file (split into -part-N if over ~19 MB; ADO's limit is 20 MB)
  _all-<api|ui|hybrid>-test-cases.csv    only with splitByTestType: true - one import file per test type
  _area-paths.txt                        distinct Area Paths - create these in ADO before importing
  _conversion-report.md                  written by build; the agent adds notes under "Agent notes"
  _placeholder-analysis.md               written by the agent (§3a)
  _ado-id-map.csv                        ADO IDs (§9) - kept between runs, never delete it
  _work/                                 plan, wording files and placeholder inventory
```

- Write only inside `OUTPUT_DIR`. **Never change `.feature` files, Java code, or anything else in the automation project.**
  The only exception is `tag-features --write`, and only when the user asks for it.
- The wording files in `_work/` are the agent's work. Keep them between runs, so a re-run only needs wording for changed scenarios.

## 7. Report

`build` writes `_conversion-report.md` with:
- the settings and counts, including the number of UI, API and hybrid test cases and whether masking is on
- a table of every test case that is API, hybrid or tagged, with the reason for its type
- the import steps
- the parameter paste checklist
- **Needs review**: renamed nodes, duplicate titles, missing Then, literal `@`, renamed or undefined parameters, adjacency
  fixes, differing Examples columns or tags, unused columns, commented-out scenarios and files outside featuresRoot
- errors and wording warnings

The agent adds, under **Agent notes**:
- the key findings from `_placeholder-analysis.md`
- steps whose rewording the tester should check against the source
- anything else a reviewer needs to know

Never say the CSV was imported or validated in ADO unless that actually happened.

## 8. Large repositories

`plan` and `build` cover the whole repo in one run. Fill in the wording one file at a time (`_work/<rel>.wording.json`),
and don't keep the whole repo in context. Use `--scope` to plan, build and validate one folder at a time if needed.
Wording already written for unchanged test cases is kept when you run `plan` again.

## 9. ADO IDs and re-runs

The goal is that after the first import, every later import **updates** the existing test cases instead of creating
duplicates.

1. First import: rows have no ID, so ADO creates new test cases.
2. In ADO Test Plans, export those test cases to CSV. Then run
   `python .github/scripts/ado_testcases.py sync-ids --export <export.csv>`. The script:
   - matches exported rows to scenarios by Area Path + Title, or by Title alone if that is unique
   - writes `_ado-id-map.csv` (key → ADO ID, State, Assigned To)
   - reports ADO items with no matching scenario, scenarios that are mapped but no longer exist (renamed or deleted), and conflicts
3. Run `build` again: rows now carry the ID and the item's current State. **Re-importing a row with an ID replaces all
   of that test case's steps.** Check with one test case whether the pasted parameter values survive a re-import.
4. Optional, only when the user asks: `tag-features` (dry run), then `tag-features --write`. This adds `@TC_<id>` to each
   mapped scenario, so the mapping stays with the automation code even if a scenario is renamed. The tag takes
   precedence over the map, and it's never exported as an ADO tag.
5. If a scenario is renamed and has no `@TC_` tag, its key changes and it would become a new test case. `sync-ids`
   reports the old key as an orphan. Fix it by adding the `@TC_<id>` tag, or by correcting the key in the map.

## 10. Golden-file regression check

`samples/stock-broker/` contains sample features covering the tricky cases, plus the approved outputs in `expected/`.
After changing these instructions, the script or the agent:
1. Run the agent on the sample, using `--config samples/stock-broker/gherkin-to-ado.config.yml` (it writes to `samples/stock-broker/actual`).
2. Run `python .github/scripts/ado_testcases.py compare --expected samples/stock-broker/expected --actual samples/stock-broker/actual`.

`compare` ignores the wording itself but checks everything else: titles, Area Paths, tags, IDs, Descriptions,
step counts, the `@parameters` in each step, which steps have an expected result, and the values files. It must report `0 difference(s)`.

## 11. Pushing directly to ADO (REST API)

This is an alternative to the CSV import. Connection details come from `ado.properties` in the workspace root:
`ado.org.url`, `ado.project`, `ado.pat`, `ado.api.version` and `ado.testplan.id`. The environment variable `ADO_PAT`
overrides the PAT. The file is gitignored; `ado.properties.example` shows the format.
**Never print, copy, log or commit the PAT.**

`python .github/scripts/ado_testcases.py push`:
1. Runs the same checks as `build`. The push is refused while any test case still has an error.
   With `requireReview: true` (the default), only test cases that pass the review gate (§12) are pushed. All others are skipped with the reason. `--verbose` lists each one.
2. Requires `adoProject` in the config to equal `ado.project`, because every Area Path starts with the project name.
3. Checks that every Area Path exists. Missing ones stop the push. `--create-areas` creates them, if the PAT's
   user has permission.
4. Mirrors the folder structure as **static suites** under the test plan's root suite, optionally under `suiteRoot`:
   `<plan>/<folder>/<sub-folder>/<feature file>`. Suite names are the same sanitized names used for the Area Path.
   Existing suites with the same name are reused. A same-named suite of another type (query-based or requirement-based) is an error.
5. Checks every mapped ADO ID (from the ID map or an `@TC_` tag) before anything is written.
6. Creates each new test case, or updates a mapped one, with title, Area Path, tags, Description, steps, and **for
   outlines the parameters and their values**. There's nothing to paste by hand. New test cases get `state` and
   `assignedTo` from the config. Updates keep the item's current state and owner.
7. Saves each new ID to the ID map **straight after it is created**, so a re-run after a failure updates the item
   instead of creating a duplicate.
8. Adds each test case to the leaf suite of its feature file. It never removes anything from a suite. Test cases in a
   suite that no scenario maps to are reported and left as they are.

How to use it:
- **Always run the dry run first.** It only reads from ADO and prints every suite, Area Path, test case and membership it
  would create or change. Show that output to the user.
- **`--apply` changes the user's ADO project.** Run it only after the user has seen the dry run and confirmed. Start
  small (`--limit 1`, or `--scope` with one feature file), have the user check the result in ADO (steps, parameters,
  values, suite placement), then do the rest.
- An update **replaces all steps** of that test case with the generated ones.
- PAT scopes needed: Work Items (Read & Write), Test Management (Read & Write), and for `--create-areas` Project and Team (Read & Write).

## 12. Human review before pushing

People must approve the written test cases before anything is uploaded to ADO.

1. `python .github/scripts/ado_testcases.py review` writes `OUTPUT_DIR/_review/`:
   - `README.md`: an index with Approved / Rejected / Pending counts for each feature file, and how to review.
   - `<relative path>.md`: one page per feature file. It shows every test case exactly as it will reach ADO: title,
     Area Path, suite, tags, type, steps table, parameter values, items to check, the Description, and a link to the source line.
   - `review-sheet.csv`: one row per test case. It opens in Excel (UTF-8 with BOM). Reviewers fill in:
     - **Decision**: `Approved`, `Rejected` or `Pending`
     - **Reviewer**: their name
     - **Comment**: required when rejecting; say what's wrong

     Saving from Excel is fine, including with `;` separators or ANSI encoding.
   - `approval-log.csv`: written by `push --apply`. It records the time, create/update, ADO ID, content hash, reviewer,
     review date, and the OS user who pushed.
2. Every row has a **Content Hash**: a fingerprint of everything that is sent to ADO (title, Area Path, tags,
   Description, steps, parameters and values). Line numbers aren't included. An approval is valid **only for that hash**.
   When a test case changes, `review` sets it back to Pending, keeps the old comment as `[previous: …]`, and prints `RESET`.
3. `push` uploads a test case only if its Decision is Approved, the Reviewer is filled in, and the hash still matches.
   Everything else is skipped: Pending, Rejected, changed after approval, no reviewer name, never reviewed.
4. Rejected test cases: the agent reads the Comment, fixes the wording in `_work/<rel>.wording.json`, and runs `build`,
   `validate` and `review` again. The test case returns as Pending for the reviewer. The agent never marks anything Approved.
5. `requireReview: false` in the config turns the gate off. Only do this when the user explicitly asks.

**The agent must never:**
- edit the Decision, Reviewer or Review Date columns
- run `approve`
- set `requireReview: false`
- push anything a person hasn't approved

If the user asks the agent to approve, tell them the approval has to be recorded by a person: in the sheet, or by
running `approve` themselves.
