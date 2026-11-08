# Demo agent eval results

Run `20261108T170251Z`, effort `low`, fake PayPal, temp DB seeded with Maria's Cart. Raw transcripts and usage: `eval/agent_runs/20261108T170251Z/`. Estimated spend $0.28 (budget $2.00).

## Per model

| model | scenarios passed | mean cost / conversation | p50 turn latency | mean tool rounds |
|---|---|---|---|---|
| claude-haiku-5-5 | 3/3 | $0.0016 | 2.8s | 3.0 |
| claude-sonnet-5-5 | 3/3 | $0.0285 | 5.2s | 3.0 |
| claude-opus-5-5 | 3/3 | $0.0631 | 4.8s | 3.0 |

One run per model per scenario, so differences are anecdotal. Latency is wall time per user turn; tool rounds are tool calls per conversation. Cost counts cached input tokens at the base input price.

## Per scenario

| model | scenario | result | asked first | order total | cost | turn latencies | tool rounds |
|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | single-shot | pass | no | $4.50 | $0.0013 | 4.4s | 3 |
| claude-haiku-5-5 | multi-turn | pass | no | $9.25 | $0.0018 | 2.8s, 2.1s | 3 |
| claude-haiku-5-5 | robustness | pass | yes | $4.50 | $0.0018 | 4.2s, 2.0s | 3 |
| claude-sonnet-5-5 | single-shot | pass | no | $4.50 | $0.0247 | 5.7s | 3 |
| claude-sonnet-5-5 | multi-turn | pass | no | $9.25 | $0.0358 | 4.7s, 3.3s | 3 |
| claude-sonnet-5-5 | robustness | pass | no | $4.50 | $0.0251 | 7.2s | 3 |
| claude-opus-5-5 | single-shot | pass | no | $4.50 | $0.0495 | 5.9s | 3 |
| claude-opus-5-5 | multi-turn | pass | no | $9.25 | $0.0699 | 4.8s, 3.7s | 3 |
| claude-opus-5-5 | robustness | pass | yes | $4.50 | $0.0700 | 6.2s, 3.9s | 3 |

## Failure notes

None.

## How to reproduce

`env -u ANTHROPIC_BASE_URL uv run python -m eval.run_agent_eval --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5`
