import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../src/App';
import { computeCreatureState } from '../src/creature';

describe('Kit UI', () => {
  it('renders chat by default and supports interaction', async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole('log')).toBeInTheDocument();
    expect(screen.getByText(/Tell me the title, the medium, and what you want to compare or test\./i)).toBeInTheDocument();

    const firstOption = screen.getByText(/Map the work/i);
    await user.click(firstOption);

    expect(screen.getByText(/Probe: elicitation\./i)).toBeInTheDocument();
  });

  it('switches to the Obsidian route and shows the vault map', async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByLabelText(/AI route/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Obsidian' }));
    expect(screen.getByLabelText(/Obsidian route/i)).toBeInTheDocument();
    expect(screen.getByText(/Kit \/ discovery/i)).toBeInTheDocument();
  });

  it('keyboard typing with unknown title still yields a reaction', async () => {
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByLabelText(/message input/i);
    await user.type(input, 'unknown title');
    await user.keyboard('{Enter}');

    expect(screen.getByText(/I don’t see that work in the current record yet\./i)).toBeInTheDocument();
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
