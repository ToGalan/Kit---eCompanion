# Working on Kit

Read [PRINCIPLES.md](PRINCIPLES.md) before changing behaviour. It is the
governing document. [VOICE.md](VOICE.md) is downstream of it and is parsed at
runtime by `mind/voice.py`, so edits there change the product, not just the docs.

Clauses marked `[locked]` in PRINCIPLES.md are settled. Do not weaken one to make
an implementation simpler, a test pass, or a metric move. If a locked clause
genuinely blocks the work, say so and stop rather than routing around it.

## Invariants that live in code

These are the places where a principle is enforced. Changing them without
changing PRINCIPLES.md first is a defect.

- **Three layers** (5.1). `kit/vault.py` rejects any persona fact whose layer is
  not `elicited`, `observed` or `inferred`. The layer travels with the fact.
- **Function plus occasion** (5.2). A hypothesis without an occasion is not
  writable. Do not add a default occasion to make a write succeed.
- **Popularity penalty** (4.6). `mind/matching.py` subtracts a popularity term
  when computing confidence. Nothing downstream may add popularity back, in any
  form, including recency, trending or engagement weighting. NOTE: the base score
  it subtracts from is unbounded, so the penalty is currently swamped on any
  persona with several facts. Fixing that is required, not optional.
- **Availability before ranking** (4.4). `mind/freshness.py` defines
  `availability_ok` and `verify_candidate`. NOTE: nothing calls either of them.
  Clauses 4.4 and 4.5 are unenforced today. Wire them ahead of `match()`.
- **Confidence is computed** (5.10). No literal confidence values in application
  code.
- **Creature never decays** (7.3). `src/creature/logic.ts` derives growth from
  tested occasions, domains with signal, and confirmed inferences. There is no
  time term and there must never be one. Absence is not punished.
- **Writes carry reasons** (6.3). Every vault write commits with a reason.
- **No user data in git** (6.7). Real vaults, signal stores and databases stay
  out of the repository. `vault/` and `data/` are ignored, and `tests/conftest.py`
  points every store at a temporary directory so a test run cannot write into the
  working tree.
- **Memory is the vault** (6.1). `mind/memory.py` records every exchange to
  `vault/conversations/` and compiles persona facts out of it. There is no second
  store and no hidden context buffer: if it is not in the markdown, Kit does not
  know it on the next turn.
- **Nothing about distress or a protected category is filed** (7.5, 6.6).
  `mind/memory.py:screen_turn` refuses the write — the persona fact and the
  transcript both, because a transcript is storage too. Do not add an exception
  for a turn that looks useful.
- **Confidence accumulates, it is not asserted** (5.10).
  `mind/memory.py:confidence_from_evidence` combines evidence as a noisy-OR and
  never reaches certainty. NOTE: `app/touchpoint.py` and `mind/elicitation.py`
  still write literal confidences (0.8, 0.85, 0.9, 0.6). Those are defects; route
  them through the computed path rather than adding more.
- **No sampling parameters on the gateway.** `mind/ai.py:SAMPLING_PARAMETERS` names
  what Opus 5 rejects with a 400. A `temperature` in the payload fails every
  request, and the chat route reports that failure to the user as "the live AI is
  unavailable". Tune depth with effort instead.

## Things that look like improvements and are not

- Inferring mood, energy or emotional state from playtime, click patterns or
  session length. Banned by 5.5. Ask instead, and file the answer as `elicited`.
- Personality types, clinical framings, or any model of the person rather than of
  their media use. Banned by 5.6.
- Padding a recommendation list to a fixed count. An honest empty is correct
  output under 8.2.
- Hedging an unverified title instead of dropping it. Banned by 4.5.
- Streaks, decay, loss framing, or re-engagement notifications. Banned by 7.2.

## Commands

```bash
python -m pytest -q
npm test -- --run
npm run dev                  # backend on 8002 and frontend on 5175, together
```

`npm run dev` needs Python 3.12+ and starts both halves; `dev:api` and `dev:web` run
them separately. Ports live in `KIT_API_PORT` / `KIT_WEB_PORT`, read by both
`vite.config.ts` and `scripts/dev.mjs` — do not hardcode a port in one of them.
