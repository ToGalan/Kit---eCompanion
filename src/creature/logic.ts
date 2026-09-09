export type Domain = 'music' | 'games' | 'film' | 'tv' | 'anime';

export type OccasionKey = {
  time_of_day?: string;
  day_type?: string;
  session_length?: string;
};

export type PersonaSnapshot = {
  elicited?: Array<{ title?: string; content?: string; layer?: string; domain?: string }>;
  active_hypotheses?: Array<{
    title?: string;
    function?: string;
    confidence?: number | string;
    occasion?: OccasionKey;
    domain?: string;
  }>;
  signals?: Array<{ domain?: string; signal?: string }>;
  inferred?: Array<{ title?: string; layer?: string; domain?: string }>;
};

export type CreatureState = {
  growth: number;
  curiosity: Domain;
  curiosities: Record<Domain, number>;
  occasionsTested: number;
  domainsWithSignal: number;
  confirmedInferences: number;
  shape: {
    bodyWidth: number;
    bodyHeight: number;
    lean: number;
    tilt: number;
  };
  ariaLabel: string;
};

const DOMAINS: Domain[] = ['music', 'games', 'film', 'tv', 'anime'];
const DOMAIN_KEYWORDS: Record<Domain, string[]> = {
  music: ['music', 'song', 'album', 'playlist', 'artist', 'spotify'],
  games: ['game', 'steam', 'rpg', 'quest', 'save', 'controller'],
  film: ['film', 'movie', 'cinema', 'screening', 'letterboxd'],
  tv: ['tv', 'series', 'show', 'episode', 'netflix'],
  anime: ['anime', 'manga', 'otaku', 'watchlist', 'anilist'],
};

function normalizeDomain(value?: string): Domain {
  const normalized = (value || '').trim().toLowerCase();
  if (normalized.includes('game')) return 'games';
  if (normalized.includes('movie') || normalized.includes('film')) return 'film';
  if (normalized.includes('show') || normalized.includes('tv') || normalized.includes('series')) return 'tv';
  if (normalized.includes('anime') || normalized.includes('manga')) return 'anime';
  return 'music';
}

function domainScoreForText(value: string): Partial<Record<Domain, number>> {
  const result: Partial<Record<Domain, number>> = {};

  for (const domain of DOMAINS) {
    const keywords = DOMAIN_KEYWORDS[domain];
    let score = 0;
    const lowered = value.toLowerCase();
    for (const keyword of keywords) {
      if (lowered.includes(keyword.toLowerCase())) score += 1;
    }
    if (score > 0) result[domain] = score;
  }

  return result;
}

function stableOccasionKey(occasion?: OccasionKey): string {
  if (!occasion) return '';
  return [occasion.time_of_day || 'any', occasion.day_type || 'any', occasion.session_length || 'any'].join(':');
}

export function computeCreatureState(
  persona: PersonaSnapshot = {},
  _options: { now?: number } = {},
): CreatureState {
  const elicited = persona.elicited || [];
  const hypotheses = persona.active_hypotheses || [];
  const signals = persona.signals || [];
  const inferred = persona.inferred || [];

  const uniqueSignalDomains = new Set<string>();
  for (const signal of signals as Array<{ domain?: string }>) {
    const domain = normalizeDomain(signal?.domain);
    if (domain) uniqueSignalDomains.add(domain);
  }

  const curiosityScores: Record<Domain, number> = {
    music: 0,
    games: 0,
    film: 0,
    tv: 0,
    anime: 0,
  };

  for (const fact of elicited) {
    const text = `${fact.title || ''} ${fact.content || ''}`;
    const score = domainScoreForText(text);
    for (const domain of DOMAINS) {
      curiosityScores[domain] += score[domain] || 0;
    }
  }

  for (const hypothesis of hypotheses) {
    const domain = normalizeDomain(hypothesis.domain || hypothesis.function || '');
    const confidence = Number(hypothesis.confidence ?? 0);
    if (confidence >= 0.6) {
      curiosityScores[domain] += 2;
    }
  }

  const domainsWithSignal = Array.from(uniqueSignalDomains).length || Math.max(1, Math.min(3, Object.values(curiosityScores).filter((score) => score > 0).length));
  const occasionsTested = new Set(
    hypotheses
      .filter((hypothesis) => {
        const confidence = Number(hypothesis.confidence ?? 0);
        return Boolean(hypothesis.occasion) && confidence >= 0.6;
      })
      .map((hypothesis) => stableOccasionKey(hypothesis.occasion)),
  ).size;

  const confirmedInferences = inferred.filter((item) => (item.layer || '').toLowerCase() === 'elicited').length;

  const growth = Math.max(0, Math.min(100, Math.round((occasionsTested * 24) + (domainsWithSignal * 18) + (confirmedInferences * 14))));

  const curiosity = DOMAINS.reduce((winner, domain) => {
    if (curiosityScores[domain] < curiosityScores[winner]) return domain;
    return winner;
  }, DOMAINS[0]);

  const bodyWidth = 92 + growth * 0.42;
  const bodyHeight = 88 + growth * 0.36;
  const lean = (curiosity === 'games' ? 12 : curiosity === 'music' ? -8 : curiosity === 'film' ? 5 : curiosity === 'anime' ? 10 : 0) * (0.75 + Math.min(1, growth / 100));
  const tilt = (growth / 100) * 12 - 3;

  const ariaLabel = `Creature state: growth ${growth} percent. Curiosity is in ${curiosity}. It has tested ${occasionsTested} occasions, ${domainsWithSignal} domains with signal, and ${confirmedInferences} confirmed inferences.`;

  return {
    growth,
    curiosity,
    curiosities: curiosityScores,
    occasionsTested,
    domainsWithSignal,
    confirmedInferences,
    shape: {
      bodyWidth,
      bodyHeight,
      lean,
      tilt,
    },
    ariaLabel,
  };
}
