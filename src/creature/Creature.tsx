import { computeCreatureState, type PersonaSnapshot } from './logic';

type CreatureProps = {
  persona?: PersonaSnapshot;
};

export function Creature({ persona }: CreatureProps) {
  const state = computeCreatureState(persona);

  return (
    <div className="creature-wrap" aria-label={state.ariaLabel}>
      <div className="creature-stage">
        <div
          className="creature"
          style={{
            transform: `translateY(${(100 - state.growth) * 0.12}px) rotate(${state.shape.tilt}deg) scale(${0.92 + state.growth / 180})`,
          }}
          aria-hidden="true"
        >
          <div
            className="creature-body"
            style={{
              width: `${state.shape.bodyWidth}px`,
              height: `${state.shape.bodyHeight}px`,
              transform: `rotate(${state.shape.lean}deg)`,
            }}
          >
            <div className="creature-eye eye-left" />
            <div className="creature-eye eye-right" />
            <div className="creature-mouth" />
            <div className="creature-pulse" />
          </div>
          <div className="creature-glow" style={{ opacity: 0.3 + state.growth / 140 }} />
        </div>
      </div>

      <div className="creature-metrics" aria-live="polite">
        <div>
          <span className="metric-label">Growth</span>
          <strong>{state.growth}%</strong>
        </div>
        <div>
          <span className="metric-label">Curiosity</span>
          <strong>{state.curiosity}</strong>
        </div>
        <div>
          <span className="metric-label">Signal</span>
          <strong>{state.domainsWithSignal}</strong>
        </div>
      </div>
    </div>
  );
}

export default Creature;
