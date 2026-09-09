import { useEffect, useMemo, useState } from 'react';

type Route = 'Chat' | 'Brain';

type PickItem = {
  id: string;
  label: string;
  reason: string;
  wrongIf: string;
  probe: string;
};

type Message = {
  id: string;
  role: 'assistant' | 'user';
  text: string;
};

const seedPicks: PickItem[] = [
  {
    id: 'p1',
    label: 'Elicit the player fit',
    reason: 'Map the player’s tastes, friction, and thresholds so the agent can search for the right kind of game rather than the loudest one.',
    wrongIf: 'The player profile is still vague or could fit any genre.',
    probe: 'elicitation',
  },
  {
    id: 'p2',
    label: 'Check the discovery gap',
    reason: 'Measure where the market is oversupplied and where structural correction is creating a real mismatch between player intent and title availability.',
    wrongIf: 'The problem is treated as a generic recommendation task instead of a discovery-market issue.',
    probe: 'produce',
  },
  {
    id: 'p3',
    label: 'Test the match signal',
    reason: 'Check whether the same game is being framed as a fit or not fit for the same player axis before the model treats it as a contradiction.',
    wrongIf: 'The mismatch is not clearly tied to player-game fit or market structure.',
    probe: 'contradiction',
  },
];

const initialMessages: Message[] = [
  { id: 'm1', role: 'assistant', text: 'I’ll elicit the player and the game context first, then map the fit and the discovery gap.' },
  { id: 'm2', role: 'assistant', text: 'Tell me the game title, the player profile, and what signal you want to test.' },
];

function App() {
  const [route, setRoute] = useState<Route>('Chat');
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [record, setRecord] = useState<string[]>(['Game: The Matrix', 'Discovery question: what matches this player in an oversupplied market?']);
  const [query, setQuery] = useState('');
  const [backendStatus, setBackendStatus] = useState<'checking' | 'live' | 'offline'>('checking');

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/health', { signal: controller.signal })
      .then((response) => {
        if (response.ok) {
          setBackendStatus('live');
        } else {
          setBackendStatus('offline');
        }
      })
      .catch(() => {
        setBackendStatus('offline');
      });

    return () => controller.abort();
  }, []);

  const picks = useMemo(() => seedPicks, []);

  const handlePick = (pick: PickItem) => {
    const produced =
      pick.probe === 'elicitation'
        ? 'The AI is eliciting the player profile and narrowing the discovery signal to a concrete fit, friction, and title match.'
        : pick.probe === 'produce'
          ? 'The AI has produced a structural discovery claim for the vault: the market is being framed as a specific, testable player-game fit problem.'
          : 'The AI is checking whether the same match signal is being framed in opposite ways before it becomes a contradiction in discovery.';

    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: 'user', text: pick.label },
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: `Probe: ${pick.probe}. Reason: ${pick.reason} Wrong if: ${pick.wrongIf} Produced response: ${produced}`,
      },
    ]);
    setRecord((current) => [...current, pick.label]);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const value = query.trim();
    if (!value) return;

    const result = value.toLowerCase().includes('unknown')
      ? 'The game is not in the current record. I can still help by eliciting the player profile more precisely and then producing the discovery-fit claim for the vault.'
      : `The AI has produced a candidate discovery signal for this game: “${value}” is now framed as a testable player-game fit claim.`;

    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', text: value }, { id: crypto.randomUUID(), role: 'assistant', text: result }]);
    setRecord((current) => [...current, value]);
    setQuery('');
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <nav className="route-switcher" aria-label="Main routes">
          {(['Chat', 'Brain'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              className={route === tab ? 'route active' : 'route'}
              onClick={() => setRoute(tab)}
            >
              {tab}
            </button>
          ))}
        </nav>
        <span className={`status-pill ${backendStatus}`} aria-live="polite">
          {backendStatus === 'live' ? 'Backend live' : backendStatus === 'checking' ? 'Checking backend' : 'Backend offline'}
        </span>
      </header>

      {route === 'Chat' ? (
        <main className="chat-layout" aria-label="Chat route">
          <section className="transcript-panel">
            <div className="kit-graphic" aria-label="Kit interactive graphic">
              <div className="core-orbit">
                <div className="kit-core">Kit</div>
                <span className="orbit orbit-a">Elicit</span>
                <span className="orbit orbit-b">Produce</span>
                <span className="orbit orbit-c">Test</span>
              </div>
            </div>

            <div className="transcript" role="log" aria-live="polite">
              {messages.map((message) => (
                <div key={message.id} className={`message ${message.role}`}>
                  <span className="bubble">{message.text}</span>
                </div>
              ))}
            </div>

            <form className="composer" onSubmit={handleSubmit}>
              <input
                aria-label="Message input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Tell me the game, the player profile, and what discovery signal you want to test"
              />
              <button type="submit">Send</button>
            </form>
          </section>

          <aside className="pick-panel" aria-label="Suggested picks">
            {picks.map((pick) => (
              <button key={pick.id} type="button" className="pick-card" onClick={() => handlePick(pick)}>
                <strong>{pick.label}</strong>
                <span>{pick.reason}</span>
                <small>Wrong if: {pick.wrongIf}</small>
                <em>Probe: {pick.probe}</em>
              </button>
            ))}
          </aside>
        </main>
      ) : (
        <main className="brain-layout" aria-label="Brain route">
          <div className="brain-sim" aria-live="polite">
            <div className="map-node violet node-q">Question</div>
            <div className="map-node teal node-c">Claim</div>
            <div className="map-node amber node-contested">Contested</div>
            <div className="map-node rose node-contradiction">Contradiction</div>
            <div className="kit-map-core">Kit</div>
            <div className="edge edge-violet" />
            <div className="edge edge-teal" />
            <div className="edge edge-amber" />
            <div className="edge edge-rose" />
          </div>
          <div className="brain-detail">
            <h3>Obsidian brain map</h3>
            <ul>
              {record.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </main>
      )}
    </div>
  );
}

export default App;
