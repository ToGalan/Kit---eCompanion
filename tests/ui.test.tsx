import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../src/App';

describe('Kit UI', () => {
  it('renders chat by default and supports interaction', async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole('log')).toBeInTheDocument();
    expect(screen.getByText(/Tell me the game title, the player profile, and what signal you want to test\./i)).toBeInTheDocument();

    const firstOption = screen.getByText(/Elicit the player fit/i);
    await user.click(firstOption);

    expect(screen.getByText(/Probe: elicitation\./i)).toBeInTheDocument();
  });

  it('only simulates brain map while route is open', async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByLabelText(/Chat route/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Brain' }));
    expect(screen.getByLabelText(/Brain route/i)).toBeInTheDocument();
    expect(screen.getByText(/Obsidian brain map/i)).toBeInTheDocument();
  });

  it('keyboard typing with unknown title still yields a reaction', async () => {
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByLabelText(/message input/i);
    await user.type(input, 'unknown title');
    await user.keyboard('{Enter}');

    expect(screen.getByText(/The game is not in the current record/i)).toBeInTheDocument();
  });
});
