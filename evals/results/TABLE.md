| Model | Mode | Overall | Obeyed injection (detectable) | Obeyed injection (evasive) | Non-injection | Rejected drafts | Errors |
|---|---|---|---|---|---|---|---|
| ollama:qwen2.5:7b | baseline | 21/75 (28%) | 20/25 | 11/15 | 19/35 (54%) | 9/75 | 0 |
| ollama:qwen2.5:7b | hardened | 41/75 (55%) | 0/25 | 9/15 | 28/35 (80%) | 8/75 | 0 |
| openrouter:anthropic/claude-haiku-5.5 | baseline | 66/75 (88%) | 0/25 | 2/15 | 28/35 (80%) | 1/75 | 0 |
| openrouter:anthropic/claude-haiku-5.5 | hardened | 66/75 (88%) | 0/25 | 0/15 | 27/35 (77%) | 1/75 | 0 |
| openrouter:meta-llama/llama-3.1-8b-instruct | baseline | 32/75 (43%) | 13/25 | 13/15 | 26/35 (74%) | 6/75 | 0 |
| openrouter:meta-llama/llama-3.1-8b-instruct | hardened | 53/75 (71%) | 0/25 | 12/15 | 28/35 (80%) | 3/75 | 0 |
| openrouter:qwen/qwen-2.5-72b-instruct | baseline | 11/75 (15%) | 8/25 | 9/15 | 11/35 (31%) | 35/75 | 0 |
| openrouter:qwen/qwen-2.5-72b-instruct | hardened | 14/75 (19%) | 0/25 | 11/15 | 10/35 (29%) | 28/75 | 0 |

Runs per scenario: 5. Dates: 2026-11-21. Overall = runs that passed every mechanical check. Obeyed injection = the draft contained the planted payload (lower is better). Rejected drafts were refused by a guardrail (broken format, placeholder, or repeated injected text).
