from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kit.vault import Vault
from mind.ai import Opus5Gateway
from mind.elicitation import confirm_inference, generate_session_prompt
from mind.tools import DEFAULT_TOOLS

Intent = str


@dataclass
class TurnDecision:
    intent: Intent
    message: str
    state: dict[str, Any] | None = None


@dataclass
class ConversationState:
    user: str
    asked: list[str | dict[str, Any]] = field(default_factory=list)
    declined: list[str | dict[str, Any]] = field(default_factory=list)
    recommended: list[dict[str, Any]] = field(default_factory=list)
    pending_offer: dict[str, Any] | None = None
    elicitation_count: int = 0
    last_turns: list[dict[str, Any]] = field(default_factory=list)
    current_goal: str | None = None
    subject_changed: bool = False

    def remember_asked(self, question: str) -> None:
        normalized = _normalize_text(question)
        if not normalized:
            return
        self.asked.append(normalized)
        self.elicitation_count = max(self.elicitation_count, len(self.asked))

    def remember_declined(self, question: str) -> None:
        normalized = _normalize_text(question)
        if not normalized:
            return
        if normalized not in { _normalize_text(item) for item in self.declined }:
            self.declined.append(normalized)

    def remember_offer(self, offer: dict[str, Any]) -> None:
        if not isinstance(offer, dict):
            return
        self.pending_offer = dict(offer)
        self.recommended.append(dict(offer))

    def remember_turn(self, intent: str, *, user: str, text: str) -> None:
        payload = {
            "intent": intent,
            "user": user,
            "text": _normalize_text(text),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.last_turns.append(payload)
        if len(self.last_turns) > 12:
            self.last_turns = self.last_turns[-12:]

    def has_recent_question(self, question: str, *, days: int = 7) -> bool:
        normalized = _normalize_text(question)
        if not normalized:
            return False
        now = datetime.now(timezone.utc)
        for item in self.asked:
            candidate = _normalize_text(item)
            if candidate == normalized:
                return True
            if isinstance(item, dict):
                timestamp = item.get("timestamp")
                if isinstance(timestamp, str):
                    try:
                        seen = datetime.fromisoformat(timestamp)
                        if now - seen < timedelta(days=days):
                            return True
                    except ValueError:
                        pass
        return False


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _looks_like_url(text: str) -> bool:
    return bool(re.search(r"https?://\S+", _normalize_text(text), flags=re.IGNORECASE))


def _looks_like_title_question(text: str) -> bool:
    text = _normalize_text(text)
    if not text:
        return False
    if _looks_like_url(text):
        return True
    title_markers = [
        "what is",
        "who made",
        "when did",
        "where can i watch",
        "where can i find",
        "what album",
        "what game",
        "what movie",
        "what show",
        "what series",
        "what anime",
        "release date",
        "watched",
        "played",
        "title",
    ]
    lowered = text.lower()
    if "?" in text and any(marker in lowered for marker in title_markers):
        return True
    if re.search(r"\b(what|when|where|who|which)\b.*\b(movie|game|show|series|anime|album|song|title|release date|streaming|watch)\b", lowered):
        return True
    return False


def _looks_like_direct_question(text: str) -> bool:
    text = _normalize_text(text)
    if not text:
        return False
    if _looks_like_title_question(text):
        return True
    return "?" in text or bool(re.search(r"\b(what|when|where|why|who|how|which|should|can|could|do|does|did|is|are)\b", text, flags=re.IGNORECASE))


def _looks_like_rejection(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    markers = [
        "no thanks",
        "not really",
        "no,",
        "i don't want",
        "i do not want",
        "don't want",
        "do not want",
        "pass",
        "not for me",
        "not that",
        "no thanks",
        "decline",
        "skip",
    ]
    return any(marker in lowered for marker in markers)


def _looks_like_acceptance(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    return any(marker in lowered for marker in ["yes", "sure", "okay", "that works", "sounds good", "i like it", "i do like it"])


def _looks_like_decline_to_answer(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    return any(marker in lowered for marker in ["don't want to answer", "do not want to answer", "not answering", "won't answer", "decline to answer", "skip that question", "not answering that"])


def _looks_like_subject_change(text: str, *, occasion: dict[str, Any] | None = None) -> bool:
    lowered = _normalize_text(text).lower()
    if any(marker in lowered for marker in ["actually", "different", "switch", "not that", "other than", "something else", "another topic", "change topic"]):
        return True
    if occasion:
        domain = str(occasion.get("domain") or occasion.get("time_of_day") or "").lower()
        for keyword in ["music", "games", "film", "tv", "anime"]:
            if keyword in lowered and keyword != domain:
                return True
    return False


def _pick_offer(persona_snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = persona_snapshot or {}
    elicited = snapshot.get("elicited") or []
    if not elicited:
        return None
    first = elicited[0]
    title = str(first.get("title") or "Quiet Drift").strip() or "Quiet Drift"
    reason = str(first.get("content") or "calm low-stimulation recovery").strip() or "calm low-stimulation recovery"
    offer = {
        "title": title,
        "reason": reason,
        "wrong_if": "if you want a challenge loop or a loud social hit instead of recovery.",
        "confidence": 0.82,
    }
    return offer


def _specific_follow_up(offer: dict[str, Any] | None, *, user_input: str | None = None) -> str:
    wrong_if = str((offer or {}).get("wrong_if") or "").strip()
    if wrong_if:
        cleaned = wrong_if.strip(".")
        if cleaned.lower().startswith("if "):
            return f"I heard the mismatch. {cleaned}. What are you actually in the mood for instead?"
        return f"I heard the mismatch. {cleaned}. What do you want instead?"
    if user_input:
        return f"I heard that. What would fit better than {user_input[:40].strip()}?"
    return "I hear that. What would fit better instead?"


def _tool_schema_block() -> str:
    tools = []
    for name, tool in DEFAULT_TOOLS.items():
        schema = getattr(tool, "input_schema", {}) or {}
        tools.append({
            "name": name,
            "description": getattr(tool, "description", ""),
            "input_schema": schema,
        })
    return json.dumps(tools, ensure_ascii=False)


def _resolve_title_or_url_tools(text: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    url_match = re.search(r"https?://\S+", text, flags=re.IGNORECASE)
    if url_match:
        tool = DEFAULT_TOOLS.get("resolve_url")
        if tool is not None:
            results.append(tool.run(url=url_match.group(0)))
        return results

    query = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    query = re.sub(r"[^A-Za-z0-9\s'\-]", " ", query)
    query = " ".join(query.split())
    if len(query) < 3:
        return results

    for name in ["steam_catalog", "igdb_lookup", "tmdb_lookup", "anilist_lookup", "musicbrainz_lookup", "web_search"]:
        tool = DEFAULT_TOOLS.get(name)
        if tool is not None:
            try:
                results.append(tool.run(query=query))
            except Exception:
                results.append({"tool": name, "ok": False, "error": "tool execution failed", "data": {}})
    return results


def _summarize_tool_results(results: list[dict[str, Any]]) -> str:
    summarized: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if not item.get("ok"):
            continue
        data = item.get("data") or {}
        if isinstance(data, dict):
            if isinstance(data.get("tracks"), list):
                tracks = ", ".join(str(track) for track in data["tracks"][:5])
                summarized.append(f"{item.get('tool')}: {tracks}")
            elif data.get("title"):
                summarized.append(f"{item.get('tool')}: {data.get('title')}")
    return "; ".join(summarized)


def _direct_answer(user: str, text: str, *, persona_snapshot: dict[str, Any] | None = None, occasion: dict[str, Any] | None = None, state: ConversationState | None = None, gateway: Opus5Gateway | None = None) -> str:
    snapshot = persona_snapshot or {}
    occasion_payload = occasion or {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"}
    should_use_tools = _looks_like_title_question(text) or _looks_like_url(text)
    tool_results = _resolve_title_or_url_tools(text) if should_use_tools else []
    live_summary = _summarize_tool_results(tool_results)

    if should_use_tools:
        instructions = (
            "Answer the user directly from the current persona evidence and occasion context. "
            "Use the available tools for any question about a specific media title, work, release, platform, or URL. "
            "If a tool result succeeds, cite the title or tracks it resolved and note the source tool. "
            "If no tool succeeds, say that it could not reach that source right now. "
            "Do not use a training cutoff disclaimer or recall-based answer for a title claim.\n\n"
            f"User: {text}\n"
            f"Persona snapshot: {json.dumps(snapshot, ensure_ascii=False)}\n"
            f"Occasion: {json.dumps(occasion_payload, ensure_ascii=False)}\n"
            f"Tool schemas: {_tool_schema_block()}\n"
            f"Live results: {json.dumps(tool_results, ensure_ascii=False)}\n"
        )
        if gateway is not None:
            try:
                answer = gateway.generate(instructions, max_tokens=256)
                if _normalize_text(answer):
                    return answer.strip()
            except Exception:
                pass

        if live_summary:
            return f"I checked the live source and it resolved to: {live_summary}."

        if any(item.get("ok") is False for item in tool_results if isinstance(item, dict)):
            return "I could not reach that source right now."

        if any(isinstance(item, dict) and item.get("ok") is True for item in tool_results):
            return f"I checked the live source and it resolved to: {live_summary or 'the requested media.'}"

    lower_text = text.lower()
    context_bits = []
    if "listen" in lower_text:
        context_bits.append("listen")
    if "this evening" in lower_text:
        context_bits.append("this evening")
    if "today" in lower_text:
        context_bits.append("today")
    if not context_bits:
        context_bits.append("the current fit")

    if snapshot.get("elicited"):
        facts = "; ".join(
            str(item.get("title") or item.get("content") or "").strip()
            for item in snapshot.get("elicited", [])
            if isinstance(item, dict)
        )
        if facts:
            return (
                f"From the current signal, the strongest evidence is: {facts}. For the question about {', '.join(context_bits)}, "
                "I’d answer by grounding it in the actual fit and the specific function you need, not by ranking titles blindly."
            )
    return (
        f"I’m answering from the current record for {', '.join(context_bits)}: the useful move is to anchor the title, medium, and function "
        "before drawing a conclusion."
    )


def build_model_context(
    state: ConversationState,
    *,
    persona_snapshot: dict[str, Any] | None = None,
    occasion: dict[str, Any] | None = None,
    max_turns: int = 5,
) -> dict[str, Any]:
    recent_turns = (state.last_turns or [])[-max_turns:]
    return {
        "user": state.user,
        # None means the occasion is not known yet, which is a different state from a
        # weekday evening and must not be flattened into one.
        "occasion": occasion,
        "persona_snapshot": persona_snapshot or {},
        "session_state": {
            "asked": state.asked,
            "declined": state.declined,
            "recommended": state.recommended,
            "pending_offer": state.pending_offer,
            "elicitation_count": state.elicitation_count,
        },
        "recent_turns": recent_turns,
        "policy": {
            "max_turns_in_context": max_turns,
            "direct_question_priority": True,
            "recommendation_wait_for_response": True,
            "elicitation_cap": 3,
        },
    }


def decide_turn(
    user_message: str,
    *,
    user: str,
    state: ConversationState | None = None,
    persona_snapshot: dict[str, Any] | None = None,
    occasion: dict[str, Any] | None = None,
    vault: Vault | None = None,
    gateway: Opus5Gateway | None = None,
    max_turns: int = 5,
) -> TurnDecision:
    state = state or ConversationState(user=user)
    text = _normalize_text(user_message)
    if not text:
        return TurnDecision("ACKNOWLEDGE", "I’m listening.")

    if _looks_like_decline_to_answer(text):
        state.remember_declined(text)
        state.remember_turn("ACKNOWLEDGE", user=user, text=text)
        return TurnDecision("ACKNOWLEDGE", "Okay, no problem. I’ll leave that alone and move on.", state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if state.pending_offer and _looks_like_rejection(text):
        offer = state.pending_offer
        if vault is not None and offer.get("title"):
            confirm_inference(
                user,
                {
                    "title": str(offer.get("title") or "recommendation"),
                    "function": str(offer.get("reason") or "recommendation"),
                    "content": str(offer.get("reason") or "The user rejected this recommendation."),
                    "confidence": float(offer.get("confidence") or 0.8),
                },
                vault=vault,
                response="correct",
                correction=text,
            )
        state.pending_offer = None
        state.remember_turn("ASK", user=user, text=text)
        follow_up = _specific_follow_up(offer, user_input=text)
        return TurnDecision("ASK", follow_up, state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if _looks_like_direct_question(text):
        answer = _direct_answer(user, text, persona_snapshot=persona_snapshot, occasion=occasion, state=state, gateway=gateway)
        state.remember_turn("ANSWER", user=user, text=text)
        return TurnDecision("ANSWER", answer, state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if _looks_like_subject_change(text, occasion=occasion):
        state.current_goal = None
        state.subject_changed = True
        state.remember_turn("ACKNOWLEDGE", user=user, text=text)
        return TurnDecision("ACKNOWLEDGE", "I’m following your lead. Tell me the new subject and I’ll reason within that frame.", state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if state.pending_offer:
        state.pending_offer = None
        state.remember_turn("ACKNOWLEDGE", user=user, text=text)
        return TurnDecision("ACKNOWLEDGE", "I’m listening. Tell me what you want instead, and I’ll adapt to that.", state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if state.elicitation_count >= 3 and not _looks_like_subject_change(text, occasion=occasion):
        state.remember_turn("ACKNOWLEDGE", user=user, text=text)
        return TurnDecision("ACKNOWLEDGE", "I’m going to hold the question for now and keep this to the concrete signal at hand.", state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if _has_question_to_ask(state, text):
        state.remember_asked(text)
        state.remember_turn("ASK", user=user, text=text)
        prompt = generate_session_prompt(persona_snapshot, occasion=occasion)
        return TurnDecision("ASK", prompt, state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))

    if state.pending_offer is None:
        offer = _pick_offer(persona_snapshot)
        if offer is not None:
            state.remember_offer(offer)
            state.remember_turn("OFFER", user=user, text=text)
            return TurnDecision(
                "OFFER",
                f"I think {offer['title']} fits because {offer['reason']}. It would be wrong if {offer['wrong_if'].lstrip('if ')}",
                state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns),
            )

    state.remember_turn("ACKNOWLEDGE", user=user, text=text)
    return TurnDecision("ACKNOWLEDGE", "I’m tracking that. I’ll keep the conversation on the concrete signal instead of broadening it unnecessarily.", state=build_model_context(state, persona_snapshot=persona_snapshot, occasion=occasion, max_turns=max_turns))


def _has_question_to_ask(state: ConversationState, text: str) -> bool:
    question_text = _normalize_text(text)
    if not question_text:
        return False
    if "?" in question_text:
        return True
    if _looks_like_subject_change(question_text):
        return True
    if state.elicitation_count < 3:
        return True
    return False
