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
  form, including recency, trending or engagement weighting.
- **Availability before ranking** (4.4). `mind/freshness.py` filters unreachable
  candidates out before scoring, not after.
- **Confidence is computed** (5.10). No literal confidence values in application
  code.
- **Creature never decays** (7.3). `src/creature/logic.ts` derives growth from
  tested occasions, domains with signal, and confirmed inferences. There is no
  time term and there must never be one. Absence is not punished.
- **Writes carry reasons** (6.3). Every vault write commits with a reason.
- **No user data in git** (6.7). Real vaults, signal stores and databases stay
  out of the repository.

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
npm run dev -- --host 0.0.0.0
```
