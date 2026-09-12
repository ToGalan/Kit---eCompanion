# Kit

Kit is a cross-media companion for tracking and interrogating media relationships across games, film, TV, anime, and music. Games are a particularly rich signal source, but the product is not limited to games and is designed to support broader media comparison and discovery work.

**[PRINCIPLES.md](PRINCIPLES.md) governs this project.** It defines what Kit is
for, what it will not do, and which of those decisions are settled. Read it
before changing matching, storage, conversation or the creature. Clauses marked
`[locked]` are not revisable through implementation choices; clauses marked
`[open]` name work that is deliberately undecided. [VOICE.md](VOICE.md) is
downstream of it and is enforced at runtime by `mind/voice.py`.

This repository is a stubbed prototype and research scaffold, not a finished recommendation engine or production catalog integration. The browser signal layer captures domain-level hits and the app keeps an evidence-first reasoning loop, but the real provider integrations and model-backed matching are still planned rather than complete.

## Signal sources today vs planned

Today, the app can observe browser-domain hits with the existing allowlist, which includes platforms such as Netflix, Letterboxd, AniList, Trakt, Spotify, and game storefronts. That is a real signal input at the browser layer, but it is not yet a complete or reliable media graph. Existing provider integrations are still lightweight and not equivalent to full catalog ingestion.

Planned signal sources include richer metadata and review APIs for games, film, TV, anime, and music; deeper provider-backed matching; and model-driven structural comparison once the evaluation layer is implemented.

## Layout

- `kit/` — vault record and markdown-backed knowledge store
- `mind/` — reasoning, embedding, and Obsidian brain helpers
- `app/` — FastAPI backend and browser signal endpoints
- `src/` — Vite + React + TypeScript interface
- `tests/` — pytest and UI regression coverage

## Local development

```bash
python -m pytest -q
npm test -- --run
npm run dev -- --host 0.0.0.0
```

## Memory

Chat memory is the vault. Every exchange is written to
`vault/conversations/<session>.md`, and the durable statements inside it accumulate as
persona facts in `vault/persona/`, each carrying its layer (`elicited`, `observed`,
`inferred`), the evidence behind it and a computed confidence that rises as the evidence
does. There is no second store: what Kit recalls on the next turn is what the markdown
says, which is why the transcript is readable and editable in Obsidian like everything
else.

Two kinds of turn are never written anywhere, however useful they look: one expressing
serious distress (7.5) and one stating something about health, sexuality, religion,
politics or immigration status (6.6). Kit still answers; it just keeps no record.

The working vault and the signal databases hold real user data, so they stay out of git
(6.7). Set `KIT_VAULT_PATH`, `KIT_DB_PATH`, `KIT_TOUCHPOINT_DB_PATH` and
`KIT_METRICS_DB_PATH` to put them somewhere else; the test suite points all four at a
temporary directory so a run never touches the repository.

## Notes

The vault is markdown on disk and intentionally compatible with Obsidian. The app is designed to treat the Markdown files as the source of truth rather than as a database layer.

This is a stubbed prototype: some UI and backend scaffolding exists, but not all provider integrations, recommendation features, or inference pipelines are live yet.

## Embeddings

The semantic embedding layer uses sentence-transformers with the local model `all-MiniLM-L6-v2`. On first run, the model downloads to the local cache and is roughly 80-90 MB in size.
