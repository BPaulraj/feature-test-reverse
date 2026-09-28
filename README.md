# feature-test-reverse

Turns the Gherkin `.feature` files of a Cucumber test-automation project (Java / Selenium / REST) into **traditional,
manually executable functional test cases** in **Azure DevOps (ADO) Test Plans**. Use it when the automated scenarios
and the test cases in ADO have drifted apart, or were never linked.

- **Area Path, suites and tags follow the feature folder structure.** `features/api/auth.feature` becomes Area Path
  `<project>\api\auth`, suite `<plan>/api/auth`, and tags `api; auth`.
- **Traditional format:** titles read "Verify that … when …", and steps are imperative actions with expected results.
  Scenario Outlines become **parameterized** test cases, one iteration per Examples row.
- **UI, API and hybrid scenarios** are detected. Tokens and API keys are **masked** before anything is written.
- **Human review gate:** nothing reaches ADO until a person has approved the exact content.
- **Two-way link:** ADO IDs are recorded and can be written back to the scenarios as `@TC=<id>` tags, so later runs
  update the same test cases instead of duplicating them.

## How it works

The work is split between deterministic code and an instruction-based agent:

| Part | File | Does |
|---|---|---|
| Script | `.github/scripts/ado_testcases.py` | Parses the features with the official Cucumber Gherkin parser, and works out Area Paths, tags, parameters, step grouping, secret masking, the CSV/REST payloads, IDs and validation. |
| Agent | `.github/agents/gherkin-to-ado.agent.md` (GitHub Copilot custom agent) | Studies the scenarios and writes the human part: step wording and "Verify that" titles. |
| Rules | `.github/instructions/gherkin-functional-test-cases.instructions.md` | The single source of truth for both. |

```
plan ─► agent writes wording + titles ─► build ─► validate ─► review ─► (human approves) ─► push ─► tag-features
```

## Setup

1. Python 3.9+, then `pip install -r .github/scripts/requirements.txt`.
2. Configure `.github/gherkin-to-ado.config.yml`. Put personal values in `.github/gherkin-to-ado.config.local.yml`,
   which is gitignored and overrides any key:
   ```yaml
   adoProject: "MyProject"
   featuresRoot: "C:/path/to/automation-repo/src/test/resources/features"
   ```
3. To push through the REST API, copy `ado.properties.example` to `ado.properties` (gitignored) and fill in the org URL,
   project, PAT, API version and test plan ID. The environment variable `ADO_PAT` can replace the PAT in the file.

## Usage

```powershell
python .github/scripts/ado_testcases.py plan          # parse features, create wording templates
#   -> in Copilot Chat, pick "Gherkin to ADO Test Cases" to write the wording and titles
python .github/scripts/ado_testcases.py build         # write CSVs (ADO import format) + report
python .github/scripts/ado_testcases.py validate      # independent checks, must show 0 errors
python .github/scripts/ado_testcases.py review        # review pages + review-sheet.csv for humans
python .github/scripts/ado_testcases.py approve --reviewer "Name" [--scope login.feature]   # humans only
python .github/scripts/ado_testcases.py push [--create-areas]          # dry run (read-only)
python .github/scripts/ado_testcases.py push --apply [--create-areas]  # create/update in ADO
python .github/scripts/ado_testcases.py tag-features [--write]         # write @TC=<id> tags into the features
```
`--scope <folder or file>` limits `plan`, `build`, `validate`, `review`, `push` and `tag-features`.
`python .github/scripts/ado_testcases.py <command> --help` lists every option.

As an alternative to `push`, the generated CSVs can be imported by hand
(Test Plans > Import test cases from CSV/XLSX). `sync-ids --export <ado-export.csv>` then records the IDs.

## Safety

- `push` and `tag-features` are **dry runs** unless you add `--apply` / `--write`.
- `tag-features --write` is the only command that edits the automation repo. Every other command only reads it.
- `push` only uploads test cases that a person has **Approved** in the review sheet and that haven't changed since.
  Every push is recorded in an approval log.
- `ado.properties` (PAT), `*.local.yml` and the generated `ado-test-cases/` output are gitignored.

## Regression sample

`samples/stock-broker/` holds sample features for an imaginary stock broker platform that cover the tricky cases, plus
the approved output in `expected/`. After changing the rules or the script, rebuild the sample and compare it:
```powershell
python .github/scripts/ado_testcases.py compare --expected samples/stock-broker/expected --actual samples/stock-broker/actual
```
See [samples/stock-broker/README.md](samples/stock-broker/README.md).
