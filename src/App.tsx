import { forceCenter, forceLink, forceManyBody, forceSimulation } from 'd3-force';
import { useEffect, useMemo, useState } from 'react';
import Creature from './creature';

type Route = 'AI' | 'Obsidian';

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

type GraphNode = {
  id: string;
  kind: string;
  type: string;
  label: string;
  body: string;
  domain: string;
  confidence: number;
  status: string;
  layer: string;
  path: string;
  color: string;
  size?: number;
  opacity?: number;
  x?: number;
  y?: number;
  occasion?: Record<string, string> | null;
};

type GraphEdge = {
  source: string;
  target: string;
  kind: string;
};

type VaultGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  filters?: {
    domains?: string[];
    occasions?: string[];
  };
};

type NoteDetail = {
  title: string;
  kind: string;
  path: string;
  body: string;
  frontmatter: Record<string, string>;
  history: Array<{ reason: string; timestamp: string; commit?: string }>;
  layer: string;
  confidence: number;
};

const seedPicks: PickItem[] = [];

const initialMessages: Message[] = [];

function App() {
  const [route, setRoute] = useState<Route>('AI');
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [query, setQuery] = useState('');
  const [backendStatus, setBackendStatus] = useState<'checking' | 'live' | 'offline'>('checking');
  const [graph, setGraph] = useState<VaultGraph>({ nodes: [], edges: [] });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [note, setNote] = useState<NoteDetail | null>(null);
  const [domainFilter, setDomainFilter] = useState<string>('all');
  const [occasionFilter, setOccasionFilter] = useState<string>('all');
  const [deletePreview, setDeletePreview] = useState<Array<{ kind: string; title: string; path: string }> | null>(null);
  const [isMobile, setIsMobile] = useState<boolean>(() => window.innerWidth <= 380);

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

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/vault/graph', { signal: controller.signal })
      .then((response) => response.json())
      .then((data: VaultGraph) => {
        setGraph(data);
        if (data.nodes.length > 0 && !selectedNodeId) {
          setSelectedNodeId(data.nodes[0].id);
        }
      })
      .catch(() => {
        setGraph({ nodes: [], edges: [] });
      });

    return () => controller.abort();
  }, []);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 380);
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!selectedNodeId) return;
    const node = graph.nodes.find((item) => item.id === selectedNodeId);
    if (!node) return;
    const params = new URLSearchParams({ kind: node.kind, title: node.label });
    fetch(`/api/vault/note?${params.toString()}`)
      .then((response) => response.ok ? response.json() : null)
      .then((payload: NoteDetail | null) => {
        if (payload) {
          setNote(payload);
        }
      })
      .catch(() => {
        setNote(null);
      });
  }, [selectedNodeId, graph.nodes]);

  const picks = useMemo(() => seedPicks, []);
  const persona = useMemo(
    () => ({
      elicited: [
        { title: 'The Matrix', content: 'The user wants function-fit comparison across media', domain: 'games' },
        { title: 'Blade Runner 2049', content: 'The user is testing emotional signal and narrative fit', domain: 'film' },
      ],
      active_hypotheses: [
        { title: 'Signal fit', function: 'compare function-fit', confidence: 0.8, domain: 'games' },
        { title: 'Cross-media tension', function: 'test contradiction', confidence: 0.72, domain: 'film' },
      ],
      signals: [{ domain: 'games', signal: 'fit' }, { domain: 'film', signal: 'tension' }],
      inferred: [{ title: 'Cross-media comparison', layer: 'inferred', domain: 'film' }],
    }),
    [],
  );

  const handledFilters = useMemo(() => {
    const domains = ['all', ...(graph.filters?.domains ?? [])];
    const occasions = ['all', ...(graph.filters?.occasions ?? [])];
    return { domains, occasions };
  }, [graph.filters]);

  const visibleNodes = useMemo(() => {
    return graph.nodes.filter((node) => {
      const matchesDomain = domainFilter === 'all' || node.domain === domainFilter;
      const matchesOccasion = occasionFilter === 'all' || (node.occasion && JSON.stringify(node.occasion).includes(occasionFilter));
      return matchesDomain && matchesOccasion;
    });
  }, [graph.nodes, domainFilter, occasionFilter]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);

  const visibleEdges = useMemo(
    () => graph.edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)),
    [graph.edges, visibleNodeIds],
  );

  const graphLayout = useMemo(() => {
    if (visibleNodes.length === 0 || isMobile) {
      return { nodes: visibleNodes, edges: visibleEdges };
    }

    const width = 850;
    const height = 520;
    const nodes = visibleNodes.map((node) => ({ ...node, x: width / 2 + (Math.random() - 0.5) * 120, y: height / 2 + (Math.random() - 0.5) * 90 }));
    const edges = visibleEdges.map((edge) => ({ ...edge, id: `${edge.source}-${edge.target}-${edge.kind}` }));
    const simulation = forceSimulation(nodes)
      .force('link', forceLink(edges).id((d: any) => d.id).distance(95).strength(0.4))
      .force('charge', forceManyBody().strength(-150))
      .force('center', forceCenter(width / 2, height / 2))
      .stop();

    for (let tick = 0; tick < 180; tick += 1) {
      simulation.tick();
    }

    return { nodes, edges };
  }, [visibleEdges, visibleNodes, isMobile]);

  const handlePick = (pick: PickItem) => {
    const produced =
      pick.probe === 'elicitation'
        ? 'I’m grounding the work in the title, the medium, and the specific comparison context before I assess fit or mismatch.'
        : pick.probe === 'produce'
          ? 'I’ve framed the comparison as a concrete evidence question: what signal matters, what is missing, and what could explain the fit.'
          : 'I’m checking whether the same signal is being described in conflicting ways before treating it as a contradiction.';

    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: 'user', text: pick.label },
      {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: `Probe: ${pick.probe}. Reason: ${pick.reason} Wrong if: ${pick.wrongIf} Produced response: ${produced}`,
      },
    ]);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const value = query.trim();
    if (!value) return;

    const result = value.toLowerCase().includes('unknown')
      ? 'I don’t see that work in the current record yet. I can help by tightening the medium and comparison question before I build a claim.'
      : `I’ve turned “${value}” into a concrete comparison question: what does the user need, what is the signal, and what evidence would confirm the fit?`;

    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', text: value }, { id: crypto.randomUUID(), role: 'assistant', text: result }]);
    setQuery('');
  };

  const saveNote = async () => {
    if (!note) return;
    const payload = { kind: note.kind, title: note.title, content: note.body };
    await fetch('/api/vault/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  };

  const rejectInference = async () => {
    if (!note) return;
    const deleteResponse = await fetch('/api/vault/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: note.kind, title: note.title, preview: true }),
    });
    const payload = await deleteResponse.json();
    if (payload.status === 'preview') {
      setDeletePreview(payload.removed);
    }
  };

  const confirmReject = async () => {
    if (!note) return;
    await fetch('/api/vault/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: note.kind, title: note.title, preview: false }),
    });
    setDeletePreview(null);
    setSelectedNodeId(null);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <nav className="route-switcher" aria-label="Main routes">
          {(['AI', 'Obsidian'] as const).map((tab) => (
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

      {route === 'AI' ? (
        <main className="chat-layout" aria-label="AI route">
          <section className="transcript-panel">
            <div className="kit-graphic" aria-label="Kit interactive graphic">
              <Creature persona={persona} />
            </div>

            <div className="transcript" role="log" aria-live="polite">
              {messages.length === 0 ? (
                <div className="message assistant">
                  <span className="bubble">Start with what you want to compare or understand.</span>
                </div>
              ) : (
                messages.map((message) => (
                  <div key={message.id} className={`message ${message.role}`}>
                    <span className="bubble">{message.text}</span>
                  </div>
                ))
              )}
            </div>

            <form className="composer" onSubmit={handleSubmit}>
              <input
                aria-label="Message input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Tell me the title, the medium, and what you want to compare or test"
              />
              <button type="submit">Send</button>
            </form>
          </section>

          {picks.length > 0 ? (
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
          ) : null}
        </main>
      ) : (
        <main className="brain-layout" aria-label="Obsidian route">
          <section className="vault-graph-panel">
            <div className="vault-toolbar">
              <label>
                Domain
                <select value={domainFilter} onChange={(event) => setDomainFilter(event.target.value)}>
                  {handledFilters.domains.map((domain) => (
                    <option key={domain} value={domain}>{domain === 'all' ? 'All domains' : domain}</option>
                  ))}
                </select>
              </label>
              <label>
                Occasion
                <select value={occasionFilter} onChange={(event) => setOccasionFilter(event.target.value)}>
                  {handledFilters.occasions.map((occasion) => (
                    <option key={occasion} value={occasion}>{occasion === 'all' ? 'All occasions' : occasion}</option>
                  ))}
                </select>
              </label>
              <a className="export-button" href="/api/vault/export" target="_blank" rel="noreferrer">
                Export vault
              </a>
            </div>

            {isMobile ? (
              <div className="graph-list">
                {visibleNodes.map((node) => (
                  <button
                    key={node.id}
                    type="button"
                    className={`graph-node-list ${node.layer === 'inferred' ? 'layer-inferred' : ''}`}
                    onClick={() => setSelectedNodeId(node.id)}
                    style={{ borderColor: node.color, opacity: node.opacity ?? 1 }}
                  >
                    <span className="mini-badge">{node.type}</span>
                    <strong>{node.label}</strong>
                    <small>{node.domain} · {node.layer}</small>
                  </button>
                ))}
              </div>
            ) : (
              <svg className="graph-svg" viewBox="0 0 850 520" role="img" aria-label="Vault knowledge graph">
                {graphLayout.edges.map((edge) => {
                  const source = graphLayout.nodes.find((node) => node.id === edge.source);
                  const target = graphLayout.nodes.find((node) => node.id === edge.target);
                  if (!source || !target) return null;
                  return (
                    <line
                      key={`${edge.source}-${edge.target}-${edge.kind}`}
                      x1={source.x ?? 0}
                      y1={source.y ?? 0}
                      x2={target.x ?? 0}
                      y2={target.y ?? 0}
                      stroke="rgba(148, 163, 184, 0.5)"
                      strokeWidth={edge.kind === 'wikilink' ? 1.2 : 1}
                    />
                  );
                })}

                {graphLayout.nodes.map((node) => (
                  <g
                    key={node.id}
                    className={selectedNodeId === node.id ? 'graph-node selected' : 'graph-node'}
                    transform={`translate(${node.x ?? 0}, ${node.y ?? 0})`}
                    onClick={() => setSelectedNodeId(node.id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <circle
                      r={Math.max(12, node.size ?? 18)}
                      fill={node.color}
                      fillOpacity={node.opacity ?? 0.85}
                      stroke={node.layer === 'inferred' ? '#fbbf24' : node.layer === 'elicited' ? '#2dd4bf' : '#a78bfa'}
                      strokeWidth={node.layer === 'observed' ? 2 : 1.4}
                      strokeDasharray={node.status === 'untested' ? '4 3' : undefined}
                    />
                    <text y={4} textAnchor="middle" fontSize="10" fill="#e2e8f0">
                      {node.label.slice(0, 10)}
                    </text>
                  </g>
                ))}
              </svg>
            )}
          </section>

          <aside className="brain-detail">
            <div className="panel-header">
              <h3>Vault note</h3>
              {note && note.layer === 'inferred' ? (
                <button type="button" className="delete-button" onClick={rejectInference}>Reject inference</button>
              ) : null}
            </div>

            {deletePreview && (
              <div className="delete-preview">
                <p>This inference is tied to other vault items and will remove:</p>
                <ul>
                  {deletePreview.map((item) => (
                    <li key={`${item.kind}:${item.title}`}>{item.title}</li>
                  ))}
                </ul>
                <div className="delete-actions">
                  <button type="button" onClick={confirmReject}>Confirm</button>
                  <button type="button" className="ghost" onClick={() => setDeletePreview(null)}>Cancel</button>
                </div>
              </div>
            )}

            {note ? (
              <>
                <div className="note-meta">
                  <span>{note.kind}</span>
                  <span>{note.layer}</span>
                  <span>{note.confidence.toFixed(2)} confidence</span>
                </div>

                <textarea
                  value={note.body}
                  onChange={(event) => setNote((current) => current ? { ...current, body: event.target.value } : current)}
                />

                <div className="note-actions">
                  <button type="button" onClick={saveNote}>Save</button>
                </div>

                <div className="history-block">
                  <h4>Revision history</h4>
                  <ul>
                    {note.history.map((item) => (
                      <li key={`${item.commit ?? item.reason}-${item.timestamp}`}>
                        <strong>{item.reason}</strong>
                        <small>{new Date(item.timestamp).toLocaleString()}</small>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            ) : (
              <p className="empty-state">Select a node to inspect its vault note.</p>
            )}
          </aside>
        </main>
      )}
    </div>
  );
}

export default App;
