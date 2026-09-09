# Kit

Kit is a game-discovery companion built around the premise from the paper: in an oversupplied market, discovery fails when attention is fragmented, the wrong games are surfaced, and player-game fit is not treated as a structural problem. The app keeps an evidence-first reasoning loop around player intent, game signals, and discovery gaps.

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

## Notes

The vault is markdown on disk and intentionally compatible with Obsidian. The app is designed to treat the Markdown files as the source of truth rather than as a database layer.
