"""Conversation memory, recorded in the vault.

Kit's memory is the vault, not a hidden context buffer. Every exchange is written to a
markdown transcript note, and the durable statements inside it accumulate as persona
facts carrying their layer, their evidence and a reason for the write. Persona is what
that accumulation adds up to; nothing here keeps a second private copy.

Three principles shape what this module refuses to do:

- 5.5 and 5.6: nothing here derives mood, emotional state, or a model of the person.
  Observed facts are counts of what happened, phrased as counts.
- 7.5: a turn expressing serious distress is not persona signal and is not filed at all.
- 6.6: a stated fact about health, sexuality, religion, politics or immigration status
  is never written as a persona attribute, however useful it looks.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kit.vault import Occasion, Vault, body_from_markdown, parse_evidence, parse_frontmatter

logger = logging.getLogger(__name__)

# How much one piece of evidence at a given layer establishes a fact. Confidence is the
# noisy-OR over independent observations: 1 - (1 - weight) ** n. A fact the user has
# stated once is not as settled as one they have stated three times, and Kit having
# watched a pattern is weaker evidence than the user describing it.
LAYER_EVIDENCE_WEIGHT = {"elicited": 0.6, "observed": 0.35, "inferred": 0.2}

# Certainty is never reached. More evidence approaches this and stops.
CONFIDENCE_CEILING = 0.95

# A domain needs repeat visits before it says anything. One visit is an anecdote.
MIN_OBSERVATIONS = 2

DEFAULT_TURN_LIMIT = 12
DEFAULT_FACT_LIMIT = 12

# Statements durable enough to be worth keeping: a habit, a preference, a pattern the
# person describes about themselves. Anything narrower is conversation, not persona.
_DURABLE_PATTERNS = (
    r"\bi (like|love|prefer|avoid|enjoy|hate|dislike)\b",
    r"\bi (listen to|watch|play|read|seek out|return to|keep coming back to)\b",
    r"\b(i'm into|i am into|i tend to|i usually|i mostly|i always|i never|i can't stand|i cannot stand)\b",
    r"\bi (bounced off|gave up on|finished|stuck with|binged)\b",
)

# 7.5. Deliberately phrase-level: a false positive costs one unfiled turn, which is the
# cheap direction to be wrong in.
_DISTRESS_PATTERNS = (
    r"\b(kill myself|killing myself|end my life|take my own life)\b",
    r"\b(want to die|wish i was dead|wish i were dead|better off dead)\b",
    r"\b(self[- ]harm|hurt myself|cutting myself)\b",
    r"\b(suicidal|suicide)\b",
    r"\b(i can'?t go on|no reason to live|nothing to live for)\b",
)

# 6.6. Stated directly, these are still not persona attributes.
_PROTECTED_PATTERNS = (
    r"\b(my (therapist|psychiatrist|diagnosis|medication|meds|illness|condition|disorder))\b",
    r"\b(i'?m|i am) (autistic|adhd|bipolar|depressed|anxious|disabled|in recovery|sober)\b",
    r"\b(i'?m|i am) (gay|queer|bisexual|bi|lesbian|trans|transgender|asexual|straight)\b",
    r"\b(my (religion|faith|church|mosque|synagogue|temple))\b",
    r"\b(i'?m|i am) (a )?(muslim|christian|jewish|jew|hindu|buddhist|atheist|catholic)\b",
    r"\b(i (vote|voted)|my (politics|party))\b",
    r"\b(i'?m|i am) (a )?(democrat|republican|socialist|conservative|liberal|leftist)\b",
    r"\b(my (visa|green card|citizenship|immigration status)|i'?m undocumented|i am undocumented)\b",
)

_SPEAKER_ROLES = {"user": "user", "kit": "assistant"}


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = _normalize(text).lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def confidence_from_evidence(count: int, *, layer: str) -> float:
    """Confidence in a fact given how much evidence stands behind it.

    Computed, never asserted (5.10). Independent observations combine as a noisy-OR, so
    confidence rises with accumulation and never reaches certainty.
    """
    weight = LAYER_EVIDENCE_WEIGHT.get(str(layer).strip().lower())
    if weight is None:
        raise ValueError(f"layer must be one of: {', '.join(sorted(LAYER_EVIDENCE_WEIGHT))}.")
    observations = max(0, int(count))
    if observations == 0:
        return 0.0
    combined = 1.0 - (1.0 - weight) ** observations
    return round(min(CONFIDENCE_CEILING, combined), 3)


def is_durable_statement(text: str) -> bool:
    """Whether a turn states something about the person worth keeping."""
    cleaned = _normalize(text)
    if len(cleaned.split()) < 4:
        return False
    return _matches(cleaned, _DURABLE_PATTERNS)


def screen_turn(text: str) -> str | None:
    """Return why this turn must not be filed, or None when it may be.

    Distress (7.5) and protected categories (6.6) are not stored at all: not as a
    persona fact, and not as transcript either, because the transcript is storage too.
    """
    cleaned = _normalize(text)
    if not cleaned:
        return "empty"
    if _matches(cleaned, _DISTRESS_PATTERNS):
        return "distress"
    if _matches(cleaned, _PROTECTED_PATTERNS):
        return "protected"
    return None


def session_id_for(user: str, *, now: datetime | None = None) -> str:
    """A stable session id for a user's day.

    Day-scoped rather than per-page-load: reloading the tab should not lose the thread,
    and a session that runs past midnight is not worth splitting.
    """
    moment = now or datetime.now(timezone.utc)
    handle = re.sub(r"[^A-Za-z0-9_-]+", "-", str(user or "kit-user").strip()) or "kit-user"
    return f"{handle}-{moment.date().isoformat()}"


def _fact_title(text: str) -> str:
    cleaned = _normalize(text).rstrip(".")
    if len(cleaned) <= 80:
        return cleaned
    return cleaned[:80].rsplit(" ", 1)[0].strip() or cleaned[:80].strip()


def _persona_fact_notes(vault: Vault, user: str | None = None) -> list[dict[str, Any]]:
    """Persona facts, scoped to whose they are.

    A note written before facts carried an owner belongs to the vault as a whole and
    stays visible; one stamped with a different user does not travel to this one.
    """
    facts: list[dict[str, Any]] = []
    folder = vault.path / "persona"
    if not folder.exists():
        return facts
    wanted = str(user).strip() if user else None
    for path in sorted(folder.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable note is a degraded read, not a failure
            logger.warning("could not read persona note %s", path, exc_info=True)
            continue
        meta = parse_frontmatter(content)
        owner = str(meta.get("user") or "").strip()
        if wanted is not None and owner and owner != wanted:
            continue
        facts.append(
            {
                "title": path.stem,
                "content": body_from_markdown(content),
                "layer": str(meta.get("layer") or "").strip().lower() or None,
                "source": meta.get("source"),
                "confidence": meta.get("confidence"),
                "updated": meta.get("updated"),
                "evidence": parse_evidence(meta.get("evidence")),
            }
        )
    return facts


def record_statement(
    vault: Vault,
    user: str,
    text: str,
    *,
    session_id: str,
) -> dict[str, Any] | None:
    """File a durable statement as an elicited persona fact, or corroborate an existing one.

    Repeating something the user already said does not duplicate the note; it adds
    evidence to it, which is what raises the computed confidence.
    """
    statement = _normalize(text)
    if not is_durable_statement(statement):
        return None
    if screen_turn(statement) is not None:
        return None

    title = _fact_title(statement)
    existing = vault.get_note(title, "persona")
    evidence: list[str] = []
    if existing is not None:
        evidence = parse_evidence(parse_frontmatter(existing["content"]).get("evidence"))
    if session_id not in evidence:
        evidence.append(session_id)

    confidence = confidence_from_evidence(len(evidence), layer="elicited")
    path = vault.write_persona_fact(
        title=title,
        content=statement,
        reason=f"{user}: stated in chat",
        layer="elicited",
        source=f"chat:{session_id}",
        confidence=confidence,
        evidence=evidence,
        user=user,
    )
    return {
        "title": title,
        "path": str(path),
        "layer": "elicited",
        "confidence": confidence,
        "evidence": list(evidence),
    }


def compile_observed_facts(vault: Vault, user: str, history: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Turn accumulated browser signal into observed persona facts.

    Counts only. What a person did is recordable; why they did it is not derivable from
    it (5.5), so nothing here reaches for a reason.
    """
    events = (history or {}).get("events") or []
    tallies: dict[str, dict[str, int]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        domain = _normalize(event.get("domain"))
        signal = _normalize(event.get("signal")).lower()
        if not domain:
            continue
        counts = tallies.setdefault(domain, {"save": 0, "bounce": 0, "circle": 0, "total": 0})
        counts["total"] += 1
        if signal in counts:
            counts[signal] += 1

    written: list[dict[str, Any]] = []
    for domain, counts in sorted(tallies.items()):
        if counts["total"] < MIN_OBSERVATIONS:
            continue
        confidence = confidence_from_evidence(counts["total"], layer="observed")
        title = f"Signal: {domain}"
        content = (
            f"Recorded {counts['total']} visits to {domain}: "
            f"{counts['save']} saved, {counts['bounce']} bounced, {counts['circle']} circled."
        )

        # Signal arrives event by event, and rewriting an unchanged tally on each one
        # would fill the vault history with commits that record nothing.
        existing = vault.get_note(title, "persona")
        if existing is not None and body_from_markdown(existing["content"]) == content:
            continue

        path = vault.write_persona_fact(
            title=title,
            content=content,
            reason=f"{user}: compiled from recorded signal",
            layer="observed",
            source="browser signal",
            confidence=confidence,
            evidence=[f"{domain}:{counts['total']} events"],
            user=user,
        )
        written.append({"title": title, "path": str(path), "layer": "observed", "confidence": confidence})
    return written


def record_exchange(
    vault: Vault,
    user: str,
    *,
    session_id: str,
    user_text: str,
    kit_text: str,
    occasion: Occasion | None = None,
) -> dict[str, Any]:
    """Write one exchange, and whatever it establishes, into the vault."""
    skip = screen_turn(user_text)
    if skip is not None:
        # Nothing is written. The answer still reaches the person; it just leaves no trace
        # in their model, which is the point of 7.5 and 6.6.
        return {"recorded": False, "reason": skip, "facts": []}

    transcript = vault.append_conversation_turns(
        session_id,
        [("user", user_text), ("kit", kit_text)],
        user=user,
        reason=f"{user}: chat exchange",
        occasion=occasion,
    )
    fact = record_statement(vault, user, user_text, session_id=session_id)
    return {
        "recorded": True,
        "reason": None,
        "transcript": str(transcript),
        "facts": [fact] if fact else [],
    }


def recall(
    vault: Vault,
    user: str,
    *,
    session_id: str,
    occasion: Occasion | None = None,
    turn_limit: int = DEFAULT_TURN_LIMIT,
    fact_limit: int = DEFAULT_FACT_LIMIT,
) -> dict[str, Any]:
    """Everything the vault holds that belongs in this turn's context."""
    turns: list[dict[str, Any]] = []
    for entry in vault.conversation_turns(session_id, limit=turn_limit):
        turns.append({**entry, "session_id": session_id})

    # Memory that stops at the session boundary is not memory. Earlier sessions fill the
    # remaining room, most recent first, and the whole lot is replayed in order.
    if len(turns) < turn_limit:
        for session in vault.conversation_sessions(user=user):
            if session["session_id"] == session_id:
                continue
            remaining = turn_limit - len(turns)
            if remaining <= 0:
                break
            earlier = vault.conversation_turns(session["session_id"], limit=remaining)
            turns = [{**entry, "session_id": session["session_id"]} for entry in earlier] + turns

    facts = _persona_fact_notes(vault, user)
    facts.sort(key=lambda item: (-float(item.get("confidence") or 0.0), item["title"].lower()))

    hypotheses: list[dict[str, Any]] = []
    if occasion is not None:
        hypotheses = vault.persona_snapshot(occasion).get("active_hypotheses", [])

    return {
        "session_id": session_id,
        "user": user,
        "turns": turns,
        "facts": facts[: max(0, int(fact_limit))],
        "hypotheses": hypotheses,
        "occasion": occasion.as_dict() if occasion is not None else None,
    }


def conversation_messages(recalled: dict[str, Any]) -> list[dict[str, str]]:
    """Recalled turns as API messages, so prior answers are Kit's own turns."""
    messages: list[dict[str, str]] = []
    for turn in recalled.get("turns") or []:
        role = _SPEAKER_ROLES.get(str(turn.get("speaker") or "").lower())
        text = _normalize(turn.get("text"))
        if role is None or not text:
            continue
        messages.append({"role": role, "content": text})
    return messages


def memory_block(recalled: dict[str, Any]) -> str:
    """The recalled vault state, rendered for the system prompt.

    Layers travel with the facts (5.1) so the model can tell what the person said from
    what Kit watched, and the difference between nothing recorded and nothing tested
    (5.3) is stated rather than left to be guessed.
    """
    lines = ["What the vault holds for this person. Use it; do not announce that you remember it."]

    occasion = recalled.get("occasion")
    if occasion:
        described = ", ".join(
            str(occasion.get(field))
            for field in ("time_of_day", "day_type", "session_length")
            if occasion.get(field)
        )
        # 5.8: derived context says it is derived.
        lines.append(f"Occasion now, derived from timestamps rather than stated: {described}.")
    else:
        lines.append("Occasion now: not known.")

    facts = recalled.get("facts") or []
    if facts:
        lines.append("Recorded facts, with the layer each came from:")
        for fact in facts:
            layer = fact.get("layer") or "unlabelled"
            confidence = fact.get("confidence")
            suffix = f" (confidence {confidence})" if confidence not in (None, "") else ""
            content = _normalize(fact.get("content")) or _normalize(fact.get("title"))
            lines.append(f"- [{layer}] {content}{suffix}")
    else:
        lines.append("Nothing recorded about this person yet. That is untested, not a weak signal.")

    hypotheses = recalled.get("hypotheses") or []
    if hypotheses:
        lines.append("Hypotheses live for this occasion:")
        for item in hypotheses:
            function = _normalize(item.get("function")) or _normalize(item.get("title"))
            lines.append(f"- [inferred, unconfirmed] {function} (confidence {item.get('confidence')})")
    elif occasion:
        lines.append("Nothing tested for this occasion yet.")

    lines.append(
        "Memory does work rather than being displayed: draw on it to answer better, never to say "
        "that you remember. Where it matters, distinguish what the person told you from what you "
        "observed. Do not treat an observation as something they said."
    )
    return "\n".join(lines)


def default_vault() -> Vault:
    """The vault Kit writes to, overridable so a deployment can keep it off the repo."""
    return Vault(Path(os.environ.get("KIT_VAULT_PATH", "vault")))
