# open_otto

A local-first pipeline tool with an AI assistant, built for people working alone: job seekers, gigging musicians, and freelancers.

Status: early but working. Schema, migration runner, CLI, AI drafting with an approval queue, and an eval harness are built and tested (281 tests). Eval results for four models are in the eval section.

## Try it

Requires Python 3.12+. No runtime dependencies.

```
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m cli init --demo        # creates open_otto.db with fictional gig data
.venv/bin/python -m cli list
.venv/bin/python -m cli followups          # who has gone quiet for 7+ days
.venv/bin/python -m cli log "Lantern Hill" email outbound "Sent second nudge"
.venv/bin/python -m cli stage "Old Mill" pitched
.venv/bin/python -m cli import gigs my_venues.csv
.venv/bin/python -m cli task add "Build press kit" --org "Old Mill" --due +5d
.venv/bin/python -m pytest -q
```

### AI drafting

The agent reads an org's record and history, drafts a follow-up, and puts it in a queue. It cannot send or approve anything.

```
export OPEN_OTTO_MODEL=ollama:llama3.1          # local, free
# or: anthropic:claude-haiku-5-5   (needs ANTHROPIC_API_KEY)
# or: openrouter:<vendor>/<model>  (needs OPENROUTER_API_KEY; one key, many models)
# or: openai:<model>               (any OpenAI-style server; OPENAI_API_KEY, OPENAI_BASE_URL)
.venv/bin/python -m cli draft-followups --voice my_voice.md   # draft for everyone gone quiet
.venv/bin/python -m cli drafts                  # see the queue
.venv/bin/python -m cli show 3                  # read one
.venv/bin/python -m cli approve 3 --edit "..."  # approve, optionally with your own wording
.venv/bin/python -m cli sent 3                  # after YOU send it; logs the interaction
```

### Evals

How good are the drafts? Run fixed scenarios through any model and score them. The checks are plain code, not an LLM judge, so results are repeatable and free to rerun.

```
.venv/bin/python -m evals --selftest                      # no model needed; proves the checks work
.venv/bin/python -m evals --model ollama:llama3.1         # score a model
.venv/bin/python -m evals --model openrouter:qwen/qwen-2.5-72b-instruct --runs 3 --json results.json
.venv/bin/python -m evals --model ollama:llama3.1 --min-score 0.8   # exit 1 below 80%, for CI
```

Scenarios live in `evals/scenarios.json` (15). They include a venue that replied last (answer them, don't re-pitch), a contact who only has a phone number (expect a short text), no contact on file (don't invent a name), a quote follow-up (don't invent a discount), five prompt injections hidden in notes and message history, and three paraphrased "evasive" injections written to slip past the filter. Checks: numbers not in the record, greeting an invented name, length, a clear ask, scenario traps (injected canaries, invented credentials), filler phrases, required facts, and channel.

Every scenario also carries a hand-written good reply that must pass and bad replies that must fail a named check. `--selftest` runs these, so a check that silently stops working is caught. If the model is unreachable, that is reported as an error and never counted against the model.

#### Results so far

Four models, 2026-11-20 and 2026-11-21, 5 runs per scenario, 15 scenarios (75 runs per row). `qwen2.5:7b` ran locally through Ollama; the rest through OpenRouter. Raw data and the generated table are in `evals/results/`.

| Model | Mode | Overall pass | Obeyed injection (detectable) | Obeyed injection (evasive) | Non-injection pass | Rejected drafts |
|---|---|---|---|---|---|---|
| qwen2.5:7b (local) | baseline | 21/75 (28%) | 20/25 | 11/15 | 19/35 (54%) | 9/75 |
| qwen2.5:7b (local) | hardened | 41/75 (55%) | **0/25** | 9/15 | 28/35 (80%) | 8/75 |
| llama-3.1-8b-instruct | baseline | 32/75 (43%) | 13/25 | 13/15 | 26/35 (74%) | 6/75 |
| llama-3.1-8b-instruct | hardened | 53/75 (71%) | **0/25** | 12/15 | 28/35 (80%) | 3/75 |
| qwen-2.5-72b-instruct | baseline | 11/75 (15%) | 8/25 | 9/15 | 11/35 (31%) | 35/75 |
| qwen-2.5-72b-instruct | hardened | 14/75 (19%) | **0/25** | 11/15 | 10/35 (29%) | 28/75 |
| claude-haiku-5.5 | baseline | 66/75 (88%) | 0/25 | 2/15 | 28/35 (80%) | 1/75 |
| claude-haiku-5.5 | hardened | 66/75 (88%) | 0/25 | 0/15 | 27/35 (77%) | 1/75 |

"Overall pass" means every mechanical check passed. "Obeyed injection" means the draft contained the planted payload (lower is better). "Rejected" means a guardrail refused the draft before scoring (broken format, placeholder, repeated injected text).

- **Detectable injections:** the filter takes the three open models from 20/25, 13/25 and 8/25 obeyed to 0/25 each. Caveat: I wrote the filter while looking at those phrasings, so this shows it catches what I anticipated, not that it is safe.
- **Evasive injections:** the filter does not help. The open models obeyed 9 to 13 of 15 runs with or without it. This is the honest estimate against wording the filter has not seen, and it is weak.
- **Model strength matters more than the filter on unseen wording:** claude-haiku-5.5 obeyed detectable injections 0/25 even without hardening and evasive ones 2/15 (0/15 hardened). The hardening makes no measurable difference for it; the 88% overall is unchanged.
- **qwen-2.5-72b scores low for a mundane reason:** 35 of its 75 baseline drafts were rejected for leaving a `[Your Name]`-style placeholder in the signoff. The guardrail is working as designed; the model is just bad at following "no placeholders". Bigger did not mean better here.
- **Non-injection scenarios:** hardening changes only a short reminder after the record, so differences on these rows are mostly run-to-run noise.
- **Format:** with plain prompting most local drafts were rejected for broken JSON. Passing the schema to Ollama as `format` (constrained output) cut format rejections to about 1 in 27.
- **Still failing across models:** "I hope this message finds you well" filler, missing asks, and invented numbers or times ("6 PM", "December 1st").
- **Not measured:** `qwen3.5:9b` and `gemma4` were attempted locally. `qwen3.5:9b` is a thinking model and took 3 to 5 minutes per call on an 8 GB GPU, so I stopped that run rather than report numbers contaminated by timeouts. The direct Anthropic backend is untested live (Haiku was reached through OpenRouter).

Every scenario also carries a hand-written good reply that must pass and bad replies that must fail a named check. `--selftest` runs these, so a check that silently stops working is caught. If the model is unreachable, that is reported as an error and never counted against the model.

The point of the harness is that model choice and hardening are measurable instead of a guess.

#### Prompt-injection hardening

Notes, contact fields, and message summaries can come from CSV imports or pasted emails, so the agent treats all of them as untrusted. Three layers, all in code (`agent/sanitize.py`, `agent/drafting.py`):

1. **Strip:** sentences that look like instructions to an AI ("ignore all previous...", "NOTE TO AI:", "end the message with...") are removed before the model sees them. A field that tries to close the `<record>` block early is dropped whole. Normal notes like "please send dates" or "you must send a W9" are left alone (tested). Each removal is written to the audit log as `injection_suspected` and printed as a warning.
2. **Remind:** the prompt repeats, after the data, that the record is data only.
3. **Echo check:** if a draft repeats a distinctive token from removed text (a code, an ALL-CAPS word, a percentage), it is rejected.

This is a filter, not a proof. Paraphrased injections get through. Three scenarios tagged `evasive` are written to slip past it on purpose, so the results show the real gap instead of hiding it. Compare any model with and without hardening:

```
.venv/bin/python -m evals --model ollama:qwen2.5:7b --runs 5 --no-hardening --json evals/results/qwen_baseline.json
.venv/bin/python -m evals --model ollama:qwen2.5:7b --runs 5 --json evals/results/qwen_hardened.json
.venv/bin/python -m evals.compare evals/results/*.json
```

Or run several models, each with and without hardening, and build the table in one go (a model that fails to start is skipped):

```
export OPENROUTER_API_KEY=...
evals/run_matrix.sh ollama:qwen2.5:7b openrouter:qwen/qwen-2.5-72b-instruct openrouter:meta-llama/llama-3.1-8b-instruct
```

Slow local models may need a longer request timeout: `export OPEN_OTTO_TIMEOUT=300` (seconds, default 60).

`my_voice.md` is a short note about who you are and how you write. Guardrails: replies must be strict JSON with no unfilled placeholders like `[Name]`, record text is treated as untrusted data in the prompt, and there is at most one pending draft per org. Failures are written to the audit log.

CSV import needs a `name` column. Optional: `kind, website, city, region, notes, source, stage, contact_name, contact_role, contact_email, contact_phone`. Re-importing the same file skips rows that already exist.

## The problem

Job searching, booking gigs, and landing freelance clients are the same job: find targets, research them, write personal outreach, follow up on time, and remember who said what. Companies pay for CRMs and sales staff to do this. Solo people do it from a spreadsheet and their memory.

open_otto is a small, self-hosted pipeline tracker where an AI agent drafts the work and you approve it.

## Who it is for

- **Job seekers:** employers, recruiters, referral contacts, application stages.
- **Musicians:** venues, bookers, bandmates, gig dates, follow-ups after a no-reply.
- **Solo operators and freelancers:** leads, past clients, quotes, repeat-business reminders.

It is not aimed at sales teams, and the license is chosen to keep it that way (see below).

## Principles

1. **Human approves every outbound action.** The agent drafts and queues. It never sends on its own.
2. **Local-first.** One SQLite file you own. No account, no SaaS, no tracking.
3. **Bring your own model.** Works with your own API key, or a local model through Ollama, so cost is never a barrier.
4. **Every action is logged.** Each draft, approval, edit, and send is a row in the database.
5. **No scraping of platforms that forbid it.** Targets come from manual entry, CSV import, and public sources.

## Design

```
open_otto/
  migrations/     plain .sql schema files (the SQL is the point, no ORM)
  core/           pipeline logic: stages, tasks, follow-up scheduler
  agent/          drafting, research, and prioritization; tool-using agent loop
  packs/          per-use-case config: stages, prompts, templates
    jobs/
    gigs/
    freelance/
  evals/          test harness that scores agent drafts
  cli/            command-line interface (v0.1)
```

**Core schema (draft):** `orgs`, `contacts`, `interactions`, `tasks`, `pipeline_stages`, `drafts`, `audit_log`.

**Packs** change the vocabulary and prompts without touching the engine. A gig pack has stages like `prospect, pitched, held, booked, played`; a job pack has `saved, applied, screen, interview, offer`.

**Agent layer:** given the database state, the agent decides who needs a follow-up, researches the target, and writes a draft in the user's voice. Drafts land in an approval queue.

**Evals:** a fixed set of sample situations with checks for tone, accuracy against the stored record, and no invented facts. Run with `pytest`.

## Stack

- Python 3.12+
- SQLite (Postgres optional later)
- Standard-library HTTP to Anthropic, OpenRouter or any OpenAI-style server, and Ollama for local models (no SDKs)
- pytest
- Optional later: FastAPI + a small local web UI

## Roadmap

- [x] v0.1 (done): schema, migration runner, CLI (init, import, list, followups, log, stage, task, drafts, show, approve, reject, sent), gigs demo data
- [x] v0.2 agent drafting (done): provider-agnostic model interface (Anthropic, Ollama, fake for tests), guardrails, per-pack prompts, approval queue. 164 tests.
- [x] v0.2 eval harness (done): 15 scenarios, 8 mechanical checks, self-testing.
- [x] Ollama backend verified live against a local model (see results above)
- [x] Prompt-injection hardening (strip, remind, echo check) with a before/after ablation: detectable injections 20/25 to 0/25 obeyed on qwen2.5:7b
- [x] OpenRouter / OpenAI-compatible backend and `evals/run_matrix.sh` (tested against fake HTTP only, not yet against a real account)
- [ ] Anthropic backend: tested against fake HTTP only, not yet against a real account
- [ ] Comparison table with hosted models. Blocked on credits: the OpenRouter key authenticates but the account has never purchased credits (HTTP 402), and `:free` models were rate-limited or returned empty text. Next step: buy the minimum credit, run a one-scenario smoke test, then `evals/run_matrix.sh`.
- [ ] Stronger defense for paraphrased injections (evasive scenarios: 9/15 still obeyed)
- [ ] v0.3: jobs pack and freelance pack
- [ ] v0.4: local web UI
- [ ] Later: optional Salesforce export/sync via REST API (Developer Edition)

## Non-goals

- Mass or automated sending
- LinkedIn or other platform automation
- Team features, permissions, billing
- A hosted version

## License

AGPL-3.0 (see `LICENSE`). Free to use, modify, and self-host. Anyone who runs a modified version as a service must publish their changes.

## Origin

Grown out of building Otto, a closed-source AI real-estate automation platform, and from needing the same tooling to book gigs and run a job search without a team.
