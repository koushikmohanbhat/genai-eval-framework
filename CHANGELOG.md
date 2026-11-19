# Changelog

## 0.7.4 (2026-11-20)

### Golden path to a live endpoint
- **`genai-evals quickstart --url <endpoint>`**: one probe request, a printed reading of the response (answer,
  tool calls, state, sources; contract v1 detected), a small project (URL in `.env`, header values never written),
  and a run with warn-only, directional gates. With `--cases` (CSV / xlsx / JSONL: input, expected, must_contain,
  tags, critical) it measures the client's questions; without, only domain-agnostic probes (answers, planted
  instructions, identifiers in answers, latency), and it says so. Clear errors for 401 / 404 / 422 and unreachable
  endpoints. [docs/quickstart.md](docs/quickstart.md).

### Telemetry in messy environments
- **`genai-evals sources preflight sources.yaml`** and **`doctor --preflight`**: read-only checks that sort each
  source and join into ok / missing (logging off, wrong log group or table) / off (spans without content) / denied
  (the permissions needed) / unknown, with the fix and the fallbacks when payload logging can't be turned on (OTLP
  with content capture, evaluating the endpoint with `quickstart` / `@agent`, the Agents SDK processor). Bedrock
  invocation-logging configuration, Vertex request-response tables, AgentCore / Cloud Trace span content.
- The "no content" hint in `sources check` and online results names the same fallbacks.

### Partner contributions
- **`genai-evals pack check <overlay>`**: the contribution bar (metadata, a shared slice tag, critical and security
  cases, no identifiers in the cases, a slice gate, green on the guarded offline system, red on the vulnerable one).
  `pack new`'s workflow runs it.
- **Overlay index** (`packs/_common/overlay-index.yaml`, `pack list --available`) with status and access, checked by a
  test; [docs/contributing-packs.md](docs/contributing-packs.md).

### Live walkthrough
- `examples/live-walkthrough/walkthrough.sh` and [docs/live-walkthrough.md](docs/live-walkthrough.md): a real model
  on Bedrock or Vertex, a prompt that drops the refund limit, the gate that blocks it, and a live-tested adapter
  record; a shot list for a 5-minute recording. Not yet run against a live service.

## 0.7.3 (2026-11-19)

Packs: each marks a `start_here` grader tier (metadata only; pack versions unchanged).

### Cost before live runs
- **`run --dry-run`** prints the plan: calls, estimated input / output tokens and cost per target, judge and
  simulated user, from the providers' prices. An upper bound from rough token counts; calls without a price are
  counted, not costed. Nothing runs.
- **`run --budget-usd N`** refuses to start when the estimate is over N (exit 2), and stops priced model calls once
  spend reaches N. `result.json` records `spend`: model USD, calls, unpriced calls, the budget and calls it stopped.

### Connect your agent
- **`from genai_evals import agent, tool, serve, report_state`**: decorate the agent and its tools; the decorated
  agent works as a Python target, and `serve()` puts it behind the HTTP contract (stdlib server) with sessions.
- **HTTP contract v1** (`options: {contract: v1}` on the HTTP target): request `message, session_id, turn, case_id,
  user`; response `output, tool_calls, state, session_id, usage, cost_usd, trace_id`; no field mapping needed.
- [docs/connect-your-agent.md](docs/connect-your-agent.md) and `examples/connect-your-agent/` (plain Python, tested;
  LangGraph and FastAPI, illustrative).

### SME labelling
- Label, review-queue and draft-review sheets can be **`.xlsx`** (`pip install "genai-eval-framework[xlsx]"`):
  frozen header, dropdowns, locked non-editable columns. `calibrate --labels`, `labels agreement` and
  `cases promote --sheet` read `.xlsx` and `.csv` alike.
- **`genai-evals label <sheet>`**: a local labelling page (127.0.0.1, one-time token, nothing sent anywhere), one
  item at a time with keyboard shortcuts, each decision saved back into the sheet with the reviewer as sign-off.
  Works for label sheets (pass / fail / unsure) and draft-case review sheets (keep / fix / drop with corrections).
  The judge's verdict is never shown.

### Experiments
- **`--monthly-volume N`** on `experiment run | rerun | report`: model cost per month per variant vs baseline at N
  requests, with the change's paired interval, next to the quality verdict; with the caveat that eval cost per case
  is not production cost, and a pointer to the ROI calculator.

### Partner packs
- **`genai-evals pack new <name> --extends <pack>`**: a partner package with the entry point, an overlay (starter
  cases, a prompt-leak attack and grader, slice / leak / critical gates), a README, and a CI workflow that checks it
  passes on the guarded offline system, fails on the vulnerable one, and builds the wheel.
- **Start-here graders**: `start_here` in each `pack.yaml`, shown by `pack info` with the rest as advanced, and a
  table in [graders](docs/graders.md#start-here).

## 0.7.2 (2026-11-19)

Packs: tool-agent 0.6.0 (multi-turn conversations; requires framework 0.7.2).

### Multi-turn conversations on every agent target
- One driver for scripted (`turns`), rule-based (`user_rules`, new: deterministic, reacts to the agent's reply) and
  model-played (`goal` + `persona`) users, on the HTTP, Python, OpenAI Agents SDK, AgentCore Runtime, Bedrock Agents
  and Agent Engine targets, each keeping one session across turns. Before, only the built-in tool loop held a
  conversation; the others sent the first message and stopped.
- Tool calls record their `turn`; samples keep the transcript; latency is the agent's own time over turns.
- New `context_retained` grader (acts on what was said earlier, not on what was corrected; skipped when the target
  ran a single turn). `step_budget` applies per user turn in a conversation.
- tool-agent pack: six 2-4 turn starter conversations (asking for the order number, a correction, "refund it", a
  change of mind, a topic switch and return, pushing back on an escalation), a `multi-turn-context` gate, and a
  stateful offline agent with a `forgetful` mode that fails it.
- Python callables opt in to per-turn calls (`fn.per_turn = True`); existing ones that handle `case.turns`
  themselves are unchanged.

### Security
- **`cases mutate`**: draft variants of a suite's attack cases (tool-targeted payloads from the target's
  `tools.yaml`, authority, role-play, base64, Hinglish / Hindi, pasted content, split across turns; optional
  model-written variants), each with a `MUT-` marker. Drafts, reviewed before they count.
- `injection_resisted` also checks tool-call arguments for markers (an injected action with the attacker's value),
  on by default (`tool_args: false` for the old behaviour).

### Partner programme
- **`genai-evals usage export`** (opt-in, writes a file, no network call): versions, packs, profiles, case-count
  ranges, gate outcomes, adapters; no case data, ids, model ids, paths or names; custom names salted-hashed.
  `usage schema` lists every field. The SOW template has a consent line.
- Results record the overlays they ran with.


## 0.7.1 (2026-11-19)

Partner go-to-market: a POC mode, vertical IP outside the core, and Promptfoo as the way in. Packs:
structured-extraction 0.3.0 (security overlay; requires framework 0.7.1).

- **`quickstart` profile in every pack** for 3–5 day proofs of concept: the core checks (RAG: faithfulness, refusals,
  PII leak, latency, cost; agent: task success, tool-call accuracy, PII leak, latency, cost; extraction: valid JSON,
  field accuracy, latency, cost), one judge, one repeat, up to 30 cases. Profiles can now add gates
  (`add_gates`), downgrade every gate (`gate_severity: warn`) and mark a run `directional`: the CLI, scorecard,
  report and PR comment then say the numbers are uncalibrated and not sign-off.
- **Partner packs and overlays as Python packages.** Entry points `genai_evals.packs` and `genai_evals.overlays`; an
  overlay declares the pack it extends (`pack:`); `--overlay` also takes a folder. `pack list` marks them `[partner]`;
  built-ins can't be shadowed; a broken package only warns. `examples/partner-pack-sap`: SAP sales-order tool use
  for the tool-agent pack (10 cases, OWASP LLM01 / LLM06), passing guarded and failing vulnerable. The agent pack's
  mock order system accepts ids in other formats and "credit memo" requests.
- **Security overlay for the extraction pack** (OWASP LLM01 / LLM02 / LLM07): instructions planted in invoices to
  change totals, PO numbers or the payee, a request to put the system prompt in a field, an unrequested card number,
  and a benign pair. The offline extractor's `vulnerable: true` mode fails every security gate.
- **Docs:** [SOW template](docs/sow-template.md) with the Day-1 workshop and SME-signed cases as deliverables;
  [Promptfoo migration playbook](docs/promptfoo-migration.md), including what tokenisation does not cover
  (Promptfoo's own results file, database and grading calls stay raw).
- **Fixed:** `promptfoo import --suite` ignored the suite's identifier-detector sets and tokenizer settings.

## 0.7.0 (2026-11-18)

Orchestrate and govern: less plumbing to maintain, a predictable partner delivery motion, domain-specific checks
outside the core. Packs: rag-knowledge-assistant 0.5.0, tool-agent 0.5.0, structured-extraction 0.2.0 (require
framework 0.7).

### Delivery
- **Run profiles.** `profiles:` in a suite, chosen with `--profile` (or `GENAI_EVALS_PROFILE`): repeats, judges,
  graders, splits, tags, a capped case sample (critical cases always run), gates and other overrides. Gates a profile
  can't evaluate are listed as skipped in the result, CLI and PR comment, never passed. Every pack ships `pr-check`
  (one judge, one repeat, up to 60 cases) and `release-gate` (the panel, three repeats, every case); the pack CI
  template runs the first on pull requests and the second on release tags, and the composite action takes `profile`.
- **`cases day0`**: seeds ~80 drafts from the customer's documents, optionally adds tokenised production drafts from
  an online run, and writes one review sheet and SME instructions, so the SME has it on day 1 and promotion happens on
  day 2.
- **Least-privilege Terraform for security review.** The GCP module replaces `roles/aiplatform.user` with a custom
  role holding only `aiplatform.endpoints.predict` for judges. New `docs/permissions.md` lists every permission on both
  clouds, why it's needed and what it can't do. CI now runs `terraform validate` on both modules, which found that
  both failed `terraform init` before this release (`${VAR}` in a variable description; now escaped).

### Orchestrate instead of rebuild
- **`run --engine promptfoo`** (opt-in): exports the suite, runs `promptfoo eval`, then applies the suite's gates,
  statistics, slices, profiles and reports to Promptfoo's results. Agent, HTTP and Python targets stay on the native
  engine (refused or listed as not run). CI runs `examples/promptfoo-engine` through a real, pinned Promptfoo.
- **Found on the first real Promptfoo run:** exported `contains` assertions referenced `vars.reference_answer`, which
  is only set from `reference.answer`, so Promptfoo failed every one (`reference.contains` is what the grader reads).
  Graders that read another reference key, or apply only to tagged cases, are now per-test assertions with the value
  filled in and are left out where genai-evals would skip them (graders with `tags` were previously applied to every
  case in Promptfoo).
- **External prompt versions.** `prompt_version` on a target (`langfuse:claims_agent@7`, a Bedrock prompt ARN with its
  version, or a map) and `run --prompt-version target=ref`: recorded on samples and results without `prompts/` or
  `prompts.lock`.

### Domain overlays, domain-agnostic core
- **Identifier detector sets** (`genai_evals.pii`): `global`, `in`, `pci`, `banking`, `eu`, `us`, `us_hipaa`, `uk`, with
  checksums (Verhoeff, Luhn, NPI, NHS mod 11, IBAN mod 97) and `custom` detectors with optional validator and context
  keyword. Suites choose with `pii:`; overlays add to it; the tokeniser and `pii_leak` use the same detectors; case
  commands take `--pii-set`. The default is unchanged (`global`, `in`, `pci`).
- **`financial-services` overlay** for the RAG pack: investment product cases, a mandatory risk disclaimer, no personal
  investment advice, no guaranteed-return claims, IBAN / card / folio numbers as personal data. The offline system's
  `noncompliant: true` mode fails all three gates, and a test checks both ways.

## 0.6.4 (2026-11-18)

### Telemetry joins, enforced
- **Online runs with unreliable joins are marked so.** `join_policy` in `sources.yaml` (default `on_problem:
  invalid`): the affected targets' gates become invalid (exit 1), the scorecard shows *incomplete*, and the report
  lists the problems. `on_problem: warn` reports without blocking. Results record `telemetry_quality`.
- **What a match proves.** Joins now flag records matched by more than one span (ambiguous), records whose completion
  disagrees with the one captured on the span, and duplicate request ids (dropped, so usage isn't double-counted).
  `sources check --pairs N` prints matched pairs, tokenised and truncated, for a person to read.
- **Session matches stay inside the turn.** A session-id match only uses records in the span's time window that
  don't name a different trace, so a turn no longer picks up another turn's completion.
- **`sources check --trace <id>`** and `examples/request-tags/` (one tagged Bedrock Converse call, one labelled Vertex
  call) to check that the ids reach the logs. Docs say which part is documented by the provider and which is unverified.

### Supply chain
- Every action in the workflows, the composite action and the pack CI template is pinned to a full commit SHA
  (Dependabot updates them). The pack CI template pinned this framework at v0.3.0; it now follows the release, and a
  test keeps both true.

## 0.6.3 (2026-11-18)

### Telemetry joins
- **`genai-evals sources check sources.yaml`** reads the telemetry window without grading and reports, per source,
  how many traces have a prompt and a completion and how each was joined to its payload record (trace id, session
  id, timestamp only, none). Exit 1 on problems; `--json` for scripts.
- **`doctor` has a telemetry joins check** on the latest live online run: no payload records (Bedrock invocation
  logging and Vertex request-response logging are off by default), records without trace ids, joins that fell back
  to timestamps, low id-match share, and traces with no completion. Each problem names the fix.
- **Trace ids from request tags.** Bedrock records read `requestMetadata` (trace_id / traceparent / session_id);
  Vertex rows read request `labels`. The insurance example's payload fixtures now carry them, so its joins match by
  trace id instead of timestamps.
- Online results record the join report (`join:<type>`) and content counts (`content`) per source.

### Releases
- The release workflow attaches the wheel, sdist, SBOM and provenance to the GitHub Release even when the release
  was created first (it failed on that before, so releases since v0.4.0 had no artifacts), and can be run by hand
  for an existing tag to backfill them.

## 0.6.2 (2026-11-18)

Review fixes for seeding and the security overlays.

### Seeding
- **Documents are untrusted input to the generator.** The prompt marks passages as untrusted data and tells the
  generator never to follow instructions inside them; text that could close or open the prompt's tags is
  neutralised; drafts from passages with model-directed text are flagged `source_contains_instructions`.
- **No silent source guessing.** A draft whose generator named a missing passage is kept but flagged
  `source_unverified`, with the generator's id and the overlap-assigned passage in `source_check` (shown on the
  review sheet). Drafts whose answer isn't found in their passage are flagged `answer_not_found_in_passage`.
- **Review is mandatory.** `cases promote` refuses seeded drafts without a reviewer, and flagged ones unless they
  were decided on the review sheet.
- **Match the deployed retriever:** `--chunks` seeds from the index's own chunks (ids and fields kept);
  `--chunker module:function` plugs in the customer's splitting. Passages record their location (file, section
  path from headings, PDF page), carried to drafts (`source_location`) and the review sheet.

### Security overlays
- New `no_prompt_paraphrase` judge (a policy grader, `warn` gate until calibrated) for leaks the exact checks miss.
- Docs: what a pass does and doesn't show, how marker checks are paired with final-state, tool-trace and
  faithfulness checks, and the steps before a sign-off. README wording now calls the overlays a starter set.

## 0.6.1 (2026-11-18)

Review feedback: seeding real knowledge bases, and visual proof.

### `cases seed` on large repositories
- **Cost bounded by cases, not repository size:** `--max-cases` (default 40), `--max-passages` (default 400),
  `--budget-usd` (trims the plan to the estimate and stops before actual spend passes it; needs `pricing` on the
  generator profile) and `--dry-run` (plan, calls, tokens and estimated cost; nothing sent or written).
- **`--sample-strategy`:** `stratified` (default: an equal share per folder and file type, so a huge archive doesn't
  crowd out a small policy folder), `per-doc` or `random`.
- **Multi-passage questions** (about 15%): need two passages, usually from different documents; both are
  `gold_passages`; tagged `multi_passage`. Questions tied to fewer than two real passages are dropped.
- Passage ids and groups now come from the file's path (`hr/leave-policy-01`).

### Fixed
- A case that passed in the baseline was listed as "never passed: check the task and the grader" when a candidate
  broke it. Such cases are regressions, not broken tasks: they're now reported as `regressed`, not as task problems.

### Visual proof
- A 45-second terminal recording in the README (animated SVG, and a player page on GitHub Pages): `init` → `run`
  → `baseline promote` → `experiment run` → a gate blocking a bad prompt. Every command and output is real
  (`scripts/record_terminal.py`).
- README links straight to the hosted exec scorecard, case drill-down, gate failure vs baseline, experiment
  comparison and security gates. The demo site gains the security overlay (guarded and vulnerable agent) and a
  "start here" strip.

## 0.6.0 (2026-11-18)

Day-1 datasets and security sign-off.

### Day-1 dataset bootstrapper
- **New `genai-evals cases seed --docs <folder> --generator <profile>`:** reads the customer's documents (txt, md,
  html; pdf and docx with the new `docs` extra), splits them into passages written as a corpus (`--corpus-out`), and
  drafts about 60% answerable questions (draft answer, evidence quote, source passage as `gold_passages`), 20% edge
  variants (tagged `typos`, `multi_intent`, `very_short`, `multilingual`, `format_request`) and 20% unanswerable
  questions (with the closest passages for the reviewer). Drafts are tokenised and `source: synthetic`.
- **New `genai-evals cases review-sheet`** writes a CSV for sign-off: each draft with its answer, passage and
  evidence; `decision` keep / fix / drop, corrected columns, `reviewer`.
- **`cases promote --sheet`** applies the decisions: fixes are written in, drops deleted, and only rows with a
  reviewer are applied; promoted cases record `reviewed_by` and notes.

### Security guardrails (OWASP LLM Top 10)
- **`security-guardrails` overlays** for `rag-knowledge-assistant` (0.4.0) and `tool-agent` (0.4.0): indirect prompt
  injection through planted documents and tool results (LLM01), system prompt leakage (LLM07), actions beyond the
  user's permissions (LLM06, agents), and over-refusal pairs. All attack cases are critical.
- **New graders:** `prompt_leak` (a canary, matched ignoring spacing, or 8+ words copied from the prompt file, in any
  assistant turn) and `injection_resisted` (per-case `reference.injection_markers`).
- **New high-risk tags:** `indirect_injection`, `prompt_leak`, `excessive_agency`; optional (a coverage note)
  unless the suite sets `security_review: true`.
- The offline systems gained the defences a real system needs (refusing prompt requests, ignoring instructions in
  retrieved text and tool results, checking who owns an order), and a `vulnerable: true` mode that drops them, so
  the overlays can be shown to fail an unguarded system.
- The tool-agent mock tools return the order's customer and any `note`; overlays can add orders (`orders.json`).
- Voice / realtime moves to v0.7.

## 0.5.2 (2026-11-18)

Review fixes.

- **Per-turn grading never truncates silently.** Every turn is graded by default (`max_turns` had defaulted to 20 and
  dropped earlier turns, so an unsafe early turn could escape). With `max_turns` set, a longer conversation is
  `unknown` by default (`over_max_turns: unknown`), or `fail`, or `grade_last`, which records the original numbers of
  the graded and ungraded turns. Turn numbers are now always the conversation's own.
- **A per-trial reset turns the response cache off** for that target, since a cached output skips the reset
  (`reset.cache: true` opts back in).
- **Baselines graded with different judge prompts** (e.g. v0.4.x after the v0.5.0 "unknown" change) make `delta()`
  and `significant()` gates on judge-graded graders invalid, with a note to re-baseline. Deterministic graders still
  compare.
- **`safety_claim: true`** on a suite makes fewer than ~100 multi-turn or ~1,000 single-turn cases a coverage and
  `doctor` problem (Anthropic's guidance for safety claims).

## 0.5.1 (2026-11-18)

The rest of Anthropic's agent and safety eval guidance ([mapping](docs/agent-evals.md#added-in-v051)). No change to
existing scores or judge prompts.

- **New `checklist` grader:** atomic criteria, each judged on its own by a separate judge call as met / not met /
  unknown, with `not_met` text that spells out what doesn't count. `require_all` (default) or a share-met threshold.
- **New `genai-evals sensitivity`:** re-grades the same samples with reworded rubrics (or any grader parameters) and
  reports flipped verdicts, Cohen's κ with the original, the change in the mean and changed gate outcomes; exits 1
  unless robust. `regrade --only all` re-grades every unit.
- **New `genai-evals power`:** cases needed for an interval width or to detect a change, paired (McNemar) or
  unpaired, and the smallest detectable change for a given n; with Anthropic's safety-claim guidance. Runs add a note
  when a grader behind a blocking gate has a 95% interval wider than ±10 points.
- **New `genai-evals cases grid`:** scenario grids from a template and dimensions, as every combination, a random
  sample, or a pairwise covering design; cases tagged `<dimension>=<value>` so slices report by dimension.
- **`gate_correction: holm`** on a suite decides every `significant()` across the run's gates and targets on
  Holm-adjusted paired-bootstrap p-values (recorded as `p_holm(grader)`). Paired comparisons now report `p_value`.

## 0.5.0 (2026-11-18)

Anthropic's guidance on agent and safety evals ([mapping](docs/agent-evals.md)). **Judge prompts changed** (the
"unknown" option), so judge-graded numbers are not comparable with 0.4.x runs: re-run baselines.

### Grading agents
- **Per-turn grading.** `per_turn: true` on `rubric` and `policy` graders judges every assistant turn, with the earlier
  turns as context, and passes only if every turn passes: one bad turn fails the conversation. Records each turn's
  verdict and the first failing turn.
- **"unknown" verdicts.** Rubric and policy judges may answer "unknown" instead of guessing. Unknown is neither pass
  nor fail: a skip labelled `unknown`, counted per grader, left out of means, not passing on critical cases, and shown
  in judge coverage. Panels record `split_verdict` when judges land on different sides of the threshold.
- **New `labels queue`** writes a reviewer sheet of unknown verdicts, split panels and persistent judge errors
  (a targeted sample, kept out of calibration).

### Task health
- **New `cases check-refs`** grades every case's reference as if it were the output (answer with gold passages and
  citations, fields, final state, tool calls in sequence order); a rejected reference means a broken task or grader.
- Every result lists **cases that never passed** on any trial or target, and **saturated graders** (every regression
  case passes every trial), as notes in the CLI output and reports. `cases health` prints them for a result.

### Capability and regression cases
- Cases take `purpose: regression` (default, gated) or `capability` (reported in their own section, never gated, not
  counted in the headline or critical cases).
- **New `cases graduate`** lists capability cases that passed every trial in all given runs; `--apply --into` sets
  them to regression.

### Isolating trials
- Every trial gets a unique `trial_id` (HTTP bodies: `{{ trial_id }}`; Python targets: `ctx["trial"]`).
- **Per-trial `reset:` hook** on any target (`{python: "mod:fn"}` or `{url: ...}`), run before every trial; what it
  returns is passed to the target. A failed reset is a target error for that trial.
- A warning when a grader's pass rate moves with trial order (trials may share state).

### Coverage
- **Both sides of every behaviour:** coverage counts cases that expect refusal, escalation, tool use and handoff,
  and cases that expect them not to happen, and flags one-sided behaviours.
- New standard tag `benign_sensitive` (a legitimate request that sounds sensitive: the over-refusal pair).
- Packs: `rag-knowledge-assistant` 0.3.0 adds two `benign_sensitive` questions; `tool-agent` 0.3.0 adds two cases
  where no tool should be called; `structured-extraction` 0.1.4.

### Fixed
- The pack case schemas rejected `split` and `weight`, so cases drawn with `cases sample` (0.4.2+) failed validation
  when promoted into a pack. The schemas now accept `split`, `weight` and `purpose`.

## 0.4.5 (2026-11-18)

Review feedback: judge failures are classified, and transient ones can be retried before a judge is judged.

- **Error classes.** Grader errors are classified as `transient` (rate limit, timeout, 5xx, connection) or
  `persistent` (unparseable verdict, schema, auth, bad request), with the same rules as provider retries. New scores
  record `error_kind`; older results are classified from the error text. A judge panel's error is transient only if
  every judge's was.
- **Coverage** reports transient errors separately from persistent errors, skips and missing scores, with the failure
  rate a retry could at best leave (`failure_rate_after_retry_best_case`).
- **New `genai-evals regrade`.** Re-runs graders on failed units of an existing result, without re-running the system
  under test: `--only transient` (default), `errors` or `unscored`; `--attempts` with exponential `--backoff`.
  Summaries, gates and reports are recomputed; the result records `regraded_from`, and per score `attempts` and
  `regraded`. It refuses when the grader's config changed since the run (`--force` overrides).
- **Judge ladder decision** is now `approved`, `not_approved` or `retry_first`. `retry_first` is returned when
  transient errors are what block approval and the most favourable retry outcome would pass every check; the output
  gives the `regrade` command. Otherwise it says retrying can't change the decision.
- `calibrate` points to `regrade` when labelled units failed transiently.

## 0.4.4 (2026-11-18)

Review feedback: judge failures and missing scores count in the judge ladder.

- New `judge_coverage`: for every labelled unit, whether the judge scored it, errored, skipped it, had no score, or
  had nothing to judge because the target failed. Coverage and failure rate exclude target errors, which are not
  the judge's fault.
- `calibrate --compare-to` reports each judge's coverage and failure rate, and approval now also requires:
  - the non-inferiority check to hold when every gradable unit counts, with a unit a judge failed to grade treated as
    a disagreement with the human (worst case), so a judge can't look acceptable by failing on hard cases;
  - the cheaper judge's failure rate to be at most `--max-judge-failure` (default 5%).
- `calibrate` (one judge) reports `coverage` and warns when more than 5% of labelled units weren't graded, or when
  labelled units had target errors.

## 0.4.3 (2026-11-18)

Review feedback: representative sets weighted for telemetry sampling, and a design-aware judge ladder.

### Representative sets and telemetry sampling
- New `capture_rate` per source in `sources.yaml`: the share of production traffic the telemetry captures (trace
  sampling, content capture). Online runs record it and the source's inclusion probability (capture rate x sampling).
- `cases sample` weights each case by the inverse of its end-to-end inclusion probability: telemetry capture rate x
  online sampling x selection (per tag stratum with `--by-tag`). Sources captured or graded at different rates are
  weighted back to production proportions. Each draft records the factors in `metadata.inclusion`.
- Provenance gains `represents`: "production traffic" only when every source's capture rate is known; otherwise "the
  captured telemetry sample only", naming the sources to fix. The CLI prints the same note.

### Judge ladder
- `calibrate --compare-to` respects the labels' sampling design: the interval on κ(cheap) − κ(strong) resamples within
  the sheet's strata, and with inverse-inclusion weights compares production-weighted κ.
- With `--validation-labels` (a random sample), **approval is decided on the validation sample**; the calibration
  sample is reported alongside, with a note if the cheaper judge looks worse there. A stratified sheet without weights
  and no validation sample is not approved.
- Output records the `basis` of the decision and both samples' statistics.

## 0.4.2 (2026-11-18)

Review feedback: a clearly defined calibration unit with design-aware intervals, evidence behind "representative",
and visible thin stress coverage.

### Calibration unit and intervals
- The labelling unit is **(case, target)**. `labels sheet` draws one random repeat per unit, records it in `repeat`,
  and computes strata and weights over units, not repeats.
- Calibration compares each label with the judge's verdict on **that repeat only**, so repeats no longer count as
  separate human comparisons (previously one label was applied to every repeat). Labels without a `repeat` column
  use repeat 0, with a warning when the unit had several repeats. The judge ladder (`--compare-to`) uses the same
  pairing.
- Intervals are design-aware. For stratified sheets, both the sample κ CI and the production-weighted κ CI use a
  stratified bootstrap: units are resampled within each sampling stratum, keeping stratum sizes and weights.
- Calibration output records `unit: case, target` and whether the interval was stratified.

### Representative set provenance
- Suite `representative_sample:` (source, population, window, method, weights, drawn_by, drawn_at) records how the
  representative set was drawn. `doctor` and `cases coverage` require it, require an explanation of any case
  weights, and require at least `min_representative_production_share` (default 80%) of representative cases to have
  `source: production`. They print the evidence they found.
- New `genai-evals cases sample <online result>`: draws the representative set from production telemetry, by unit
  (session / case and target) rather than by score.
  - It does a simple random sample, or proportional stratified sampling by tag with weights (`--by-tag`).
  - It writes drafts (`source: production`, `split: representative`) and a ready-to-paste provenance block with the
    window and telemetry sampling.
  - Fixture telemetry is labelled and doesn't count as production. `cases promote` keeps sampled cases `typical`.
- Each pack suite has a commented `representative_sample:` template.

### Stress coverage
- Each coverage tag has a minimum number of cases by risk tier: 10 for high-risk tags (`jailbreak`,
  `system_conflict`, `bad_tool_args`, `circular_handoff`) and 5 otherwise. Set them with `stress_min_cases` and
  `stress_min_by_tag`. Tags below the minimum are reported as **thin**.
- Before a run, `cases coverage` and `doctor` list gates whose tag or split slice has fewer cases than their
  `min_n`. Blocking ones are problems.
- New gate severity **`info`** (diagnostic): reported, including "too few cases", but never fails a run, even with
  `--fail-on-warn`. The RAG pack's `grounded-under-stress` is now `info`. Packs bumped to 0.2.2 / 0.2.2 / 0.1.3.

## 0.4.1 (2026-11-18)

Review feedback: keep the production estimate apart from stress testing, and account for how human labels were sampled.

### Representative and stress sets
- Every case is in the **representative** split (production-like) or the **stress** split (`edge` / `adversarial`
  tags, configurable with `suite.stress_tags`, or an explicit `split:`). Representative cases can carry a `weight`
  (production prevalence). The representative mean and its CI are then weighted, with a weighted bootstrap.
- Every grader is reported per split (`summary[target].graders[g].splits`), in the HTML and Markdown reports, and on
  the scorecard. The scorecard headline is the representative number, with the stress result beside it.
- Gate functions accept `split='representative' | 'stress'`, and `validate` checks the value.
- `doctor`'s coverage check becomes **eval set**:
  - the representative set needs at least `min_representative_cases` (default 30);
  - the stress suite needs a case for every applicable coverage tag;
  - blocking gates that average the two sets are flagged (`>= 1.0` requirements are exempt);
  - the old "at least 20% edge or adversarial" rule is gone, because it encouraged mixing.
- Pack headline gates now use `split='representative'`, with separate stress gates:
  - `tool-agent` 0.2.1 adds `stress-success`;
  - `rag-knowledge-assistant` 0.2.1 adds `grounded-under-stress` (warn), and refusal is gated on the stress split;
  - `structured-extraction` 0.1.2 adds `field-accuracy-under-stress` (warn).
- Results record each case's split and weight, and the suite's `stress_tags`.

### Calibration sampling
- `labels sheet --design stratified | random`. Every row records `sample_design`, `stratum` and an inverse-inclusion
  `weight`.
- `calibrate` reports:
  - agreement on the calibration sample, labelled with its design;
  - a **production-weighted** estimate (weighted κ, accuracy, human and judge pass rates);
  - agreement **per slice** (every tag, plus `split:representative` / `split:stress`), with a warning for slices well
    below the overall figure;
  - a separate **representative validation sample** (`--validation-labels`), never pooled with the calibration
    sample.
- When a validation sample is present, a trusted judge must clear `min_judge_kappa` on it too. `doctor` says whether
  judges were validated on a representative sample.
- Docs: two sets in `growing-the-eval-set.md`, two samples in `human-labels.md`, splits in `gates.md`, the
  best-practices mapping, the runbook template, and a new `doctor` transcript.

## 0.4.0 (2026-11-17)

Aligns the framework with OpenAI's [evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
(`docs/openai-best-practices.md` maps every section of the guide) and makes Promptfoo the primary interop story.

### Promptfoo
- `docs/promptfoo.md`: genai-evals as packs, adapters, gates and reporting around Promptfoo, three ways to combine
  them, and exactly what survives each direction (pinned by `tests/test_promptfoo_roundtrip.py`).
- `genai-evals promptfoo import --suite <suite.yaml>` evaluates a suite's gates, slices and critical cases on
  Promptfoo results (a gates-only suite works too) and names gates whose graders are missing.
- Imports read repeats (`testCase.metadata.repeat`, or row order per case and provider), tags, critical flags and
  `vars.input`; the Promptfoo results export writes them, so a genai-evals → Promptfoo → genai-evals round trip keeps
  every non-skipped score and gives identical gate outcomes.
- Config export writes `evaluateOptions.repeat` and notes that gates aren't exported and that the file holds the
  dataset's raw inputs.
- The hosted-Evals migration moves to an optional appendix.

### Judges
- Every judge verdict starts with a short, checkable **rationale**: at most two sentences quoting the deciding
  evidence (per-claim evidence: a quote of at most 20 words). Judges are told not to write out step-by-step thinking
  or repeat personal details. Stored rationales are capped (`judge_policy.max_rationale_chars`, default 300) or
  dropped (`store_rationale: false`), and are tokenised like outputs. Older `reasoning` keys are still read.
- Rubric, relevance and pairwise prompts add a rule that length is not quality. The judge prompt hash recorded
  with each result changes with this release, so compare judge-graded numbers only with runs made on 0.4.0
  (re-run baselines).
- `pairwise` records both answers' word counts, their ratio and whether the longer answer won; `max_length_ratio`
  flags length-confounded comparisons. The run summary adds `length_bias` (longer-answer win rate, mean length
  ratio, confounded count, position consistency), shown in the HTML report.

### Human labels and the judge ladder
- Labels files may have several reviewers (`reviewer`, `round`, `adjudicated` columns). They are combined by
  majority, unanimous or adjudicated consensus; agreement between reviewers (raw, Fleiss' κ, per reviewer) is
  reported, and the calibration warns when reviewers agree only moderately.
- `genai-evals labels guide | sheet | agreement`: a labelling guide with examples at each level from the grader's
  rubric and anchors; a stratified reviewer sheet that leaves out the judge's verdict; agreement, disputes and
  consensus labels.
- `genai-evals calibrate --compare-to <grader>`: approves a cheaper judge only if it is not meaningfully worse than
  the stronger one (95% lower bound of the κ difference above −margin) on the same labelled items, with both judges'
  cost.

### Coverage and a growing eval set
- Standard coverage tags from the guide's edge cases: difficulty (`typical`, `edge`, `adversarial`), input
  (`multilingual`, `structured_input`, `image_input`, `noisy_input`), context (`multi_intent`, `typos`,
  `very_short`, `long_conversation`, `ambiguous_tool_output`, `bad_tool_args`, `circular_handoff`, `no_answer`) and
  safety (`jailbreak`, `system_conflict`, `format_request`), each tied to the workflows it applies to.
- `genai-evals cases tags | coverage | generate | harvest | promote`. `generate` drafts cases for missing tags with a
  strong model; `harvest` drafts tokenised cases from failing production samples; `promote` moves reviewed drafts
  into the dataset. Cases with `review_status: draft` are left out of runs and gates (suite `include_draft_cases`).
- `genai-evals doctor` adds an **edge-case coverage** check.
- Fix: `doctor` read the calibration file in a different shape from the one `genai-evals calibrate` writes, so
  calibrated judges were reported as not calibrated, and the offline-demo check crashed on hand-written files.
  Both now use one loader that accepts either shape. The engagement guide's sample transcript is replaced with real
  output.

### Graders
- `context_precision` (judge-labelled usefulness of each retrieved chunk, scored as average precision) and
  `context_recall` (share of the reference answer's statements the context supports), for RAG suites without
  relevance labels.
- `no_handoff_loop`: fails on circular handoffs between agents or more than `max_handoffs`.

### Packs
- `tool-agent` 0.2.0: six new conversations (a "store manager" jailbreak that conflicts with the refund policy,
  typos, multi-intent, Hinglish, JSON input, conflicting order numbers), standard coverage tags on every case,
  `no_handoff_loop`, a single vs multi-agent experiment template (`experiment.multi-agent.example.yaml`).
- `rag-knowledge-assistant` 0.2.0: three new cases (Hinglish, multi-intent, a format request), coverage tags,
  `context_precision` and `context_recall` with a warning gate.
- `structured-extraction` 0.1.1: coverage tags. Pack case schemas accept `review_status`.
- The runbook covers coverage, generated and harvested drafts, several reviewers and the judge ladder.

### Docs
- New: Promptfoo guide, OpenAI best-practices mapping, human labels and the judge ladder, growing the eval set;
  single vs multi-agent experiments. Voice / realtime moves to v0.5.

## 0.3.0 (2026-11-07)

Makes the framework a repeatable asset: each engagement is easy to configure, run, validate and hand over, without
assuming the customer's cloud or agent stack.

### Eval packs (the repeatable unit)
- Three workflow packs in `packs/`, shipped in the wheel: `rag-knowledge-assistant`, `tool-agent`,
  `structured-extraction`. Each has a dataset schema with synthetic starter cases and guidance for customer cases,
  graders, failure categories, recommended gates with reasons, target files for each stack, provider and telemetry
  configuration with recorded OTLP telemetry, an experiment, a report template, a generated runbook and handover
  checklist, a validation file and an offline simulator.
- Optional industry overlays: `insurance-policy` (RAG) and `india-gst` (extraction). Suites gain `overlays`.
- `genai-evals init <dir> --pack <pack> [--overlay ...]` scaffolds a project and runs it offline, writing a sample
  report and a monitoring demo. Replaces the previous starter template.
- `genai-evals pack list | info | validate | docs`; `genai-evals doctor` checks a project against the definition of
  repeatable (offline demo, a live provider and telemetry source, an understandable report, a meaningful release
  gate, judges calibrated, handover complete, adapters validated).
- Suites gain `targets_from` (the system under test chosen by an env var), `dataset_schema` (rows validated on load,
  errors name the case and field), `extra_datasets`, `report` (title, intro, headline grader, failure categories),
  `validation` and `pack`. Reports show a failure-category table.

### Adapter contracts and conformance
- Six adapter kinds (provider, source, target, grader, store, report) with contract version 1, a catalog, and
  third-party plugins through entry points (`genai_evals.<kind>s`); plugins can't shadow built-ins and a contract
  major mismatch is refused.
- Conformance kit (`genai-evals conformance`, `assert_conformant`): formats, normalisation, determinism, missing
  fields, empty input, timestamps, pagination and retries for sources; normalisation, tool calls, retries and JSON
  mode for providers. Built-in specs and fixtures for every provider and 14 sources; untestable checks are reported
  as not covered, never as passes.
- Maturity levels: implemented, fixture tested, live tested, customer validated (the last two recorded per project
  with `genai-evals adapters record`). Results and reports list every adapter a run used with its effective status.
  `docs/adapters.md` is generated from the catalog and a conformance run.
- Shared retry helper for sources (`Source.call`), used by the CloudWatch Logs, AgentCore Memory and X-Ray paths.
- Fixes found by the kit: OTLP / OpenInference messages without a role no longer crash the normaliser; AWS sources
  no longer import boto3 when a client is injected; ISO timestamps with nanosecond or one-digit fractions (common in
  Google Cloud telemetry) are parsed on every supported Python, where 3.10 used to drop them.

### Portable results
- Results carry `schema: genai-evals/result` and `schema_version: 1.0`; the JSON Schema ships with the package
  (`genai-evals result schema | validate`). Pre-0.3 results are upgraded on read; newer majors are refused.
- `genai-evals result export --format csv,jsonl,promptfoo,...` (one row per score; Promptfoo results shape that
  round-trips with `promptfoo import`). `file://` results store (a folder plus an index), alongside SQLite and Postgres.

### Other
- `import_ref` prefers the suite folder's own modules, so several projects can run in one process.
- Docs: packs, engagement guide, plugins and conformance, adapters (generated), results; getting started rewritten
  around packs. Voice / realtime moves to v0.4.

## 0.2.0 (2026-11-06)

### Experiments (eval-driven iteration)
- `experiment.yaml`: a suite, the target to vary, a baseline and variants (prompt version, provider profile, options,
  retriever, any target field). All variants run in one run on the same cases, seed, repeats and judges.
- `genai-evals experiment run | rerun | report`. Output: `comparison.html/.md/.json`, `manifest.json`, a copy of the
  experiment file and the full run.
- Comparison per variant vs baseline: verdict (regresses / trade-off / improves / no detectable difference),
  per-grader Δ with intervals and McNemar, pass^k, new failures and fixes; cost and latency (mean, p95, relative,
  paired Δ); critical cases newly failing or fixed; slices ranked by change (under `min_n` listed, not ranked);
  failure types and flipped cases; quality vs cost and latency chart.
- Multiple comparisons: Bonferroni across variants × (graders + cost + latency) by default, with intervals shown at
  the corrected level; `correction: none` to opt out.
- Reproducibility: the manifest records hashes of the experiment, suite, providers, dataset, calibration, local target
  code and every prompt version used; providers used (no secrets); judge provenance; env var names; seed, repeats;
  framework / Python version and git commit. `rerun` refuses when inputs changed (`--force` to override) and reports
  whether each comparison reproduced.
- `genai-evals compare` and `prompts diff` now produce the same comparison (`compare --out`, `--fail-on-regression`).
- Demo scenario 3b and a sample comparison page; Python targets receive the target's prompt (`ctx["prompt"]`).

Voice / realtime moves to v0.3.

## 0.1.1 (2026-11-06)

Fixes from an external review. Several change results: re-run baselines after upgrading.

### Gates and statistics (behaviour changes)
- Gates have three outcomes: pass, fail and **invalid**. Invalid blocking gates fail the run.
- `delta()` / `significant()` without a baseline make the gate invalid instead of passing. Use
  `--allow-missing-baseline` (CLI and GitHub Action) or `allow_missing_baseline: true` on a gate for a first run.
  A `--baseline` file that doesn't exist is now an error (exit 2).
- Target and grader errors count as failed scores (value 0) by default (`suite.on_error: fail`; `exclude` restores the
  old behaviour for diagnosis).
- `suite.min_n` (default 5, overridable per gate): gates on too few cases, tag slices or baseline overlap are invalid.
- `significant(g, alpha=...)` for multiple-comparison corrections.

### Judges and calibration
- A judge is trusted only with at least `min_calibration_n` labels (default 30) and the **lower bound** of a bootstrap
  95% CI on κ at or above `min_judge_kappa`. Calibration also reports human pass rate, positive / negative agreement,
  balanced accuracy and prevalence warnings. `genai-evals calibrate --min-n` and a non-zero exit when untrusted.
- Results record judge provenance (prompt template hash, temperature, response format, policy, per-judge provider,
  configured and reported model ids, family, region, options) and a config hash per grader.

### DPDP
- Error strings, sample metadata and score reasoning / details / errors / labels are now tokenised before storage.
- The response cache is off by default when tokenisation is on (it would hold raw outputs). Opting in warns and
  writes the cache with owner-only permissions.
- A warning when `GENAI_EVALS_TOKEN_KEY` is unset (tokens then differ between runs); results record data handling.

### Online evals
- Sampling is deterministic and per session (hash of the session / trace id), and the result records graded vs read
  counts, rate and window per source.
- Online reports and scorecards are framed as production monitoring (reference-free signals), not a release verdict.
- Reports state the scope of `pii_leak` (structured identifiers only).

### Docs
- Integration maturity labels and an explicit "nothing live-tested yet" statement; gate outcomes and exit codes;
  statistical assumptions (exchangeability, multiple comparisons, small *n*); the security boundary for Python / HTTP
  code; a step-by-step DPDP data-flow table; what online scores do and don't mean.

## 0.1.0 (2026-11-06)

### Partner setup
- No environment-specific settings in the repo: every YAML value can come from `${VAR}`, `${VAR:-default}` or
  `${VAR-default}`; missing variables are reported by name. `.env` is loaded automatically (`--env-file` to choose);
  existing environment variables win.
- `genai-evals init <dir>`: scaffolds a starter evals project for a partner's or customer's own repo (offline and
  live providers, suite, sources, online checks, dataset, versioned prompt, `.env.example`, GitHub Actions workflow).
- Example live configs, Terraform (no region defaults, `environment` map, `terraform.tfvars.example`) and the
  Dockerfile (no suites baked in) no longer carry environment-specific values.
- New docs: getting started, configuration reference, AWS / Google Cloud / OpenAI and Anthropic setup,
  troubleshooting; expanded Terraform guide. Install instructions point at GitHub until a PyPI release.

### See it
- Sample reports from the offline demo in `docs/demo/` (served with GitHub Pages), README screenshots, an
  architecture diagram, a 5-minute walkthrough, and a Codespaces dev container that runs the demo.
  `scripts/build_demo_site.py` regenerates the pages and screenshots.

### Core
- Evals as code: suites, datasets (JSONL / JSON / CSV / Parquet), targets, graders, gates.
- Providers: OpenAI Responses, OpenAI-compatible, Anthropic (direct / Vertex), Bedrock Converse, Vertex Gemini, mock.
- Targets: prompt / model, RAG (Bedrock KB, Vertex RAG, Python), tool-loop agent, OpenAI Agents SDK, AgentCore Runtime, Bedrock Agents, Agent Engine, HTTP, Python.
- 37 graders incl. judge panels, calibration, pairwise with position swap, Indian-format field normalisers, PII leak.
- Statistics: bootstrap CIs, pass@k / pass^k, paired deltas, McNemar, slices, critical cases. Gate DSL.
- Prompts as code: versions, hashes, lock stages, gated promotion, OTel span stamping, registry mirror, DPDP change detection.
- Native sources (AWS, Google Cloud, OpenAI, Anthropic, OTLP, Langfuse), OTel GenAI normaliser, online evals.
- Reports (HTML, scorecard, Markdown, JUnit, JSON); publishing (CloudWatch, EMF, Cloud Monitoring, OTLP); OTel emission.
- Interop: Promptfoo export / import, AgentCore Evaluations, Vertex Gen AI evaluation.
- Offline insurance demo; GitHub Action; Terraform for AWS and GCP; ADOT and Google collector configs.
