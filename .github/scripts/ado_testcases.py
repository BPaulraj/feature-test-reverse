#!/usr/bin/env python3
"""Deterministic helper for the "Gherkin to ADO Test Cases" Copilot agent.

The code does everything that must be exact (parsing, Area Paths, titles, @parameters, values
files, CSV layout, IDs, validation). The agent only writes the tester-friendly wording.

Run from anywhere; paths in the config are relative to the workspace root (parent of .github).

  plan          parse features -> _work/<rel>.plan.json, _work/<rel>.wording.json (template for the agent),
                _work/placeholder-inventory.md
  build         plan + agent wording -> per-feature CSVs, .parameters/ files, _all-test-cases*.csv,
                _area-paths.txt, _conversion-report.md
  validate      check generated (or hand-written) outputs against the feature files
  compare       compare two output folders, ignoring wording (golden-file regression check)
  sync-ids      read an ADO "Export test cases to CSV" file and record ADO IDs in the ID map
  tag-features  add @TC_<id> tags to scenarios (dry run unless --write; the ONLY command that edits .feature files)

Rules implemented here are documented in .github/instructions/gherkin-functional-test-cases.instructions.md.
Requires: pip install -r .github/scripts/requirements.txt
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path

try:
    from gherkin.parser import Parser
    from gherkin.token_scanner import TokenScanner
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency. Run: pip install -r .github/scripts/requirements.txt")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ".github/gherkin-to-ado.config.yml"
COLUMNS = ["ID", "Work Item Type", "Title", "Test Step", "Step Action", "Step Expected",
           "Area Path", "Assigned To", "State", "Tags", "Description"]
TC_FIELDS = ["ID", "Work Item Type", "Title", "Area Path", "Assigned To", "State", "Tags", "Description"]
MAP_COLUMNS = ["Key", "ADO ID", "Area Path", "Title", "State", "Assigned To", "Last Synced"]
MAX_IMPORT_BYTES = 19 * 1024 * 1024          # documented limit is 20 MB; keep a margin
MAX_TITLE = 255
MAX_AREA_DEPTH = 14
MAX_DESCRIPTION_VALUE_ROWS = 50
RESERVED_NODES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
PLACEHOLDER = re.compile(r"<([^<>\n]+)>")
PARAM_TOKEN = re.compile(r"@([A-Za-z0-9_]+)")
QUOTED = re.compile(r'"([^"\n]*)"')
SPECIAL_TOKENS = {"", "null", "blank", "<empty>", "n/a", "na", "none", "empty", '""'}
COMMENTED_SCENARIO = re.compile(r"^\s*#\s*(Scenario Outline|Scenario Template|Scenario|Example)\s*:", re.I)

# --- API vs UI detection (§3b). Verbs are matched in UPPER CASE only ("get" is an ordinary English word).
API_VERB = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
API_TERM = re.compile(r"\b(status code|response (?:body|header|headers|code|status|time|schema|field|payload|contains)|"
                      r"request (?:body|header|headers|payload)|endpoint|json ?path|xpath|api key|bearer|content-type|"
                      r"payload|schema|http|rest|graphql|query param(?:eter)?s?|path param(?:eter)?s?)\b", re.I)
API_URL = re.compile(r"(?:\"|\s)(/[A-Za-z0-9_\-{}.:<>@]+(?:/[A-Za-z0-9_\-{}.:<>@]*)+)|https?://", re.I)
UI_TERM = re.compile(r"\b(page|click|clicks|button|screen|dialog|navigate|browser|dropdown|menu|popup|tab|link|"
                     r"is displayed|is shown|opens the|enters|selects|scrolls|hovers|toast|modal)\b", re.I)
# --- secrets (§3b): masked before anything is written, so they never reach ADO
MASK = "***MASKED***"
SECRET_NAME = re.compile(r"^(x-)?(api[_-]?key|apikey|client[_-]?secret|secret|access[_-]?token|refresh[_-]?token|"
                         r"auth[_-]?token|id[_-]?token|token|authorization|private[_-]?key|x-api-key)$", re.I)
PASSWORD_NAME = re.compile(r"(^|[_\- ])(pass(word)?|pwd)([_\- ]|$)", re.I)
SECRET_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), lambda m: MASK),       # JWT
    (re.compile(r"\b(Bearer|Basic|Token)\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{8,}"), lambda m: f"{m.group(1)} {MASK}"),
    (re.compile(r"(?i)(\"?\b(?:x-api-key|api[_-]?key|apikey|client[_-]?secret|access[_-]?token|refresh[_-]?token|"
                r"auth[_-]?token|id[_-]?token|private[_-]?key|secret|token|authorization)\b\"?\s*[:=]\s*\"?)"
                r"(?!\*\*\*)(?![<@])(?!Bearer\b|Basic\b)([^\"\s,}<]{6,})"), lambda m: m.group(1) + MASK),
    # step wording such as: header "X-API-Key" is "k9f8..."
    (re.compile(r"(?i)(\"(?:x-api-key|api[_-]?key|apikey|client[_-]?secret|access[_-]?token|refresh[_-]?token|"
                r"auth[_-]?token|id[_-]?token|private[_-]?key|secret|token|authorization)\"\s+(?:header\s+)?"
                r"(?:is|equals|=|:|as|set to|with value)\s+\")(?!\*\*\*)(?![<@])(?!Bearer\b|Basic\b)([^\"]{6,})"),
     lambda m: m.group(1) + MASK),
]
FILE_REF = re.compile(r"[\w./\\-]+\.(?:json|xml|xsd|ya?ml|csv|txt|properties|graphql)\b", re.I)
# closing or self-closing tags only: a Gherkin placeholder like <symbol> never has them
MARKUP = re.compile(r"</\s*[A-Za-z][\w:.-]*\s*>|<[A-Za-z][\w:.-]*(\s[^<>]*)?/>")
LARGE_CELL = 4000
API_FLAG_PREFIXES = ("Literal", "Secret", "Password", "Step at line", "Tagged", "In API folder", "Hybrid",
                     "Detected as API")


# ----------------------------------------------------------------------------------------------- config
def load_config(path: str, overrides: dict) -> dict:
    cfg_path = (ROOT / path) if not Path(path).is_absolute() else Path(path)
    if not cfg_path.exists():
        sys.exit(f"Config file not found: {cfg_path}")
    cfg = {}
    # <name>.local.yml next to the config (gitignored) overrides it: personal paths/projects stay out of the repo
    local_path = cfg_path.with_name(cfg_path.stem + ".local" + cfg_path.suffix)
    for path in [cfg_path] + ([local_path] if local_path.exists() else []):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split(" #")[0].strip() if not raw.strip().startswith("#") else ""
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
                if raw.split(":", 1)[1].strip().startswith('"'):
                    value = value.replace("\\\\", "\\")
            cfg[key.strip()] = value
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    cfg.setdefault("areaRoot", "")
    cfg.setdefault("outputDir", "ado-test-cases")
    cfg.setdefault("state", "Design")
    cfg.setdefault("assignedTo", "")
    cfg.setdefault("tcTagPrefix", "TC_")
    cfg.setdefault("idMapFile", f"{cfg['outputDir'].rstrip('/')}/_ado-id-map.csv")
    cfg.setdefault("apiTags", "api")
    cfg.setdefault("uiTags", "ui")
    cfg.setdefault("apiPaths", "")
    cfg.setdefault("maskSecrets", "true")
    cfg.setdefault("splitByTestType", "false")
    cfg.setdefault("suiteRoot", "")
    cfg.setdefault("titleStyle", "scenario")
    cfg["_verifyTitles"] = cfg["titleStyle"].strip().lower() == "verify"
    cfg.setdefault("requireReview", "true")
    cfg["_review"] = cfg["requireReview"].lower() not in ("false", "no", "0")
    cfg["_apiTags"] = {t.strip().lstrip("@").lower() for t in cfg["apiTags"].split(",") if t.strip()}
    cfg["_uiTags"] = {t.strip().lstrip("@").lower() for t in cfg["uiTags"].split(",") if t.strip()}
    cfg["_apiPaths"] = [p.strip().replace("\\", "/") for p in cfg["apiPaths"].split(",") if p.strip()]
    cfg["_mask"] = cfg["maskSecrets"].lower() not in ("false", "no", "0")
    cfg["_split"] = cfg["splitByTestType"].lower() in ("true", "yes", "1")
    problems = []
    if not cfg.get("adoProject") or "<<" in cfg["adoProject"]:
        problems.append("adoProject is empty or still the placeholder")
    if not cfg.get("featuresRoot"):
        problems.append("featuresRoot is empty")
    elif not (ROOT / cfg["featuresRoot"]).is_dir():
        problems.append(f"featuresRoot folder does not exist: {cfg['featuresRoot']}")
    if problems:
        sys.exit("Fix the config (" + str(cfg_path) + "):\n  - " + "\n  - ".join(problems))
    cfg["_features"] = (ROOT / cfg["featuresRoot"]).resolve()
    cfg["_src_root"] = source_root(cfg["_features"])
    cfg["_out"] = (ROOT / cfg["outputDir"]).resolve()
    cfg["_map"] = (ROOT / cfg["idMapFile"]).resolve()
    cfg["_config_path"] = cfg_path
    return cfg


def source_root(features: Path) -> Path:
    """Root that 'Source:' paths are relative to: this workspace if the features live in it, otherwise the automation
    repo root (nearest folder above featuresRoot with pom.xml / build.gradle / .git)."""
    if ROOT == features or ROOT in features.parents:
        return ROOT
    for parent in [features, *features.parents]:
        if any((parent / m).exists() for m in ("pom.xml", "build.gradle", "build.gradle.kts", ".git")):
            return parent
    return features.parent


# ----------------------------------------------------------------------------------------------- naming
def sanitize_node(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9 _.\-]", "-", name).strip(" .")
    if out in ("", ".", ".."):
        out = "-"
    if out.upper() in RESERVED_NODES:
        out += "_"
    return out[:255]


def sanitize_param(name: str) -> str:
    out = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", name)).strip("_") or "p"
    return f"p_{out}" if out[0].isdigit() else out


def check_title(title: str) -> list[str]:
    """Rules for agent-written titles (titleStyle: verify, instructions §4a)."""
    if not title:
        return ["title not written yet - fill 'title' in the wording file (titleStyle: verify)"]
    problems = []
    if not re.match(r"(?i)^verify that\s+\S", title):
        problems.append(f"title must start with 'Verify that ': '{title[:60]}'")
    if "@" in title:
        problems.append("title must not contain '@' (ADO parameters or literal @ belong in the steps)")
    if PLACEHOLDER.search(title):
        problems.append("title must not contain <placeholders> - use [name] if the title needs to name one")
    if len(title) > MAX_TITLE:
        problems.append(f"title is {len(title)} chars (max {MAX_TITLE})")
    if "\n" in title:
        problems.append("title must be a single line")
    return problems


def slugify(text: str, limit: int = 50) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", text.lower())).strip("-")[:limit].strip("-") or "test-case"


def clean_tag(tag: str) -> str:
    return re.sub(r"[;,]", "-", tag.lstrip("@"))


# ----------------------------------------------------------------------------------------------- api / secrets
def mask_text(text: str) -> tuple[str, int]:
    count = 0
    for rx, fn in SECRET_PATTERNS:
        text, n = rx.subn(fn, text)
        count += n
    return text, count


def mask_step(step: dict) -> tuple[dict, int]:
    """Copy of a Gherkin step with secret-looking values replaced by ***MASKED*** (placeholders are never masked)."""
    st = json.loads(json.dumps(step))
    st["text"], total = mask_text(st["text"])
    if st.get("dataTable"):
        for row in st["dataTable"]["rows"]:
            cells = row["cells"]
            for c in cells:
                c["value"], n = mask_text(c["value"])
                total += n
            if len(cells) == 2 and SECRET_NAME.match(cells[0]["value"].strip()) and \
                    cells[1]["value"] not in ("", MASK) and not PLACEHOLDER.fullmatch(cells[1]["value"].strip()) \
                    and MASK not in cells[1]["value"]:
                cells[1]["value"] = MASK
                total += 1
    if st.get("docString"):
        st["docString"]["content"], n = mask_text(st["docString"]["content"])
        total += n
    return st, total


def step_kind(step: dict) -> str:
    """'api', 'ui' or 'neutral' for one step, from its wording (API evidence wins)."""
    text = step["text"]
    if API_VERB.search(text) or API_TERM.search(text) or API_URL.search(" " + text):
        return "api"
    if step.get("docString") and (step["docString"].get("mediaType") or "").lower() in ("json", "xml", "graphql"):
        return "api"
    if UI_TERM.search(text):
        return "ui"
    return "neutral"


def path_matches(rel: str, patterns: list[str]) -> str | None:
    from fnmatch import fnmatch
    for p in patterns:
        if fnmatch(rel, p) or fnmatch(rel, p.rstrip("/*") + "/*") or rel.startswith(p.rstrip("*").rstrip("/") + "/"):
            return p
    return None


# ----------------------------------------------------------------------------------------------- model
def render_step(step: dict, repl) -> str:
    """Gherkin step -> text with its data table / doc string (§4.6), placeholders replaced by repl()."""
    text = repl(step["text"])
    if step.get("dataTable"):
        rows = [[repl(c["value"]) for c in r["cells"]] for r in step["dataTable"]["rows"]]
        if rows and len(rows[0]) == 2:
            text += "\n" + "\n".join(f"{a} = {b}" for a, b in rows)
        else:
            text += "\n" + "\n".join("| " + " | ".join(r) + " |" for r in rows)
    if step.get("docString"):
        text += "\n" + repl(step["docString"]["content"])
    return text


def raw_step_texts(step: dict) -> list[str]:
    parts = [step["text"]]
    if step.get("dataTable"):
        parts += [c["value"] for r in step["dataTable"]["rows"] for c in r["cells"]]
    if step.get("docString"):
        parts.append(step["docString"]["content"])
    return parts


def build_rows(steps: list[dict], repl) -> list[dict]:
    """Group Gherkin steps into ADO rows (§4): Given/When -> action row, Then -> expected of the previous row."""
    rows, prev = [], "Action"
    for st in steps:
        kt = st.get("keywordType", "Unknown")
        kind = prev if kt in ("Conjunction", "Unknown") else kt
        prev = kind
        line = f"{st['keyword'].strip()} {render_step(st, repl)}"
        sk = step_kind(st)
        media = (st.get("docString") or {}).get("mediaType") or ""
        if kind == "Outcome":
            if not rows:
                rows.append({"kind": "Verify", "source": "Verify the result", "sourceExpected": [], "verify": True,
                             "lines": [], "stepType": "neutral", "payload": ""})
            rows[-1]["sourceExpected"].append(line)
            rows[-1]["lines"].append(st["location"]["line"])
            if sk == "api" and rows[-1]["stepType"] != "api":
                rows[-1]["stepType"] = "api"
            rows[-1]["payload"] = rows[-1]["payload"] or media
        else:
            rows.append({"kind": "Given" if kind == "Context" else "When", "source": line, "sourceExpected": [],
                         "verify": False, "lines": [st["location"]["line"]], "stepType": sk, "payload": media})
    for r in rows:
        r["sourceExpected"] = "\n".join(r["sourceExpected"])
    return rows


class Model:
    """Everything deterministic about all feature files under featuresRoot."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.features: list[dict] = []
        self.outside: list[str] = []
        self.errors: list[str] = []
        self.id_map = read_id_map(cfg["_map"])
        titles_per_area: dict[str, dict[str, int]] = {}
        skip = ("target", "build", "node_modules", ".git", ".idea", "out")
        src_root = cfg["_src_root"]
        files = sorted(p for p in cfg["_features"].rglob("*.feature") if not any(
            part in skip for part in p.relative_to(cfg["_features"]).parts))
        for f in sorted(src_root.rglob("*.feature")):   # feature files in the same repo but outside featuresRoot
            if cfg["_features"] not in f.resolve().parents and not any(
                    part in skip for part in f.relative_to(src_root).parts):
                self.outside.append(f.relative_to(src_root).as_posix())
        for path in files:
            self.features.append(self._feature(path, titles_per_area))

    # -- one feature file
    def _feature(self, path: Path, titles_per_area: dict) -> dict:
        cfg = self.cfg
        rel = path.relative_to(cfg["_features"]).as_posix()
        rel_noext = rel[: -len(".feature")]
        src_rel = path.relative_to(cfg["_src_root"]).as_posix()
        flags: list[str] = []
        nodes = [cfg["adoProject"]] + [n for n in re.split(r"[\\/]", cfg["areaRoot"]) if n]
        for part in rel_noext.split("/"):
            clean = sanitize_node(part)
            if clean != part:
                flags.append(f"Area Path node renamed: '{part}' -> '{clean}'")
            nodes.append(clean)
        area = "\\".join(nodes)
        if len(nodes) > MAX_AREA_DEPTH:
            flags.append(f"ERROR Area Path has {len(nodes)} levels (max {MAX_AREA_DEPTH}): {area}")
        feat = {"rel": rel, "relNoExt": rel_noext, "source": src_rel, "areaPath": area, "flags": flags,
                "testCases": [], "commentedOut": []}
        text = path.read_text(encoding="utf-8-sig")
        for i, line in enumerate(text.splitlines(), 1):
            if COMMENTED_SCENARIO.match(line):
                feat["commentedOut"].append(f"{src_rel}:{i}: {line.strip()}")
        try:
            doc = Parser().parse(TokenScanner(text))
        except Exception as exc:  # noqa: BLE001 - parser raises CompositeParserException etc.
            feat["parseError"] = str(exc)
            self.errors.append(f"{src_rel}: parse error: {exc}")
            return feat
        feature = doc.get("feature")
        if not feature:
            feat["flags"].append("File has no Feature")
            return feat
        feat["name"] = feature["name"]
        titles = titles_per_area.setdefault(area.lower(), {})
        self._agent_titles = {}
        if cfg["_verifyTitles"]:   # titles are written by the agent in the wording file (§4a)
            wp = work_path(cfg, rel_noext, ".wording.json")
            if wp.exists():
                try:
                    self._agent_titles = {t["key"]: t.get("title", "") for t in
                                          json.loads(wp.read_text(encoding="utf-8"))["testCases"]}
                except (ValueError, KeyError):
                    self._agent_titles = {}
        keys: dict[str, int] = {}
        outline_no = 0
        f_bg: list[dict] = []
        items = []  # (scenario, rule, rule_bg)
        for child in feature["children"]:
            if "background" in child:
                f_bg = child["background"]["steps"]
            elif "scenario" in child:
                items.append((child["scenario"], None, []))
            elif "rule" in child:
                rule, r_bg = child["rule"], []
                for rc in rule["children"]:
                    if "background" in rc:
                        r_bg = rc["background"]["steps"]
                    elif "scenario" in rc:
                        items.append((rc["scenario"], rule, r_bg))
        for sc, rule, r_bg in items:
            is_outline = bool(sc.get("examples")) or "Outline" in sc["keyword"] or "Template" in sc["keyword"]
            if is_outline:
                outline_no += 1
            feat["testCases"].append(self._test_case(feat, feature, sc, rule, f_bg + r_bg, is_outline,
                                                     outline_no, titles, keys))
        return feat

    # -- one scenario / outline
    def _test_case(self, feat, feature, sc, rule, bg_steps, is_outline, outline_no, titles, keys) -> dict:
        cfg = self.cfg
        flags: list[str] = []
        steps = bg_steps + sc["steps"]
        where = f"{feat['source']}:{sc['location']['line']}"
        name = sc["name"].strip()
        # key (stable identity for the ID map)
        key = f"{feat['rel']}::{(rule['name'].strip() + ' > ') if rule else ''}{name or '(unnamed)'}"
        keys[key] = keys.get(key, 0) + 1
        if keys[key] > 1:
            key += f" #{keys[key]}"
        # title: from the scenario name, or (titleStyle: verify) the agent's "Verify that ..." title
        if not name:
            derived = f"{feature['name'].strip()} - scenario at line {sc['location']['line']}"
            flags.append("Empty scenario name")
        else:
            derived = PLACEHOLDER.sub(lambda m: f"[{m.group(1)}]", name) if is_outline else name
        derived = derived[:MAX_TITLE]
        title, title_problems = derived, []
        if cfg["_verifyTitles"]:
            agent = (self._agent_titles.get(key) or "").strip()
            title_problems = check_title(agent)
            if not title_problems:
                title = agent
        n = titles.get(title.lower(), 0) + 1
        titles[title.lower()] = n
        if n > 1:
            if cfg["_verifyTitles"] and not title_problems:
                title_problems.append(f"same title as another test case in this Area Path - make it unique: "
                                      f"'{title}'")
            suffix = f" ({n})"
            title = title[: MAX_TITLE - len(suffix)] + suffix
            flags.append(f"Duplicate title in Area Path, renamed to '{title}'")
        # tags
        tag_prefix = "@" + cfg["tcTagPrefix"]
        all_tags = list(feature["tags"]) + (list(rule["tags"]) if rule else []) + list(sc["tags"])
        for ex in sc.get("examples", []):
            all_tags += ex["tags"]
        existing_id = None
        gherkin_tags: list[str] = []   # kept in the Description only
        for t in all_tags:
            m = re.fullmatch(re.escape(tag_prefix) + r"(\d+)", t["name"])
            if m:
                if t in sc["tags"]:
                    existing_id = m.group(1)
                continue
            if t["name"] not in gherkin_tags:
                gherkin_tags.append(t["name"])
        # ADO tags = the breadcrumb after featuresRoot: folders + file name (same names as the Area Path nodes)
        tags: list[str] = []
        for part in feat["relNoExt"].split("/"):
            c = clean_tag(sanitize_node(part))
            if c not in tags:
                tags.append(c)
        # secrets are masked before anything else looks at the steps (§3b)
        if cfg["_mask"]:
            masked = []
            for st in steps:
                m_st, n = mask_step(st)
                if n:
                    flags.append(f"Secret-like value masked in step at line {st['location']['line']} ({n}x)")
                masked.append(m_st)
            steps = masked
        test_type, type_reason = self._test_type(feat, [t["name"] for t in all_tags], steps, flags)
        # literal @ anywhere in the test case steps -> ADO would see a false parameter
        for st in steps:
            for part in raw_step_texts(st):
                for m in re.finditer(r"@\w+", part):
                    flags.append(f"Literal '{m.group(0)}' in step at line {st['location']['line']} "
                                 f"- ADO will treat it as a parameter")
        self._api_flags(steps, flags)
        tc = {"key": key, "title": title, "originalName": sc["name"], "line": sc["location"]["line"],
              "where": where, "isOutline": is_outline, "areaPath": feat["areaPath"], "tags": tags,
              "gherkinTags": gherkin_tags,
              "rule": rule["name"].strip() if rule else "", "existingTagId": existing_id, "flags": flags,
              "testType": test_type, "testTypeReason": type_reason,
              "scenarioTitle": derived, "titleProblems": title_problems}
        if is_outline:
            self._outline(tc, sc, steps, outline_no, feat)
        else:
            tc["rows"] = build_rows(steps, lambda s: s)
        if not any(r["sourceExpected"] for r in tc["rows"]):
            flags.append("No expected result (no Then step)")
        if not tc["rows"]:
            flags.append("Scenario has no steps")
        tc["description"] = self._description(tc, feature, rule, sc, feat)
        mapped = self.id_map.get(key)
        tc["adoId"] = existing_id or (mapped["ADO ID"] if mapped else "")
        if existing_id and mapped and mapped["ADO ID"] != existing_id:
            flags.append(f"ERROR ID conflict: tag {tag_prefix}{existing_id} vs ID map {mapped['ADO ID']}")
        # existing items keep their current ADO state (the import rejects invalid state transitions)
        tc["state"] = (mapped.get("State") if mapped and tc["adoId"] == mapped["ADO ID"] else "") or cfg["state"]
        tc["assignedTo"] = (mapped.get("Assigned To") if mapped and tc["adoId"] == mapped["ADO ID"] else "") or \
            cfg["assignedTo"]
        return tc

    def _test_type(self, feat, tag_names, steps, flags) -> tuple[str, str]:
        """'api', 'ui' or 'hybrid' (§3b). Tags win, then apiPaths, then the step wording."""
        cfg = self.cfg
        names = {t.lstrip("@").lower() for t in tag_names}
        kinds = [step_kind(st) for st in steps]
        n_api, n_ui = kinds.count("api"), kinds.count("ui")
        api_tag = sorted(names & cfg["_apiTags"])
        ui_tag = sorted(names & cfg["_uiTags"])
        if api_tag and ui_tag:
            return "hybrid", f"tags @{api_tag[0]} and @{ui_tag[0]}"
        if api_tag:
            if n_ui:
                flags.append(f"Tagged @{api_tag[0]} but {n_ui} step(s) look like UI steps - check the test type")
            return "api", f"tag @{api_tag[0]}"
        if ui_tag:
            if n_api:
                flags.append(f"Tagged @{ui_tag[0]} but {n_api} step(s) look like API steps - check the test type")
            return "ui", f"tag @{ui_tag[0]}"
        pattern = path_matches(feat["rel"], cfg["_apiPaths"])
        if pattern:
            if n_ui:
                flags.append(f"In API folder '{pattern}' but {n_ui} step(s) look like UI steps - check the test type")
            return "api", f"folder rule {pattern}"
        if n_api and n_ui:
            flags.append(f"Hybrid: {n_api} API step(s) and {n_ui} UI step(s) - check the test type")
            return "hybrid", f"detected from steps: {n_api} API, {n_ui} UI"
        if n_api:
            flags.append(f"Detected as API from {n_api} step(s) without an API tag or folder rule - check the test type")
            return "api", f"detected from steps: {n_api} API"
        return "ui", "default (no API evidence)"

    def _api_flags(self, steps, flags):
        for st in steps:
            line = st["location"]["line"]
            for ref in FILE_REF.findall(st["text"]):
                flags.append(f"Step at line {line} refers to file '{ref}' from the automation repo - "
                             f"the tester needs access to it")
            content = (st.get("docString") or {}).get("content", "")
            if MARKUP.search(content) or MARKUP.search(st["text"]):
                flags.append(f"Step at line {line} contains XML/HTML markup - ADO steps are HTML; check it shows "
                             f"correctly after the import")
            size = len(render_step(st, lambda s: s))
            if size > LARGE_CELL:
                flags.append(f"Step at line {line} is {size} chars (payload) - hard to read in ADO; consider "
                             f"referencing a file instead")
            if st.get("dataTable"):
                for row in st["dataTable"]["rows"]:
                    cells = row["cells"]
                    if len(cells) == 2 and PASSWORD_NAME.search(cells[0]["value"]):
                        flags.append(f"Password-like data at line {line} is copied to ADO (not masked)")

    def _mask_value(self, header: str, value: str, flags: list[str]) -> str:
        if PASSWORD_NAME.search(header):
            msg = f"Password-like Examples column '{header}' is copied to ADO (not masked)"
            if msg not in flags:
                flags.append(msg)
        if not self.cfg["_mask"] or not value:
            return value
        if SECRET_NAME.match(header.strip()):
            msg = f"Examples column '{header}' looks like a secret - values masked"
            if msg not in flags:
                flags.append(msg)
            return MASK
        masked, n = mask_text(value)
        if n:
            msg = f"Secret-like value masked in Examples column '{header}'"
            if msg not in flags:
                flags.append(msg)
        return masked

    def _outline(self, tc, sc, steps, outline_no, feat):
        flags = tc["flags"]
        examples = sc.get("examples", [])
        headers: list[str] = []
        for ex in examples:
            for c in (ex.get("tableHeader") or {}).get("cells", []):
                if c["value"] not in headers:
                    headers.append(c["value"])
        if len({tuple(c["value"] for c in (ex.get("tableHeader") or {}).get("cells", [])) for ex in examples}) > 1:
            flags.append("Examples blocks have different columns - merged, missing cells left empty")
        if len({tuple(sorted(t["name"] for t in ex["tags"])) for ex in examples}) > 1:
            flags.append("Examples blocks have different tags - see Description for which rows came from which block")
        used_in: dict[str, set] = {}
        prev = "Action"
        for st in steps:
            kt = st.get("keywordType", "Unknown")
            prev = prev if kt in ("Conjunction", "Unknown") else kt
            kind = {"Context": "setup", "Action": "action", "Outcome": "expected"}.get(prev, "action")
            for part_i, part in enumerate(raw_step_texts(st)):
                where = "text" if part_i == 0 else ("docString" if st.get("docString") and
                                                    part == st["docString"]["content"] else "dataTable")
                for m in PLACEHOLDER.finditer(part):
                    used_in.setdefault(m.group(1), set()).add(f"{st['keyword'].strip()}:{where}:{kind}")
        in_title = {m.group(1) for m in PLACEHOLDER.finditer(sc["name"])}
        undefined = [p for p in used_in if p not in headers]
        for p in undefined:
            flags.append(f"Placeholder <{p}> has no Examples column - kept as literal text")
        params: dict[str, str] = {}
        taken: set[str] = set()
        for h in headers:
            if h in used_in:
                name = base = sanitize_param(h)
                i = 2
                while name.lower() in taken:
                    name, i = f"{base}_{i}", i + 1
                taken.add(name.lower())
                params[h] = name
                if name != h:
                    flags.append(f"Parameter <{h}> renamed to @{name}")
        adjacency: set[str] = set()

        def repl(text: str) -> str:
            def sub(m):
                ph = m.group(1)
                if ph not in params:
                    return m.group(0)
                nxt = text[m.end(): m.end() + 1]
                if nxt and re.match(r"[A-Za-z0-9_]", nxt):
                    adjacency.add(ph)
                    return f"@{params[ph]} "
                return f"@{params[ph]}"
            return PLACEHOLDER.sub(sub, text)

        tc["rows"] = build_rows(steps, repl)
        for ph in sorted(adjacency):
            flags.append(f"<{ph}> was directly followed by text - a space was inserted after @{params[ph]}")
        columns = [params[h] for h in headers if h in params]
        values, blocks, row_no = [], [], 0
        for ex in examples:
            hdr = [c["value"] for c in (ex.get("tableHeader") or {}).get("cells", [])]
            body = ex.get("tableBody") or []
            start = row_no + 1
            for r in body:
                cells = dict(zip(hdr, (c["value"] for c in r["cells"])))
                values.append([self._mask_value(h, cells.get(h, ""), flags) for h in headers if h in params])
                row_no += 1
            label = f'"{ex["name"].strip()}"' if ex["name"].strip() else "(unnamed)"
            tag_txt = " ".join(t["name"] for t in ex["tags"])
            rng = f"rows {start}-{row_no}" if row_no > start else (f"row {start}" if row_no == start else "no rows")
            blocks.append(f"{rng} {label}{(' ' + tag_txt) if tag_txt else ''}")
        if not values:
            flags.append("ERROR Scenario Outline has no Examples rows")
        not_params = [h for h in headers if h not in params]
        np_values = {}
        for h in not_params:
            vals = []
            for ex in examples:
                hdr = [c["value"] for c in (ex.get("tableHeader") or {}).get("cells", [])]
                if h in hdr:
                    idx = hdr.index(h)
                    vals += [self._mask_value(h, r["cells"][idx]["value"], flags) for r in ex.get("tableBody") or []]
            np_values[h] = vals
            flags.append(f"Examples column '{h}' is {'title-only' if h in in_title else 'unused'} - not a parameter")
        slug = slugify(tc["title"])
        tc.update({
            "parameters": columns,
            "placeholderToParam": params,
            "placeholderUse": {k: sorted(v) for k, v in used_in.items()},
            "placeholdersInTitle": sorted(in_title),
            "notParameters": np_values,
            "values": values,
            "examplesBlocks": blocks,
            "examplesDetail": [{"name": ex["name"], "tags": [t["name"] for t in ex["tags"]],
                                "columns": [c["value"] for c in (ex.get("tableHeader") or {}).get("cells", [])],
                                "rows": len(ex.get("tableBody") or [])} for ex in examples],
            "valuesFile": f"{feat['relNoExt']}.parameters/{outline_no:02d}-{slug}.csv",
        })

    def _description(self, tc, feature, rule, sc, feat) -> str:
        lines = [f"Source: {tc['where']}", f"Feature: {feature['name'].strip()}"]
        if rule:
            lines.append(f"Rule: {rule['name'].strip()}")
        lines.append(f"Scenario: {sc['name'].strip()}"
                     f" ({'Scenario Outline - parameterized' if tc['isOutline'] else 'Scenario'})")
        if tc["gherkinTags"]:
            lines.append("Gherkin tags: " + " ".join(tc["gherkinTags"]))
        context = " ".join(x.strip() for x in [feature.get("description", ""),
                                                rule.get("description", "") if rule else ""] if x.strip())
        if context:
            lines.append("Context: " + re.sub(r"\s*\n\s*", " ", context))
        type_label = {"api": "API", "ui": "UI", "hybrid": "Hybrid (API + UI)"}[tc["testType"]]
        lines.append(f"Test type: {type_label} ({tc['testTypeReason']})")
        if tc["testType"] in ("api", "hybrid"):
            lines.append("Manual execution: API steps are sent with an API client (e.g. Postman) against the "
                         "environment under test.")
        if tc["isOutline"]:
            lines.append("Parameters: " + ", ".join("@" + p for p in tc["parameters"]))
            lines.append(f"Iterations: {len(tc['values'])} | values file: {tc['valuesFile']}")
            lines.append("Examples blocks: " + "; ".join(tc["examplesBlocks"]))
            if tc["notParameters"]:
                lines.append("Not parameters (title-only/unused columns): " + "; ".join(
                    f"{k}={', '.join(v)}" for k, v in tc["notParameters"].items()))
            lines.append("Values:")
            lines.append(" | ".join(tc["parameters"]))
            for row in tc["values"][:MAX_DESCRIPTION_VALUE_ROWS]:
                lines.append(" | ".join(row))
            if len(tc["values"]) > MAX_DESCRIPTION_VALUE_ROWS:
                lines.append(f"... {len(tc['values']) - MAX_DESCRIPTION_VALUE_ROWS} more rows in the values file")
        return "\n".join(lines)

    def in_scope(self, scope: str | None):
        if not scope:
            return self.features
        s = scope.replace("\\", "/").strip("/")
        root_rel = self.cfg["featuresRoot"].replace("\\", "/").strip("/")
        if s.startswith(root_rel + "/"):
            s = s[len(root_rel) + 1:]
        return [f for f in self.features if f["rel"] == s or f["rel"].startswith(s.rstrip("/") + "/")]


# ----------------------------------------------------------------------------------------------- id map
def read_id_map(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["Key"]: r for r in csv.DictReader(fh) if r.get("Key") and r.get("ADO ID")}


def write_id_map(path: Path, entries: dict[str, dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, MAP_COLUMNS, lineterminator="\r\n")
        w.writeheader()
        for key in sorted(entries):
            w.writerow({c: entries[key].get(c, "") for c in MAP_COLUMNS})


# ----------------------------------------------------------------------------------------------- io helpers
def work_path(cfg, rel_noext: str, suffix: str) -> Path:
    return cfg["_out"] / "_work" / f"{rel_noext}{suffix}"


def csv_path(cfg, feat) -> Path:
    return cfg["_out"] / f"{feat['relNoExt']}.csv"


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def csv_text(rows: list[list[str]], header=True) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    if header:
        w.writerow(COLUMNS)
    w.writerows(rows)
    return buf.getvalue()


def read_csv_rows(path: Path) -> tuple[list[str], list[dict]]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    return reader.fieldnames or [], list(reader)


def params_in(text: str) -> set[str]:
    return set(PARAM_TOKEN.findall(text or ""))


def say(msg=""):
    sys.stdout.write(msg + "\n")


# ----------------------------------------------------------------------------------------------- plan
def cmd_plan(cfg, args):
    model = Model(cfg)
    feats = model.in_scope(args.scope)
    kept = reset = 0
    for feat in feats:
        write_json(work_path(cfg, feat["relNoExt"], ".plan.json"), feat)
        wpath = work_path(cfg, feat["relNoExt"], ".wording.json")
        old = {}
        if wpath.exists():
            try:
                old = {tc["key"]: tc for tc in json.loads(wpath.read_text(encoding="utf-8"))["testCases"]}
            except (ValueError, KeyError):
                old = {}
        out_tcs = []
        for tc in feat["testCases"]:
            prev = old.get(tc["key"])
            same = prev and [(r.get("source"), r.get("sourceExpected")) for r in prev["rows"]] == \
                [(r["source"], r["sourceExpected"]) for r in tc["rows"]]
            rows = []
            for i, r in enumerate(tc["rows"]):
                action = prev["rows"][i].get("action", "") if same else ("Verify the result" if r["verify"] else "")
                expected = prev["rows"][i].get("expected", "") if same else ""
                row = {"step": i + 1, "stepType": r["stepType"]}
                if r["payload"]:
                    row["payload"] = r["payload"]
                row.update({"source": r["source"], "sourceExpected": r["sourceExpected"],
                            "action": action, "expected": expected})
                rows.append(row)
            if same:
                kept += 1
            elif prev:
                reset += 1
            if cfg["_verifyTitles"]:
                title = (prev.get("title", "") if same else "") if prev else ""
            else:
                title = tc["title"]
            out_tcs.append({"key": tc["key"], "scenarioTitle": tc["scenarioTitle"], "title": title,
                            "testType": tc["testType"], "parameters": tc.get("parameters", []), "rows": rows})
        how = ("Fill 'action' and 'expected' for every row (rules: instructions §4). Keep every @parameter, "
               "quoted value, table and doc-string text from 'source'/'sourceExpected'. Leave 'expected' empty "
               "when 'sourceExpected' is empty. Don't add, remove or reorder rows.")
        if cfg["_verifyTitles"]:
            how += (" Also write 'title' for every test case (instructions §4a): 'Verify that <expected result> "
                    "when <test condition>', from this scenario only. 'scenarioTitle' is read-only.")
        write_json(wpath, {"feature": feat["source"], "howTo": how, "testCases": out_tcs})
    write_inventory(cfg, model)
    tcs = [tc for f in feats for tc in f["testCases"]]
    say(f"Planned {len(feats)} feature file(s), {len(tcs)} test case(s) "
        f"({sum(tc['isOutline'] for tc in tcs)} parameterized), "
        f"{sum(len(tc['rows']) for tc in tcs)} step row(s).")
    say(f"Wording templates: {cfg['_out'] / '_work'} (kept {kept} unchanged test case(s), reset {reset} changed)")
    todo = sum(1 for f in feats for tc in json.loads(work_path(cfg, f['relNoExt'], '.wording.json')
                                                     .read_text(encoding='utf-8'))["testCases"]
               for r in tc["rows"] if not r["action"])
    say(f"Rows still needing wording: {todo}")
    say(f"Placeholder inventory: {cfg['_out'] / '_work' / 'placeholder-inventory.md'}")
    for e in model.errors:
        say("ERROR " + e)
    return 1 if model.errors else 0


def write_inventory(cfg, model: Model):
    L = ["# Placeholder inventory (generated by `ado_testcases.py plan` - do not edit)", "",
         "Deterministic facts only. The agent writes its interpretation (roles, same-entity judgement) in "
         "`_placeholder-analysis.md`.", ""]
    variants: dict[str, set] = {}
    tables: dict[str, list] = {}
    problems = []
    for feat in model.features:
        for tc in feat["testCases"]:
            if not tc["isOutline"]:
                for fl in tc["flags"]:
                    if fl.startswith(API_FLAG_PREFIXES):
                        problems.append(f"{tc['where']} - {fl}")
                continue
            L.append(f"## {tc['title']}  (`{tc['where']}`)")
            L.append("")
            for ex in tc["examplesDetail"]:
                for col in ex["columns"]:
                    variants.setdefault(re.sub(r"[^a-z0-9]", "", col.lower()), set()).add(col)
            L.append("| Placeholder | ADO parameter | Used in (keyword:location:meaning) |")
            L.append("|---|---|---|")
            for ph, uses in tc["placeholderUse"].items():
                L.append(f"| `<{ph}>` | {('@' + tc['placeholderToParam'][ph]) if ph in tc['placeholderToParam'] else '*(undefined)*'} | {', '.join(uses)} |")
                variants.setdefault(re.sub(r"[^a-z0-9]", "", ph.lower()), set()).add(ph)
            for ph in tc["placeholdersInTitle"]:
                if ph not in tc["placeholderUse"]:
                    L.append(f"| `<{ph}>` | *(title only)* | outline name |")
            L.append("")
            for ex in tc["examplesDetail"]:
                label = '"' + ex["name"] + '"' if ex["name"] else "(unnamed)"
                L.append(f"- Examples {label} {' '.join(ex['tags'])}: columns {ex['columns']}, {ex['rows']} row(s)")
            specials = sorted({v for row in tc["values"] for v in row if v.strip().lower() in SPECIAL_TOKENS})
            if specials:
                L.append(f"- Empty/special values present: {', '.join(repr(s) for s in specials)}")
            sig = hashlib.sha1(json.dumps([tc["parameters"], tc["values"]]).encode()).hexdigest()[:10]
            tables.setdefault(sig, []).append(tc["where"])
            for fl in tc["flags"]:
                if not fl.startswith("Duplicate title"):
                    problems.append(f"{tc['where']} - {fl}")
            L.append("")
    L += ["## Test types", "", "| Test case | Type | Reason | API / UI rows |", "|---|---|---|---|"]
    for feat in model.features:
        for tc in feat["testCases"]:
            kinds = [r["stepType"] for r in tc["rows"]]
            L.append(f"| {tc['title']} (`{tc['where']}`) | {tc['testType']} | {tc['testTypeReason']} | "
                     f"{kinds.count('api')} / {kinds.count('ui')} |")
    L.append("")
    L += ["## Naming variants (same letters, different spelling)", ""]
    groups = [sorted(v) for v in variants.values() if len(v) > 1]
    L += [f"- {', '.join('`<' + x + '>`' for x in g)}" for g in groups] or ["- none"]
    L += ["", "## Identical Examples tables (Shared Parameters candidates)", ""]
    dups = [w for w in tables.values() if len(w) > 1]
    L += [f"- {', '.join(w)}" for w in dups] or ["- none"]
    L += ["", "## Problems", ""]
    L += [f"- {p}" for p in problems] or ["- none"]
    path = cfg["_out"] / "_work" / "placeholder-inventory.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------- build
def check_wording(tc, w) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one test case's wording."""
    errors = [f"{tc['where']}: {p}" for p in tc.get("titleProblems", [])]
    warnings = []
    if w is None:
        return errors + [f"{tc['where']}: no wording for test case '{tc['title']}' - run plan and fill the wording "
                         f"file"], []
    if len(w["rows"]) != len(tc["rows"]):
        return errors + [f"{tc['where']}: wording has {len(w['rows'])} rows, plan has {len(tc['rows'])} - re-run "
                         f"plan"], []
    for i, (r, wr) in enumerate(zip(tc["rows"], w["rows"]), 1):
        if (wr.get("source"), wr.get("sourceExpected")) != (r["source"], r["sourceExpected"]):
            errors.append(f"{tc['where']} step {i}: wording is stale (feature changed) - re-run plan")
            continue
        if not wr.get("action", "").strip():
            errors.append(f"{tc['where']} step {i}: 'action' is empty")
        if r["sourceExpected"] and not wr.get("expected", "").strip():
            errors.append(f"{tc['where']} step {i}: 'expected' is empty but the source has a Then step")
        if not r["sourceExpected"] and wr.get("expected", "").strip():
            errors.append(f"{tc['where']} step {i}: 'expected' is filled but the source has no Then step (invented)")
        for field, src in (("action", r["source"]), ("expected", r["sourceExpected"])):
            got, want = params_in(wr.get(field, "")), params_in(src)
            if field == "action" and r["verify"]:
                want = set()
            if got != want:
                errors.append(f"{tc['where']} step {i} {field}: @parameters {sorted(got)} != source {sorted(want)}")
            for q in QUOTED.findall(src):
                if q and q not in wr.get(field, ""):
                    warnings.append(f"{tc['where']} step {i} {field}: quoted value \"{q}\" not found verbatim")
            for extra in src.split("\n")[1:]:
                if extra.startswith(("Then ", "And ", "But ", "* ")) and field == "expected":
                    continue
                if extra.strip() and extra.strip() not in wr.get(field, ""):
                    warnings.append(f"{tc['where']} step {i} {field}: table/doc-string line not kept verbatim: "
                                    f"{extra.strip()[:60]}")
    return errors, warnings


def tc_csv_rows(tc, wording) -> list[list[str]]:
    base = {"ID": tc["adoId"], "Work Item Type": "Test Case", "Title": tc["title"], "Area Path": tc["areaPath"],
            "Assigned To": tc["assignedTo"], "State": tc["state"], "Tags": "; ".join(tc["tags"]),
            "Description": tc["description"]}
    rows = []
    if not tc["rows"]:
        return [[base.get(c, "") for c in COLUMNS]]
    for i, wr in enumerate(wording["rows"], 1):
        rec = dict(base, **{"Test Step": str(i), "Step Action": wr["action"].strip(),
                            "Step Expected": wr["expected"].strip()})
        rows.append([rec.get(c, "") for c in COLUMNS])
    return rows


def cmd_build(cfg, args):
    model = Model(cfg)
    feats = model.in_scope(args.scope)
    all_errors, all_warnings, written = list(model.errors), [], []
    for feat in feats:
        if feat.get("parseError"):
            continue
        wpath = work_path(cfg, feat["relNoExt"], ".wording.json")
        wording = {}
        if wpath.exists():
            wording = {tc["key"]: tc for tc in json.loads(wpath.read_text(encoding="utf-8"))["testCases"]}
        rows, errs = [], []
        for tc in feat["testCases"]:
            w = wording.get(tc["key"])
            if args.use_source_text and (w is None or any(not r.get("action") for r in w["rows"])):
                w = {"rows": [{"source": r["source"], "sourceExpected": r["sourceExpected"],
                               "action": (w["rows"][i]["action"] if w and i < len(w["rows"]) and
                                          w["rows"][i].get("action") else r["source"]),
                               "expected": (w["rows"][i]["expected"] if w and i < len(w["rows"]) and
                                            w["rows"][i].get("expected") else r["sourceExpected"])}
                              for i, r in enumerate(tc["rows"])]}
                all_warnings.append(f"{tc['where']}: --use-source-text filled missing wording with Gherkin text")
            e, wn = check_wording(tc, w)
            errs += e
            all_warnings += wn
            if not e:
                rows += tc_csv_rows(tc, w)
        for fl in feat["flags"]:
            if fl.startswith("ERROR"):
                errs.append(f"{feat['source']}: {fl}")
        for tc in feat["testCases"]:
            errs += [f"{tc['where']}: {fl}" for fl in tc["flags"] if fl.startswith("ERROR")]
        out = csv_path(cfg, feat)
        pdir = cfg["_out"] / f"{feat['relNoExt']}.parameters"
        if pdir.exists():
            shutil.rmtree(pdir)
        if errs:
            # never leave a partial or out-of-date CSV behind - it would end up in _all-test-cases.csv
            if out.exists():
                out.unlink()
                errs.append(f"{feat['source']}: previous {out.name} removed so it can't be imported by mistake")
            all_errors += errs
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(csv_text(rows), encoding="utf-8", newline="")
        for tc in feat["testCases"]:
            if tc["isOutline"] and tc["values"]:
                p = cfg["_out"] / tc["valuesFile"]
                p.parent.mkdir(parents=True, exist_ok=True)
                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\r\n")
                w.writerow(tc["parameters"])
                w.writerows(tc["values"])
                p.write_text(buf.getvalue(), encoding="utf-8", newline="")
        written.append(feat)
    write_combined(cfg, model)
    write_report(cfg, model, feats, written, all_errors, all_warnings)
    say(f"Wrote {len(written)} of {len(feats)} feature CSV(s) to {cfg['_out']}")
    say(f"Report: {cfg['_out'] / '_conversion-report.md'}")
    for e in all_errors:
        say("ERROR " + e)
    for w in all_warnings[:40]:
        say("WARN  " + w)
    if len(all_warnings) > 40:
        say(f"WARN  ... {len(all_warnings) - 40} more warnings in the report")
    return 1 if all_errors else 0


def group_rows(rows: list[dict]) -> list[list[dict]]:
    """Split CSV rows into test cases (consecutive rows of one test case)."""
    groups: list[list[dict]] = []
    for r in rows:
        if groups and r["Title"] == groups[-1][0]["Title"] and r["Test Step"] not in ("", "1"):
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups


def test_type_of(group: list[dict]) -> str:
    """From the Description line 'Test type: API | UI | Hybrid (API + UI) (...)'."""
    m = re.search(r"^Test type: (API|UI|Hybrid)\b", group[0]["Description"], re.M)
    return m.group(1).lower() if m else "ui"


def write_combined(cfg, model: Model):
    out = cfg["_out"]
    for old in out.glob("_all-*test-cases*.csv"):
        old.unlink()
    groups: list[list[dict]] = []
    for feat in model.features:
        p = csv_path(cfg, feat)
        if p.exists():
            groups += group_rows(read_csv_rows(p)[1])
    sets = {"_all-test-cases": groups}
    if cfg["_split"]:
        for tt in ("api", "ui", "hybrid"):
            sel = [g for g in groups if test_type_of(g) == tt]
            if sel:
                sets[f"_all-{tt}-test-cases"] = sel
    header_size = len(csv_text([]).encode("utf-8"))
    for base, sel in sets.items():
        chunks, cur, size = [], [], 0
        for g in sel:
            text = csv_text([[r.get(c, "") for c in COLUMNS] for r in g], header=False)
            n = len(text.encode("utf-8"))
            if cur and size + n + header_size > MAX_IMPORT_BYTES:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(text)
            size += n
        if cur or not chunks:
            chunks.append(cur)
        names = [f"{base}.csv"] if len(chunks) == 1 else [f"{base}-part-{i}.csv" for i in range(1, len(chunks) + 1)]
        for name, chunk in zip(names, chunks):
            (out / name).write_text(csv_text([]) + "".join(chunk), encoding="utf-8", newline="")
    areas = sorted({f["areaPath"] for f in model.features if csv_path(cfg, f).exists()}, key=str.lower)
    (out / "_area-paths.txt").write_text("\n".join(areas) + ("\n" if areas else ""), encoding="utf-8")


def write_report(cfg, model, feats, written, errors, warnings):
    tcs = [tc for f in feats for tc in f["testCases"]]
    outlines = [tc for tc in tcs if tc["isOutline"]]
    L = ["# Conversion report", "",
         f"Generated {_dt.datetime.now().isoformat(timespec='seconds')} by `ado_testcases.py build`. "
         "The CSV has **not** been imported into ADO by this tool.", "",
         "## Settings", "",
         f"- ADO_PROJECT: `{cfg['adoProject']}`", f"- AREA_ROOT: `{cfg['areaRoot']}`",
         f"- FEATURES_ROOT: `{cfg['featuresRoot']}`", f"- OUTPUT_DIR: `{cfg['outputDir']}`",
         f"- ASSIGNED_TO: `{cfg['assignedTo']}`" + ("  **(empty - the import wizard may require a user)**"
                                                    if not cfg["assignedTo"] else ""),
         f"- ID map: `{cfg['idMapFile']}`", "",
         "## Counts", "",
         f"- Feature files: {len(feats)} (CSV written: {len(written)})",
         f"- Test cases: {len(tcs)} - scenarios: {len(tcs) - len(outlines)}, parameterized outlines: {len(outlines)}",
         f"- Iterations (Examples rows): {sum(len(tc['values']) for tc in outlines)}",
         f"- Step rows: {sum(max(1, len(tc['rows'])) for tc in tcs)}",
         f"- Test cases with an ADO ID (will UPDATE existing items): {sum(1 for tc in tcs if tc['adoId'])}",
         f"- Test cases without an ADO ID (will CREATE new items): {sum(1 for tc in tcs if not tc['adoId'])}",
         "- Test types: " + ", ".join(f"{t.upper() if t != 'hybrid' else 'Hybrid'} {sum(1 for tc in tcs if tc['testType'] == t)}"
                                      for t in ("ui", "api", "hybrid")) +
         (" - separate import files per type: `_all-<type>-test-cases.csv`" if cfg["_split"] else ""),
         f"- Secret masking: {'on' if cfg['_mask'] else '**OFF**'}", "",
         "| Feature file | Area Path | Test cases | Outlines | Iterations | Steps |", "|---|---|---|---|---|---|"]
    for f in feats:
        ft = f["testCases"]
        L.append(f"| `{f['source']}` | `{f['areaPath']}` | {len(ft)} | {sum(t['isOutline'] for t in ft)} | "
                 f"{sum(len(t.get('values', [])) for t in ft)} | {sum(max(1, len(t['rows'])) for t in ft)} |")
    L += ["", "## Before importing", "",
          "1. Create every Area Path in `_area-paths.txt` (Project Settings > Boards > Project configuration > Areas).",
          "2. Import one feature CSV first and check it, then `_all-test-cases.csv`.",
          "3. Rows with an ID update existing test cases and **replace all their steps**. Rows without an ID create new "
          "test cases - importing the same file twice creates duplicates.",
          "4. After importing new test cases: export them from ADO to CSV and run "
          "`python .github/scripts/ado_testcases.py sync-ids --export <file>` so the next run updates instead of "
          "duplicating.", ""]
    if outlines:
        L += ["## Parameter paste checklist", "",
              "After the import, open each test case below, go to **Parameter values**, and paste the header + rows "
              "from its values file (open it in Excel, copy, paste). Check with the first one that ADO lists the "
              "`@parameters` from the imported steps. ADO shows one outcome per test case: if any iteration fails, "
              "the test case shows Failed.", "",
              "| Title | Area Path | Values file | Iterations |", "|---|---|---|---|"]
        L += [f"| {tc['title']} | `{tc['areaPath']}` | `{tc['valuesFile']}` | {len(tc['values'])} |" for tc in outlines]
        L.append("")
    typed = [tc for tc in tcs if tc["testType"] != "ui" or not tc["testTypeReason"].startswith("default")]
    if typed:
        L += ["## Test types (API / hybrid / tagged)", "",
              "Check these, especially the ones detected from the step wording. To fix one, add an API/UI tag "
              "to the scenario or an `apiPaths` rule to the config.", "",
              "| Title | Type | Reason |", "|---|---|---|"]
        L += [f"| {tc['title']} | {tc['testType']} | {tc['testTypeReason']} |" for tc in typed]
        L.append("")
    L += ["## Needs review", ""]
    review = [(tc["where"], fl) for tc in tcs for fl in tc["flags"]]
    review += [(f["source"], fl) for f in feats for fl in f["flags"]]
    review += [(c.split(": ", 1)[0], "Commented-out scenario - not converted: " + c.split(": ", 1)[1])
               for f in feats for c in f["commentedOut"]]
    review += [(o, "Outside featuresRoot - not converted") for o in model.outside]
    L += [f"- `{w}` - {fl}" for w, fl in review] or ["- nothing"]
    if errors:
        L += ["", "## Errors (feature CSV not written)", ""] + [f"- {e}" for e in errors]
    if warnings:
        L += ["", "## Wording warnings (check against the source)", ""] + [f"- {w}" for w in warnings]
    L += ["", "## Agent notes", "", "_The agent adds its review notes below this line._", ""]
    (cfg["_out"] / "_conversion-report.md").write_text("\n".join(L), encoding="utf-8")


# ----------------------------------------------------------------------------------------------- validate
def cmd_validate(cfg, args):
    model = Model(cfg)
    feats = model.in_scope(args.scope)
    errors, warnings = list(model.errors), []
    out = cfg["_out"]
    all_rows: list[tuple] = []
    for feat in feats:
        if feat.get("parseError"):
            continue
        p = csv_path(cfg, feat)
        if not p.exists():
            errors.append(f"{feat['source']}: missing {p}")
            continue
        if p.read_bytes().startswith(b"\xef\xbb\xbf"):
            warnings.append(f"{p.name}: has a UTF-8 BOM (rules say UTF-8 without BOM)")
        try:
            header, rows = read_csv_rows(p)
        except (csv.Error, UnicodeDecodeError) as exc:
            errors.append(f"{p}: cannot be read as CSV: {exc}")
            continue
        if header != COLUMNS:
            errors.append(f"{p.name}: header {header} != {COLUMNS}")
            continue
        all_rows += [tuple(r[c] for c in COLUMNS) for r in rows]
        groups: list[list[dict]] = []
        for r in rows:
            if groups and r["Title"] == groups[-1][0]["Title"] and r["Test Step"] not in ("", "1"):
                groups[-1].append(r)
            else:
                groups.append([r])
        if len(groups) != len(feat["testCases"]):
            errors.append(f"{p.name}: {len(groups)} test case(s) in CSV, {len(feat['testCases'])} in the feature file")
        for tc, g in zip(feat["testCases"], groups):
            where = f"{p.name} '{tc['title']}'"
            first = g[0]
            for r in g[1:]:
                diff = [c for c in TC_FIELDS if r[c] != first[c]]
                if diff:
                    errors.append(f"{where} step {r['Test Step']}: test case fields differ between rows: {diff}")
            expect = {"ID": tc["adoId"], "Work Item Type": "Test Case", "Title": tc["title"],
                      "Area Path": tc["areaPath"]}
            for c, v in expect.items():
                if first[c] != v:
                    errors.append(f"{where}: {c} is '{first[c]}', expected '{v}'")
            if set(t.strip() for t in first["Tags"].split(";") if t.strip()) != set(tc["tags"]):
                errors.append(f"{where}: Tags '{first['Tags']}' != expected '{'; '.join(tc['tags'])}'")
            if not first["Description"].startswith(f"Source: {tc['where']}"):
                errors.append(f"{where}: Description must start with 'Source: {tc['where']}'")
            elif first["Description"] != tc["description"]:
                warnings.append(f"{where}: Description differs from the generated one")
            if not tc["adoId"] and first["State"] != cfg["state"]:
                warnings.append(f"{where}: State '{first['State']}' != config '{cfg['state']}'")
            steps = [r["Test Step"] for r in g]
            want = [str(i) for i in range(1, len(tc["rows"]) + 1)] or [""]
            if steps != want:
                errors.append(f"{where}: Test Step numbers {steps} != {want}")
                continue
            for r, src in zip(g, tc["rows"]):
                if not r["Step Action"].strip():
                    errors.append(f"{where} step {r['Test Step']}: empty Step Action")
                if bool(r["Step Expected"].strip()) != bool(src["sourceExpected"]):
                    errors.append(f"{where} step {r['Test Step']}: Step Expected is "
                                  f"{'filled' if r['Step Expected'].strip() else 'empty'} but source "
                                  f"{'has' if src['sourceExpected'] else 'has no'} Then step")
                for col, s in (("Step Action", "" if src["verify"] else src["source"]),
                               ("Step Expected", src["sourceExpected"])):
                    if params_in(r[col]) != params_in(s):
                        errors.append(f"{where} step {r['Test Step']} {col}: @parameters "
                                      f"{sorted(params_in(r[col]))} != source {sorted(params_in(s))}")
                    if "<" in r[col] and tc["isOutline"] and PLACEHOLDER.search(r[col]) and \
                            any(ph in tc["placeholderToParam"] for ph in PLACEHOLDER.findall(r[col])):
                        errors.append(f"{where} step {r['Test Step']} {col}: <placeholder> not converted")
            if tc["isOutline"] and tc["values"]:
                vp = out / tc["valuesFile"]
                if not vp.exists():
                    errors.append(f"{where}: missing values file {tc['valuesFile']}")
                else:
                    got = list(csv.reader(io.StringIO(vp.read_text(encoding="utf-8-sig"), newline="")))
                    if got != [tc["parameters"]] + tc["values"]:
                        errors.append(f"{where}: values file {tc['valuesFile']} does not match the Examples")
    combined = sorted(out.glob("_all-test-cases*.csv"))
    if combined:
        crow = []
        for c in combined:
            h, rows = read_csv_rows(c)
            if h != COLUMNS:
                errors.append(f"{c.name}: wrong header")
            crow += [tuple(r[k] for k in COLUMNS) for r in rows]
        if not args.scope and sorted(crow) != sorted(all_rows):
            errors.append("_all-test-cases*.csv is not the exact union of the per-feature CSVs")
        big = [c.name for c in combined if c.stat().st_size > 20 * 1024 * 1024]
        if big:
            errors.append(f"Import files over 20 MB: {big}")
    elif not args.scope:
        errors.append("missing _all-test-cases.csv")
    if cfg["_split"] and not args.scope:
        split_rows = []
        for c in sorted(out.glob("_all-*-test-cases*.csv")):
            if c.name.startswith(("_all-api-", "_all-ui-", "_all-hybrid-")):
                split_rows += [tuple(r[k] for k in COLUMNS) for r in read_csv_rows(c)[1]]
        if sorted(split_rows) != sorted(all_rows):
            errors.append("_all-<type>-test-cases*.csv files are not the exact union of the per-feature CSVs")
    if cfg["_mask"]:  # safety net: nothing that looks like a secret may leave the repo
        for f in sorted(out.rglob("*.csv")):
            if "_work" in f.relative_to(out).parts or f.name == "_ado-id-map.csv":
                continue
            text = f.read_text(encoding="utf-8-sig")
            for rx, _ in SECRET_PATTERNS:
                m = rx.search(text)
                if m:
                    errors.append(f"{f.relative_to(out).as_posix()}: unmasked secret-like value "
                                  f"'{m.group(0)[:12]}...' - fix the wording or re-run build")
                    break
    ap = out / "_area-paths.txt"
    if not args.scope:
        want = sorted({f["areaPath"] for f in model.features if not f.get("parseError")}, key=str.lower)
        got = ap.read_text(encoding="utf-8").splitlines() if ap.exists() else []
        if got != want:
            errors.append(f"_area-paths.txt does not list exactly the {len(want)} expected Area Paths")
    say(f"Validated {len(feats)} feature file(s): {len(errors)} error(s), {len(warnings)} warning(s)")
    for e in errors:
        say("ERROR " + e)
    for w in warnings:
        say("WARN  " + w)
    return 1 if errors else 0


# ----------------------------------------------------------------------------------------------- compare
def cmd_compare(cfg, args):
    exp, act = Path(args.expected).resolve(), Path(args.actual).resolve()
    diffs = []
    wording_cols = {"Step Action", "Step Expected"}

    def files(base: Path):
        return {p.relative_to(base).as_posix() for p in base.rglob("*")
                if p.is_file() and "_work" not in p.relative_to(base).parts
                and p.name not in ("_conversion-report.md", "_placeholder-analysis.md", "_ado-id-map.csv")}
    ef, af = files(exp), files(act)
    diffs += [f"missing in actual: {f}" for f in sorted(ef - af)]
    diffs += [f"extra in actual: {f}" for f in sorted(af - ef)]
    for f in sorted(ef & af):
        a, b = exp / f, act / f
        if f.endswith(".csv") and "/" in f and ".parameters/" in f or f == "_area-paths.txt":
            if a.read_text(encoding="utf-8-sig").splitlines() != b.read_text(encoding="utf-8-sig").splitlines():
                diffs.append(f"{f}: content differs")
            continue
        if not f.endswith(".csv"):
            continue
        ha, ra = read_csv_rows(a)
        hb, rb = read_csv_rows(b)
        if ha != hb:
            diffs.append(f"{f}: header differs")
            continue
        if len(ra) != len(rb):
            diffs.append(f"{f}: {len(ra)} rows expected, {len(rb)} actual")
        for i, (x, y) in enumerate(zip(ra, rb), 2):
            for c in ha:
                if c in wording_cols:
                    if params_in(x[c]) != params_in(y[c]) or bool(x[c].strip()) != bool(y[c].strip()):
                        diffs.append(f"{f} line~{i} {c}: params/emptiness differ "
                                     f"({sorted(params_in(x[c]))} vs {sorted(params_in(y[c]))})")
                elif c == "Tags":
                    if {t.strip() for t in x[c].split(";")} != {t.strip() for t in y[c].split(";")}:
                        diffs.append(f"{f} line~{i} Tags differ: '{x[c]}' vs '{y[c]}'")
                elif x[c] != y[c]:
                    diffs.append(f"{f} line~{i} {c} differs: '{x[c][:60]}' vs '{y[c][:60]}'")
    say(f"Compared {len(ef)} expected file(s) with {act}: {len(diffs)} difference(s) (wording ignored)")
    for d in diffs[:100]:
        say("DIFF " + d)
    return 1 if diffs else 0


# ----------------------------------------------------------------------------------------------- ids
def cmd_sync_ids(cfg, args):
    model = Model(cfg)
    by_area_title: dict[tuple, list] = {}
    by_title: dict[str, list] = {}
    for f in model.features:
        for tc in f["testCases"]:
            by_area_title.setdefault((tc["areaPath"].lower(), tc["title"].lower()), []).append(tc)
            by_title.setdefault(tc["title"].lower(), []).append(tc)
    exported: dict[str, dict] = {}
    for e in args.export:
        _, rows = read_csv_rows(Path(e))
        last = None
        for r in rows:
            if r.get("ID", "").strip():
                last = r["ID"].strip()
                exported.setdefault(last, {"Title": r.get("Title", "").strip(),
                                           "Area Path": r.get("Area Path", "").strip(),
                                           "State": r.get("State", "").strip(),
                                           "Assigned To": r.get("Assigned To", "").strip()})
    entries = {k: dict(v) for k, v in model.id_map.items()}
    ids_in_map = {v["ADO ID"]: k for k, v in entries.items()}
    now = _dt.date.today().isoformat()
    matched, unmatched, conflicts = 0, [], []
    for ado_id, info in exported.items():
        cands = by_area_title.get((info["Area Path"].lower(), info["Title"].lower())) or []
        if not cands and len(by_title.get(info["Title"].lower(), [])) == 1:
            cands = by_title[info["Title"].lower()]
        if len(cands) != 1:
            unmatched.append(f"{ado_id} '{info['Title']}' ({info['Area Path']})" +
                             (" - ambiguous" if len(cands) > 1 else ""))
            continue
        tc = cands[0]
        cur = entries.get(tc["key"])
        if cur and cur["ADO ID"] != ado_id and not args.overwrite:
            conflicts.append(f"{tc['key']}: map has {cur['ADO ID']}, export has {ado_id} (use --overwrite)")
            continue
        if ado_id in ids_in_map and ids_in_map[ado_id] != tc["key"]:
            conflicts.append(f"ADO ID {ado_id} already mapped to '{ids_in_map[ado_id]}'")
            continue
        entries[tc["key"]] = {"Key": tc["key"], "ADO ID": ado_id, "Area Path": tc["areaPath"], "Title": tc["title"],
                              "State": info["State"], "Assigned To": info["Assigned To"], "Last Synced": now}
        matched += 1
    for f in model.features:  # @TC_<id> tags in feature files are also a source of truth
        for tc in f["testCases"]:
            if tc["existingTagId"] and tc["key"] not in entries:
                entries[tc["key"]] = {"Key": tc["key"], "ADO ID": tc["existingTagId"], "Area Path": tc["areaPath"],
                                      "Title": tc["title"], "Last Synced": now}
    all_keys = {tc["key"] for f in model.features for tc in f["testCases"]}
    orphans = sorted(k for k in entries if k not in all_keys)
    write_id_map(cfg["_map"], entries)
    say(f"ID map: {cfg['_map']}  ({len(entries)} entries, {matched} matched from {len(exported)} exported)")
    for c in conflicts:
        say("CONFLICT " + c)
    for u in unmatched:
        say("NOT MATCHED (in ADO, not found in feature files) " + u)
    for o in orphans:
        say(f"ORPHAN (mapped, scenario no longer exists - renamed or deleted?) {o} -> {entries[o]['ADO ID']}")
    missing = sorted(k for k in all_keys if k not in entries)
    say(f"Test cases still without an ADO ID: {len(missing)}")
    say("Next: re-run `build` so the CSVs carry the IDs (import will then update, not duplicate).")
    return 1 if conflicts else 0


def cmd_tag_features(cfg, args):
    model = Model(cfg)
    prefix = cfg["tcTagPrefix"]
    changes: dict[Path, list] = {}
    for f in model.in_scope(args.scope):
        for tc in f["testCases"]:
            if tc["adoId"] and not tc["existingTagId"]:
                changes.setdefault(cfg["_src_root"] / f["source"], []).append((tc["line"], tc["adoId"], tc["title"]))
    total = 0
    for path, items in sorted(changes.items()):
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        nl = "\r\n" if "\r\n" in text else "\n"
        lines = text.split(nl)
        for line_no, ado_id, title in sorted(items, reverse=True):
            idx = line_no - 1
            indent = re.match(r"\s*", lines[idx]).group(0)
            tag = f"@{prefix}{ado_id}"
            if idx > 0 and lines[idx - 1].strip().startswith("@"):
                lines[idx - 1] = lines[idx - 1].rstrip() + " " + tag
                action = f"append {tag} to line {line_no - 1}"
            else:
                lines.insert(idx, indent + tag)
                action = f"insert {tag} above line {line_no}"
            say(f"{'WRITE' if args.write else 'DRY-RUN'} {path.relative_to(cfg['_src_root']).as_posix()}: {action} "
                f"('{title}')")
            total += 1
        if args.write:
            path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + nl.join(lines).encode("utf-8"))
    say(f"{total} tag(s) {'written' if args.write else 'would be written - re-run with --write to apply'}")
    return 0


# ----------------------------------------------------------------------------------------------- push (REST API)
class AdoError(Exception):
    pass


def load_ado_properties(path: str) -> dict:
    import os
    p = (ROOT / path) if not Path(path).is_absolute() else Path(path)
    if not p.exists():
        sys.exit(f"ADO properties file not found: {p} (copy ado.properties.example and fill it in)")
    props = {}
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")) or not re.search(r"[=:]", line):
            continue
        k, v = re.split(r"\s*[=:]\s*", line, maxsplit=1)
        props[k.strip()] = v.strip()
    if os.environ.get("ADO_PAT"):
        props["ado.pat"] = os.environ["ADO_PAT"]
    missing = [k for k in ("ado.org.url", "ado.project", "ado.pat", "ado.api.version", "ado.testplan.id")
               if not props.get(k)]
    if missing:
        sys.exit(f"Missing in {p.name}: {', '.join(missing)}")
    if not props["ado.testplan.id"].isdigit():
        sys.exit(f"ado.testplan.id must be a number, got '{props['ado.testplan.id']}'")
    gi = ROOT / ".gitignore"
    if p.parent == ROOT and (not gi.exists() or p.name not in gi.read_text(encoding="utf-8").split()):
        say(f"WARN  {p.name} holds a PAT and is not in .gitignore - add it before committing")
    return props


class AdoClient:
    """Minimal Azure DevOps REST client (urllib, no extra dependencies). Never logs the PAT."""

    def __init__(self, props: dict):
        import base64
        from urllib.parse import quote
        self.q = quote
        self.org = props["ado.org.url"].rstrip("/")
        self.project = props["ado.project"]
        self.v = props["ado.api.version"]
        self.base = f"{self.org}/{quote(self.project)}/_apis"
        self.auth = "Basic " + base64.b64encode(f":{props['ado.pat']}".encode()).decode()
        self.calls = 0

    def req(self, method: str, url: str, body=None, content_type="application/json", allow_404=False):
        import time
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(5):
            r = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": self.auth, "Accept": "application/json", "Content-Type": content_type})
            try:
                self.calls += 1
                with urllib.request.urlopen(r, timeout=60) as resp:
                    raw = resp.read().decode("utf-8", "replace") or "null"
                    try:
                        return json.loads(raw), dict(resp.headers)
                    except ValueError:
                        raise AdoError(f"{method} {url.split('?')[0]} -> HTTP {resp.status} returned a non-JSON page "
                                       f"(usually an invalid/expired PAT or a wrong ado.org.url)") from None
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and allow_404:
                    return None, {}
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(float(exc.headers.get("Retry-After") or 2 ** attempt))
                    continue
                detail = exc.read().decode("utf-8", "replace")
                try:
                    detail = json.loads(detail).get("message", detail)
                except ValueError:
                    pass
                hint = {401: " (PAT invalid or expired)", 403: " (PAT lacks a scope: needs Work Items Read & Write "
                        "and Test Management Read & Write)", 203: " (authentication failed)"}.get(exc.code, "")
                raise AdoError(f"{method} {url.split('?')[0]} -> HTTP {exc.code}{hint}: {str(detail)[:300]}") from None
            except urllib.error.URLError as exc:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise AdoError(f"{method} {url.split('?')[0]} -> {exc.reason}") from None
        raise AdoError(f"{method} {url} failed after retries")

    def _paged(self, url: str) -> list:
        items, token = [], None
        while True:
            u = url + (f"&continuationToken={self.q(token)}" if token else "")
            data, headers = self.req("GET", u)
            items += (data or {}).get("value", [])
            token = {k.lower(): v for k, v in headers.items()}.get("x-ms-continuationtoken")
            if not token:
                return items

    # test plans / suites
    def plan(self, plan_id):
        data, _ = self.req("GET", f"{self.base}/testplan/plans/{plan_id}?api-version={self.v}", allow_404=True)
        if not data:
            raise AdoError(f"Test plan {plan_id} not found in project '{self.project}'")
        return data

    def suites(self, plan_id):
        return self._paged(f"{self.base}/testplan/Plans/{plan_id}/suites?api-version={self.v}")

    def create_suite(self, plan_id, parent_id, name):
        data, _ = self.req("POST", f"{self.base}/testplan/Plans/{plan_id}/suites?api-version={self.v}",
                           {"suiteType": "staticTestSuite", "name": name, "parentSuite": {"id": parent_id}})
        return data

    def suite_case_ids(self, plan_id, suite_id) -> set:
        items = self._paged(f"{self.base}/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase?api-version={self.v}")
        return {int(i["workItem"]["id"]) for i in items if i.get("workItem")}

    def add_to_suite(self, plan_id, suite_id, ids):
        for i in range(0, len(ids), 100):
            self.req("POST", f"{self.base}/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase?api-version={self.v}",
                     [{"workItem": {"id": x}} for x in ids[i:i + 100]])

    # work items
    def work_item(self, wid):
        data, _ = self.req("GET", f"{self.base}/wit/workitems/{wid}?fields=System.WorkItemType,System.State,"
                                  f"System.AssignedTo&api-version={self.v}", allow_404=True)
        return data

    def create_test_case(self, ops):
        data, _ = self.req("POST", f"{self.base}/wit/workitems/$Test%20Case?api-version={self.v}", ops,
                           "application/json-patch+json")
        return data

    def update_work_item(self, wid, ops):
        data, _ = self.req("PATCH", f"{self.base}/wit/workitems/{wid}?api-version={self.v}", ops,
                           "application/json-patch+json")
        return data

    # area paths
    def area_exists(self, area: str) -> bool:
        parts = area.split("\\")[1:]
        if not parts:
            return True
        path = "/".join(self.q(p) for p in parts)
        data, _ = self.req("GET", f"{self.base}/wit/classificationnodes/Areas/{path}?api-version={self.v}",
                           allow_404=True)
        return bool(data)

    def create_area(self, area: str):
        parts = area.split("\\")[1:]
        for i in range(1, len(parts) + 1):
            sub = "\\".join([self.project] + parts[:i])
            if not self.area_exists(sub):
                parent = "/".join(self.q(p) for p in parts[:i - 1])
                url = f"{self.base}/wit/classificationnodes/Areas{('/' + parent) if parent else ''}?api-version={self.v}"
                self.req("POST", url, {"name": parts[i - 1]})


def _html(text: str) -> str:
    import html
    return "<br/>".join(html.escape(line) for line in text.split("\n"))


def steps_xml(rows: list[dict]) -> str:
    """Microsoft.VSTS.TCM.Steps: step text is HTML inside XML (ElementTree escapes it once more)."""
    import xml.etree.ElementTree as ET
    root = ET.Element("steps", id="0", last=str(len(rows) + 1))
    for i, r in enumerate(rows, 2):
        exp = r["expected"].strip()
        st = ET.SubElement(root, "step", id=str(i), type="ValidateStep" if exp else "ActionStep")
        for text in (r["action"].strip(), exp):
            ps = ET.SubElement(st, "parameterizedString", isformatted="true")
            ps.text = f"<DIV><P>{_html(text)}</P></DIV>" if text else ""
        ET.SubElement(st, "description")
    return ET.tostring(root, encoding="unicode")


def parameters_xml(params: list[str]) -> str:
    return "<parameters>" + "".join(f'<param name="{p}" bind="default"/>' for p in params) + "</parameters>"


def local_data_source_xml(params: list[str], values: list[list[str]]) -> str:
    from xml.sax.saxutils import escape
    cols = "".join(f"<xs:element name='{p}' type='xs:string' minOccurs='0' />" for p in params)
    schema = ("<xs:schema id='NewDataSet' xmlns:xs='http://www.w3.org/2001/XMLSchema' "
              "xmlns:msdata='urn:schemas-microsoft-com:xml-msdata'><xs:element name='NewDataSet' "
              "msdata:IsDataSet='true' msdata:Locale=''><xs:complexType><xs:choice minOccurs='0' "
              "maxOccurs='unbounded'><xs:element name='Table1'><xs:complexType><xs:sequence>"
              f"{cols}</xs:sequence></xs:complexType></xs:element></xs:choice></xs:complexType></xs:element>"
              "</xs:schema>")
    rows = "".join("<Table1>" + "".join(f"<{p}>{escape(v)}</{p}>" for p, v in zip(params, row)) + "</Table1>"
                   for row in values)
    return f"<NewDataSet>{schema}{rows}</NewDataSet>"


def work_item_ops(cfg, tc, wording, creating: bool) -> list[dict]:
    fields = {
        "System.Title": tc["title"],
        "System.AreaPath": tc["areaPath"],
        "System.Tags": "; ".join(tc["tags"]),
        "System.Description": f"<div>{_html(tc['description'])}</div>",
        "Microsoft.VSTS.TCM.Steps": steps_xml(wording["rows"]),
        "Microsoft.VSTS.TCM.Parameters": parameters_xml(tc["parameters"]) if tc["isOutline"] else "",
        "Microsoft.VSTS.TCM.LocalDataSource":
            local_data_source_xml(tc["parameters"], tc["values"]) if tc["isOutline"] else "",
    }
    if creating:
        fields["System.State"] = cfg["state"]
        if cfg["assignedTo"]:
            fields["System.AssignedTo"] = cfg["assignedTo"]
    return [{"op": "add", "path": f"/fields/{k}", "value": v} for k, v in fields.items()
            if v != "" or not creating]


def suite_path_for(cfg, feat) -> list[str]:
    root = [sanitize_node(n) for n in re.split(r"[\\/]", cfg.get("suiteRoot", "")) if n.strip()]
    return root + [sanitize_node(p) for p in feat["relNoExt"].split("/")]


def cmd_push(cfg, args):
    model = Model(cfg)
    feats = [f for f in model.in_scope(args.scope) if not f.get("parseError")]
    props = load_ado_properties(args.ado_properties)
    apply = args.apply
    mode = "APPLY" if apply else "DRY-RUN"
    # 1. everything must be convertible first (same checks as build)
    ready, problems = collect_ready(cfg, model, feats)
    if problems:
        say("Fix these first (run plan/build until build has no errors):")
        for p in problems[:30]:
            say("ERROR " + p)
        return 1
    # 2. human review gate: only Approved + Reviewer + unchanged content is pushed
    approvals: dict[str, dict] = {}
    if cfg["_review"]:
        sheet = read_sheet(review_dir(cfg) / "review-sheet.csv")
        if not sheet:
            say("ERROR no review sheet - run `review`, have a person approve the test cases, then push.")
            return 1
        kept, skipped = [], {"Pending": 0, "Rejected": 0, "changed after approval": 0, "no reviewer name": 0,
                             "never reviewed": 0}
        for item in ready:
            _, tc, wording = item
            row = sheet.get(tc["key"])
            h = content_hash(tc, wording)
            if not row:
                reason = "never reviewed"
            elif norm_decision(row.get("Decision", "")) != "Approved":
                reason = norm_decision(row.get("Decision", ""))
            elif row.get("Content Hash") != h:
                reason = "changed after approval"
            elif not row.get("Reviewer", "").strip():
                reason = "no reviewer name"
            else:
                kept.append(item)
                approvals[tc["key"]] = dict(row, _hash=h)
                continue
            skipped[reason] += 1
            if args.verbose:
                say(f"SKIP    ({reason}) {tc['title']}")
        say(f"Review gate: {len(kept)} approved test case(s) will be pushed; skipped: " +
            ", ".join(f"{n} {k}" for k, n in skipped.items() if n) if any(skipped.values()) else
            f"Review gate: all {len(kept)} test case(s) approved")
        ready = kept
        if not ready:
            say("Nothing approved to push. Run `review`, and ask a reviewer to approve test cases in "
                "review-sheet.csv.")
            return 1
    if args.limit:
        ready = ready[: args.limit]
    if props["ado.project"].lower() != cfg["adoProject"].lower():
        say(f"ERROR config adoProject '{cfg['adoProject']}' != ado.properties project '{props['ado.project']}'. "
            f"Area Paths must start with the ADO project name - fix one of them (or pass --ado-project).")
        return 1
    client = AdoClient(props)
    plan_id = int(props["ado.testplan.id"])
    try:
        plan = client.plan(plan_id)
        root_id = plan["rootSuite"]["id"]
        say(f"{mode}: plan {plan_id} '{plan.get('name')}' in {client.org}/{client.project}, root suite {root_id}")
        # 2. area paths
        areas = sorted({tc["areaPath"] for _, tc, _ in ready}, key=str.lower)
        missing_areas = [a for a in areas if not client.area_exists(a)]
        for a in missing_areas:
            if args.create_areas:
                say(f"{mode} create Area Path {a}")
                if apply:
                    client.create_area(a)
            else:
                say(f"MISSING Area Path {a}")
        if missing_areas and not args.create_areas:
            say("Create the missing Area Paths in ADO (Project Settings > Boards > Areas), or re-run with "
                "--create-areas (needs permission to edit areas).")
            if apply:
                return 1
        # 3. suites: <plan root>/[suiteRoot]/<folders>/<feature file>
        all_suites = client.suites(plan_id)
        children: dict[tuple, dict] = {}
        for s in all_suites:
            parent = (s.get("parentSuite") or {}).get("id")
            children[(parent, s["name"].lower())] = s
        leaf_ids: dict[str, int | str] = {}
        suite_label: dict = {}
        created_suites = 0
        for feat in sorted({f["relNoExt"]: f for f, _, _ in ready}.values(), key=lambda f: f["relNoExt"]):
            parent = root_id
            walked: list[str] = []
            for name in suite_path_for(cfg, feat):
                walked.append(name)
                s = children.get((parent, name.lower()))
                if s and s.get("suiteType") not in (None, "staticTestSuite"):
                    raise AdoError(f"Suite '{name}' exists but is a {s['suiteType']} suite - rename it or change "
                                   f"suiteRoot")
                if not s:
                    created_suites += 1
                    say(f"{mode} create suite {plan.get('name')}/{'/'.join(walked)}")
                    s = client.create_suite(plan_id, parent, name) if apply else \
                        {"id": f"new:{parent}/{name}", "name": name, "suiteType": "staticTestSuite"}
                    children[(parent, name.lower())] = s
                parent = s["id"]
            leaf_ids[feat["relNoExt"]] = parent
            suite_label[parent] = f"{plan.get('name')}/{'/'.join(walked)}"
        # 4. check every mapped ID before anything is written
        bad_ids = []
        for _, tc, _ in ready:
            if tc["adoId"]:
                existing = client.work_item(tc["adoId"])
                wit = (existing or {}).get("fields", {}).get("System.WorkItemType")
                if not existing:
                    bad_ids.append(f"{tc['where']}: mapped ADO ID {tc['adoId']} does not exist in "
                                   f"'{client.project}' - fix the ID map or the @{cfg['tcTagPrefix']} tag")
                elif wit != "Test Case":
                    bad_ids.append(f"{tc['where']}: ADO ID {tc['adoId']} is a {wit}, not a Test Case")
        for b in bad_ids:
            say("ERROR " + b)
        if bad_ids and apply:
            raise AdoError(f"{len(bad_ids)} mapped ID problem(s) - nothing was changed")
        # 5. test cases
        entries = {k: dict(v) for k, v in model.id_map.items()}
        created = updated = added = 0
        per_suite: dict = {}
        import getpass

        def log(action, wid, tc):
            a = approvals.get(tc["key"], {})
            append_log(cfg, [{"Timestamp": _dt.datetime.now().isoformat(timespec="seconds"), "Action": action,
                              "ADO ID": wid, "Key": tc["key"], "Title": tc["title"],
                              "Content Hash": a.get("_hash", "(review not required)"),
                              "Reviewer": a.get("Reviewer", ""), "Review Date": a.get("Review Date", ""),
                              "Pushed By": getpass.getuser()}])
        for feat, tc, wording in ready:
            wid = tc["adoId"]
            if wid:
                say(f"{mode} update  #{wid} {tc['title']}")
                if apply:
                    client.update_work_item(wid, work_item_ops(cfg, tc, wording, creating=False))
                    log("update", wid, tc)
                updated += 1
            else:
                say(f"{mode} create  {tc['title']}  ({tc['areaPath']})")
                if apply:
                    data = client.create_test_case(work_item_ops(cfg, tc, wording, creating=True))
                    wid = str(data["id"])
                    f = data.get("fields", {})
                    who = f.get("System.AssignedTo")
                    entries[tc["key"]] = {"Key": tc["key"], "ADO ID": wid, "Area Path": tc["areaPath"],
                                          "Title": tc["title"], "State": f.get("System.State", cfg["state"]),
                                          "Assigned To": who.get("uniqueName", "") if isinstance(who, dict) else "",
                                          "Last Synced": _dt.date.today().isoformat()}
                    write_id_map(cfg["_map"], entries)  # save after every create: a re-run never duplicates
                    log("create", wid, tc)
                created += 1
            per_suite.setdefault(leaf_ids[feat["relNoExt"]], []).append((wid, tc["title"]))
        # 6. suite membership (only add; never removes anything)
        for suite_id, items in per_suite.items():
            have = client.suite_case_ids(plan_id, suite_id) if isinstance(suite_id, int) else set()
            todo = [int(w) for w, _ in items if w and int(w) not in have]
            new_in_suite = [t for w, t in items if not w]
            for w, t in items:
                if (w and int(w) not in have) or not w:
                    say(f"{mode} add to suite {suite_label[suite_id]}: {('#' + str(w)) if w else '(new)'} {t}")
            added += len(todo) + len(new_in_suite)
            if apply and todo:
                client.add_to_suite(plan_id, suite_id, todo)
            ours = {int(w) for w, _ in items if w}
            for extra in sorted(have - ours):
                say(f"NOTE    suite {suite_label[suite_id]} also holds #{extra}, which no scenario maps to - left as it is")
    except AdoError as exc:
        say(f"ERROR {exc}")
        say("Nothing after this point was changed." if apply else "Dry run stopped.")
        return 1
    say("")
    say(f"{mode} summary: {created} test case(s) {'created' if apply else 'to create'}, {updated} "
        f"{'updated' if apply else 'to update'}, {created_suites} suite(s) {'created' if apply else 'to create'}, "
        f"{added} suite membership(s) {'added' if apply else 'to add'}, {client.calls} API call(s).")
    if apply:
        say(f"ID map updated: {cfg['_map']}. Run `build` so the CSV exports carry the IDs.")
    else:
        say("Nothing was changed in ADO. Re-run with --apply to do it.")
    return 0


# ----------------------------------------------------------------------------------------------- human review
SHEET_COLUMNS = ["Key", "Feature", "Area Path", "Title", "Test Type", "Steps", "Iterations", "Review Page",
                 "Content Hash", "Decision", "Reviewer", "Review Date", "Comment", "Note"]
LOG_COLUMNS = ["Timestamp", "Action", "ADO ID", "Key", "Title", "Content Hash", "Reviewer", "Review Date",
               "Pushed By"]
DECISIONS = ("Approved", "Rejected", "Pending")


def collect_ready(cfg, model, feats) -> tuple[list, list]:
    """(ready, problems): test cases whose wording passes the build checks, with their wording."""
    ready, problems = [], list(model.errors)
    for feat in feats:
        wpath = work_path(cfg, feat["relNoExt"], ".wording.json")
        wording = {t["key"]: t for t in json.loads(wpath.read_text(encoding="utf-8"))["testCases"]} \
            if wpath.exists() else {}
        for tc in feat["testCases"]:
            errs, _ = check_wording(tc, wording.get(tc["key"]))
            errs += [f"{tc['where']}: {fl}" for fl in tc["flags"] if fl.startswith("ERROR")]
            if errs:
                problems += errs
            else:
                ready.append((feat, tc, wording[tc["key"]]))
    return ready, problems


def content_hash(tc, wording) -> str:
    """Fingerprint of everything a reviewer approves (and push sends). Line numbers are left out on purpose."""
    desc = "\n".join(l for l in tc["description"].split("\n") if not l.startswith("Source: "))
    payload = {"title": tc["title"], "area": tc["areaPath"], "tags": tc["tags"], "description": desc,
               "steps": [[r["action"].strip(), r["expected"].strip()] for r in wording["rows"]],
               "parameters": tc.get("parameters", []), "values": tc.get("values", [])}
    return "h-" + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def review_dir(cfg) -> Path:
    return cfg["_out"] / "_review"


def norm_decision(value: str) -> str:
    v = (value or "").strip().lower()
    return "Approved" if v.startswith("approv") else "Rejected" if v.startswith("reject") else "Pending"


def read_sheet(path: Path) -> dict[str, dict]:
    """Reads the review sheet as saved by Excel (UTF-8 or ANSI, ',' or ';')."""
    if not path.exists():
        return {}
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = csv.DictReader(io.StringIO(text, newline=""), delimiter=delim)
    return {r["Key"]: r for r in rows if r.get("Key")}


def write_sheet(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:   # BOM: Excel opens UTF-8 correctly
        w = csv.DictWriter(fh, SHEET_COLUMNS, lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)


def _md(text: str) -> str:
    import html
    return html.escape(text or "", quote=False).replace("|", "\\|").replace("\n", "<br>") or " "


def write_review_page(cfg, feat, items, sheet_rows, plan_name="<test plan>"):
    """One readable Markdown page per feature file: exactly what would be sent to ADO."""
    import os
    page = review_dir(cfg) / f"{feat['relNoExt']}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    abs_src = cfg["_src_root"] / feat["source"]
    try:
        src = os.path.relpath(abs_src, page.parent).replace("\\", "/")
    except ValueError:  # different drive (e.g. repo on C:, workspace on D:) - link to the absolute file instead
        src = abs_src.resolve().as_uri()
    L = [f"# Review: `{feat['rel']}`", "",
         f"- Source: [{feat['source']}]({src.replace(' ', '%20').replace('#', '%23')})",
         f"- Area Path: `{feat['areaPath']}`",
         f"- ADO suite: `{plan_name}/{'/'.join(suite_path_for(cfg, feat))}`",
         f"- ADO tags: `{'; '.join(items[0][0]['tags']) if items else ''}`", "",
         "Record decisions in `review-sheet.csv` (Decision / Reviewer / Comment). This page is regenerated by "
         "`review` - edits here are lost.", ""]
    for n, (tc, wording) in enumerate(items, 1):
        row = sheet_rows[tc["key"]]
        badge = {"Approved": "APPROVED", "Rejected": "REJECTED", "Pending": "PENDING"}[row["Decision"]]
        L += [f"## {n}. {tc['title']}", "",
              f"**{badge}**" + (f" by {row['Reviewer']} ({row['Review Date']})" if row["Reviewer"] else "") +
              (f" - _{row['Comment']}_" if row["Comment"] else "") +
              (f" - {row['Note']}" if row["Note"] else ""), "",
              f"- Type: {tc['testType'].upper() if tc['testType'] != 'hybrid' else 'Hybrid'} - "
              f"{'Scenario Outline (parameterized)' if tc['isOutline'] else 'Scenario'} - "
              f"[line {tc['line']}]({src.replace(' ', '%20').replace('#', '%23')}#L{tc['line']})"
              + (f" - ADO #{tc['adoId']} (will be updated)" if tc["adoId"] else " - new in ADO"),
              f"- Scenario: _{_md(tc['originalName'].strip())}_",
              f"- Gherkin tags: {' '.join(tc['gherkinTags']) or '-'}",
              f"- Content hash: `{row['Content Hash']}`", "",
              "| # | Action | Expected result |", "|---|---|---|"]
        L += [f"| {i} | {_md(r['action'])} | {_md(r['expected'])} |" for i, r in enumerate(wording["rows"], 1)]
        if tc["isOutline"]:
            L += ["", f"**Parameter values ({len(tc['values'])} iteration(s))**", "",
                  "| " + " | ".join("@" + p for p in tc["parameters"]) + " |",
                  "|" + "---|" * len(tc["parameters"])]
            L += ["| " + " | ".join(_md(v) for v in row_v) + " |" for row_v in tc["values"]]
        flags = [f for f in tc["flags"] if not f.startswith("ERROR")]
        if flags:
            L += ["", "**Check:**", ""] + [f"- {_md(f)}" for f in flags]
        L += ["", "<details><summary>Description sent to ADO</summary>", "", "```text", tc["description"], "```", "",
              "</details>", ""]
    page.write_text("\n".join(L), encoding="utf-8")
    return page


def cmd_review(cfg, args):
    model = Model(cfg)
    feats = [f for f in model.in_scope(args.scope) if not f.get("parseError")]
    ready, problems = collect_ready(cfg, model, feats)
    rdir = review_dir(cfg)
    sheet_path = rdir / "review-sheet.csv"
    old = read_sheet(sheet_path)
    in_scope_keys = {tc["key"] for f in feats for tc in f["testCases"]}
    rows: dict[str, dict] = {k: v for k, v in old.items() if k not in in_scope_keys}  # keep out-of-scope rows
    counts = {d: 0 for d in DECISIONS}
    reset = []
    by_feat: dict[str, list] = {}
    for feat, tc, wording in ready:
        h = content_hash(tc, wording)
        prev = old.get(tc["key"])
        row = {"Key": tc["key"], "Feature": feat["source"], "Area Path": tc["areaPath"], "Title": tc["title"],
               "Test Type": tc["testType"], "Steps": str(len(wording["rows"])),
               "Iterations": str(len(tc.get("values", []))) if tc["isOutline"] else "",
               "Review Page": f"{feat['relNoExt']}.md", "Content Hash": h,
               "Decision": "Pending", "Reviewer": "", "Review Date": "", "Comment": "", "Note": ""}
        if prev:
            decision = norm_decision(prev.get("Decision", ""))
            if prev.get("Content Hash") == h:
                row.update({"Decision": decision, "Reviewer": prev.get("Reviewer", "").strip(),
                            "Review Date": prev.get("Review Date", "").strip(),
                            "Comment": prev.get("Comment", "").strip()})
                if decision != "Pending" and not row["Review Date"]:
                    row["Review Date"] = _dt.date.today().isoformat()
                if decision == "Approved" and not row["Reviewer"]:
                    row["Note"] = "Approved without a Reviewer name - not accepted by push"
            elif decision != "Pending":
                row["Note"] = (f"Changed since {decision.lower()} by {prev.get('Reviewer') or '?'} "
                               f"on {prev.get('Review Date') or '?'} - review again")
                row["Comment"] = f"[previous: {prev.get('Comment', '').strip()}]" if prev.get("Comment") else ""
                reset.append(f"{tc['title']} ({decision} -> Pending)")
        rows[tc["key"]] = row
        counts[row["Decision"]] += 1
        by_feat.setdefault(feat["relNoExt"], []).append((feat, tc, wording))
    ordered = sorted(rows.values(), key=lambda r: (r["Feature"], r["Key"]))
    write_sheet(sheet_path, ordered)
    plan_name = args.plan_name
    if not plan_name:
        props_file = ROOT / "ado.properties"
        m = re.search(r"^ado\.testplan\.id\s*[=:]\s*(\d+)", props_file.read_text(encoding="utf-8-sig"), re.M) \
            if props_file.exists() else None
        plan_name = f"Test plan {m.group(1)}" if m else "<test plan>"
    for rel, items in by_feat.items():
        write_review_page(cfg, items[0][0], [(tc, w) for _, tc, w in items], rows, plan_name)
    # index page
    L = ["# Test case review", "",
         f"Generated {_dt.datetime.now().isoformat(timespec='seconds')}. "
         f"**{counts['Approved']} approved, {counts['Rejected']} rejected, {counts['Pending']} pending.**", "",
         "## How to review", "",
         "1. Open each feature page below and read the test cases exactly as they will appear in ADO.",
         "2. In `review-sheet.csv` (opens in Excel) set **Decision** to `Approved` or `Rejected`, fill **Reviewer** "
         "(your name) and, for rejections, a **Comment** saying what is wrong. Save as CSV.",
         "3. Run `python .github/scripts/ado_testcases.py review` again to refresh these pages.",
         "4. Only **Approved** test cases with a Reviewer and unchanged content are uploaded by `push`. "
         "Any later change to a test case sets it back to Pending.", "",
         "| Feature file | Approved | Rejected | Pending | Page |", "|---|---|---|---|---|"]
    for rel, items in sorted(by_feat.items()):
        ds = [rows[tc["key"]]["Decision"] for _, tc, _ in items]
        L.append(f"| `{items[0][0]['rel']}` | {ds.count('Approved')} | {ds.count('Rejected')} | "
                 f"{ds.count('Pending')} | [{rel}.md]({rel.replace(' ', '%20').replace('#', '%23')}.md) |")
    if problems:
        L += ["", "## Not ready for review (fix and run build first)", ""] + [f"- {_md(p)}" for p in problems]
    (rdir / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    say(f"Review pack: {rdir}  (sheet: {sheet_path.name}, index: README.md)")
    say(f"{counts['Approved']} approved, {counts['Rejected']} rejected, {counts['Pending']} pending "
        f"({len(ready)} test case(s) ready for review)")
    for r in reset:
        say("RESET   " + r)
    rejected = [r for r in ordered if r["Decision"] == "Rejected" and r["Key"] in in_scope_keys]
    for r in rejected:
        say(f"REJECTED {r['Title']} - {r['Comment'] or '(no comment)'}")
    for p in problems[:20]:
        say("NOT READY " + p)
    return 0


def cmd_approve(cfg, args):
    """For humans: set Decision/Reviewer/Comment in the review sheet from the command line."""
    sheet_path = review_dir(cfg) / "review-sheet.csv"
    rows = read_sheet(sheet_path)
    if not rows:
        sys.exit("No review sheet yet - run `review` first.")
    if not args.reviewer.strip():
        sys.exit("--reviewer is required")
    decision = "Rejected" if args.reject else "Approved"
    if decision == "Rejected" and not args.comment:
        sys.exit("--comment is required when rejecting (say what is wrong)")
    scope = (args.scope or "").replace("\\", "/").strip("/")
    changed = 0
    for r in rows.values():
        rel = r["Key"].split("::", 1)[0]
        if scope and not (rel == scope or rel.startswith(scope + "/") or rel[:-len(".feature")] == scope):
            continue
        if args.title and args.title.lower() not in r["Title"].lower():
            continue
        r.update({"Decision": decision, "Reviewer": args.reviewer.strip(),
                  "Review Date": _dt.date.today().isoformat(), "Comment": args.comment or r.get("Comment", ""),
                  "Note": ""})
        changed += 1
        say(f"{decision.upper()} {r['Title']}  ({rel})")
    for r in rows.values():
        for c in SHEET_COLUMNS:
            r.setdefault(c, "")
    write_sheet(sheet_path, sorted(rows.values(), key=lambda r: (r["Feature"], r["Key"])))
    say(f"{changed} test case(s) marked {decision}. Run `review` to refresh the pages.")
    return 0


def append_log(cfg, entries: list[dict]):
    if not entries:
        return
    path = review_dir(cfg) / "approval-log.csv"
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, LOG_COLUMNS, lineterminator="\r\n")
        if new:
            w.writeheader()
        w.writerows(entries)


# ----------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="config file (default: %(default)s)")
    ap.add_argument("--features-root", dest="featuresRoot")
    ap.add_argument("--output-dir", dest="outputDir")
    ap.add_argument("--ado-project", dest="adoProject")
    ap.add_argument("--id-map", dest="idMapFile")
    ap.add_argument("--assigned-to", dest="assignedTo", help="override assignedTo ('' = leave unassigned)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "build", "validate", "tag-features"):
        sp = sub.add_parser(name)
        sp.add_argument("--scope", help="folder or file (relative to featuresRoot or workspace) to limit the run")
        if name == "build":
            sp.add_argument("--use-source-text", action="store_true",
                            help="fill missing wording with the Gherkin text (draft output)")
        if name == "tag-features":
            sp.add_argument("--write", action="store_true", help="actually edit the .feature files")
    sp = sub.add_parser("compare")
    sp.add_argument("--expected", required=True)
    sp.add_argument("--actual", required=True)
    sp = sub.add_parser("push", help="create/update test cases and the suite hierarchy in ADO via REST (dry run "
                                     "unless --apply)")
    sp.add_argument("--scope", help="folder or file (relative to featuresRoot or workspace) to limit the run")
    sp.add_argument("--apply", action="store_true", help="actually change ADO (default: dry run, read-only)")
    sp.add_argument("--limit", type=int, help="only the first N test cases (try it on a few first)")
    sp.add_argument("--create-areas", action="store_true", help="create missing Area Paths (needs area permissions)")
    sp.add_argument("--ado-properties", default="ado.properties", help="org/project/PAT/plan file "
                                                                          "(default: %(default)s; env ADO_PAT overrides "
                                                                          "the PAT)")
    sp.add_argument("--verbose", action="store_true", help="list every test case skipped by the review gate")
    sp = sub.add_parser("review", help="build the human review pack (pages + review-sheet.csv)")
    sp.add_argument("--scope", help="folder or file to limit the run")
    sp.add_argument("--plan-name", help="test plan name shown on the review pages (default: from ado.properties)")
    sp = sub.add_parser("approve", help="(for humans) approve or reject test cases in the review sheet")
    sp.add_argument("--reviewer", required=True, help="your name")
    sp.add_argument("--scope", help="feature file or folder, e.g. auth/login.feature (default: all)")
    sp.add_argument("--title", help="only test cases whose title contains this text")
    sp.add_argument("--reject", action="store_true", help="reject instead of approve (needs --comment)")
    sp.add_argument("--comment", help="review comment")
    sp = sub.add_parser("sync-ids")
    sp.add_argument("--export", nargs="+", required=True, help="CSV file(s) from ADO 'Export test cases to CSV'")
    sp.add_argument("--overwrite", action="store_true", help="replace IDs that are already mapped")
    args = ap.parse_args(argv)
    if args.cmd == "compare":
        return cmd_compare(None, args)
    overrides = {k: getattr(args, k) for k in ("featuresRoot", "outputDir", "adoProject", "idMapFile", "assignedTo")}
    cfg = load_config(args.config, overrides)
    return {"plan": cmd_plan, "build": cmd_build, "validate": cmd_validate, "sync-ids": cmd_sync_ids,
            "tag-features": cmd_tag_features, "push": cmd_push, "review": cmd_review,
            "approve": cmd_approve}[args.cmd](cfg, args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
