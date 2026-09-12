import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import { computeCreatureState } from '../src/creature';

type ChatCall = { body: Record<string, unknown>; headers: Record<string, string> };

const chatCalls: ChatCall[] = [];
let recalledTurns: Array<{ speaker: string; text: string; timestamp: string }> = [];

function jsonResponse(payload: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) } as Response);
}

beforeEach(() => {
  chatCalls.length = 0;
  recalledTurns = [];
  localStorage.clear();

  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);

    if (url.startsWith('/api/auth/session')) {
      return jsonResponse({ user: 'kit-user', token: 'kit-user.token' });
    }
    if (url.startsWith('/api/health')) {
      return jsonResponse({ status: 'ok' });
    }
    if (url.startsWith('/api/vault/graph')) {
      return jsonResponse({ nodes: [], edges: [] });
    }
    if (url.startsWith('/api/chat/memory')) {
      return jsonResponse({ session_id: 'kit-user-2026-09-12', turns: recalledTurns, facts: [] });
    }
    if (url.startsWith('/api/chat')) {
      chatCalls.push({
        body: JSON.parse(String(init?.body ?? '{}')),
        headers: (init?.headers ?? {}) as Record<string, string>,
      });
      return jsonResponse({ ok: true, message: 'A 25-minute loop fits that.', session_id: 'kit-user-2026-09-12' });
    }
    return jsonResponse({});
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Kit UI', () => {
  it('resumes the recorded conversation on load', async () => {
    recalledTurns = [
      { speaker: 'user', text: 'I usually play something short on a weeknight.', timestamp: '2026-09-12T18:00:00+00:00' },
      { speaker: 'kit', text: 'Short runs, then.', timestamp: '2026-09-12T18:00:04+00:00' },
    ];

    render(<App />);

    expect(screen.getByRole('log')).toBeInTheDocument();
    expect(await screen.findByText('I usually play something short on a weeknight.')).toBeInTheDocument();
    expect(screen.getByText('Short runs, then.')).toBeInTheDocument();
  });

  it('sends a message to the live route and renders the answer', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(chatCalls).toHaveLength(0));

    const input = screen.getByLabelText(/message input/i);
    await user.type(input, 'What should I start tonight?');
    await user.keyboard('{Enter}');

    expect(await screen.findByText('A 25-minute loop fits that.')).toBeInTheDocument();
    expect(chatCalls).toHaveLength(1);
    expect(chatCalls[0].body.message).toBe('What should I start tonight?');
    // The vault is the record of the conversation, so the client does not post its own.
    expect(chatCalls[0].body.history).toBeUndefined();
    expect(chatCalls[0].headers.Authorization).toBe('Bearer kit-user.token');
  });

  it('pins later messages to the session it was given', async () => {
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByLabelText(/message input/i);
    await user.type(input, 'first');
    await user.keyboard('{Enter}');
    await screen.findByText('A 25-minute loop fits that.');

    await user.type(input, 'second');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(chatCalls).toHaveLength(2));
    expect(chatCalls[1].body.session_id).toBe('kit-user-2026-09-12');
  });

  it('switches to the Obsidian route and shows the vault map', async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByLabelText(/AI route/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Obsidian' }));
    expect(screen.getByLabelText(/Obsidian route/i)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /vault knowledge graph/i })).toBeInTheDocument();
    // 6.4: the export is reachable from the map, not buried.
    expect(screen.getByRole('link', { name: /export vault/i })).toBeInTheDocument();
  });

  it('keeps creature state stable without time-based decay', () => {
    const persona = {
      elicited: [{ title: 'The Matrix', content: 'compares signal across media', domain: 'games' }],
      active_hypotheses: [{ title: 'Function fit', function: 'fit', confidence: 0.8, domain: 'games' }],
      signals: [{ domain: 'games', signal: 'attention' }],
      inferred: [{ title: 'Cross-media fit', layer: 'inferred', domain: 'games' }],
    };

    const first = computeCreatureState(persona, { now: 1_000 });
    const second = computeCreatureState(persona, { now: 2_000_000 });

    expect(first.growth).toBe(second.growth);
    expect(first.curiosity).toBe(second.curiosity);
  });
});
