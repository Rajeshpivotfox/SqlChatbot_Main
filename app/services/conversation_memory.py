# ──────────────────────────────────────────────────────────────────────────────
# CONVERSATION MEMORY — sliding window of Q→SQL turns per session.
#
# Enables follow-up questions like "now show only the top 5" by giving Claude
# the previous SQL to modify instead of generating from scratch.
#
# TOKEN IMPACT:
#   Each stored turn adds ~50-150 tokens to the NL→SQL prompt (depending on
#   SQL complexity). We cap at 6 turns to bound this cost. The NLToSQLEngine
#   further optimizes by only including full SQL for the last 3 turns — older
#   turns include just the question text.
#
# SESSION LIFECYCLE:
#   - Sessions auto-expire after 2 hours of inactivity
#   - _evict_expired() runs lazily on every get/add call
#   - Frontend can call clear_session() on "New Chat" button click
# ──────────────────────────────────────────────────────────────────────────────

import time
import structlog

logger = structlog.get_logger(__name__)

_SESSION_TTL = 7200   # 2 hours of inactivity before a session expires
_MAX_TURNS = 6        # Q→SQL pairs to keep — bounds token cost in prompt


class ConversationMemory:
    """In-memory per-session conversation history for contextual follow-ups."""

    def __init__(self, max_turns: int = _MAX_TURNS, session_ttl: int = _SESSION_TTL):
        self._max_turns = max_turns
        self._session_ttl = session_ttl
        # session_id → list of {"question": str, "sql": str, "row_count": int}
        self._sessions: dict[str, list[dict]] = {}
        self._last_access: dict[str, float] = {}

    def get_history(self, session_id: str) -> list[dict]:
        """Return conversation history for the session (oldest first).
        Caller (NLToSQLEngine) formats this into the prompt."""
        self._evict_expired()
        history = self._sessions.get(session_id, [])
        if history:
            self._last_access[session_id] = time.time()
        return history

    def add_turn(self, session_id: str, question: str, sql: str,
                 row_count: int = 0) -> None:
        """Append a completed Q→SQL turn. Keeps only the most recent N turns."""
        self._evict_expired()
        turns = self._sessions.setdefault(session_id, [])
        turns.append({"question": question, "sql": sql, "row_count": row_count})
        if len(turns) > self._max_turns:
            self._sessions[session_id] = turns[-self._max_turns:]
        self._last_access[session_id] = time.time()
        logger.info("memory_turn_added", session_id=session_id,
                    turns=len(self._sessions[session_id]))

    def clear_session(self, session_id: str) -> None:
        """Remove all history for a session (e.g. 'New Chat' button)."""
        self._sessions.pop(session_id, None)
        self._last_access.pop(session_id, None)
        logger.info("memory_session_cleared", session_id=session_id)

    def _evict_expired(self) -> None:
        """Lazy eviction: remove sessions inactive for > _session_ttl seconds."""
        now = time.time()
        expired = [sid for sid, t in self._last_access.items()
                   if now - t > self._session_ttl]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._last_access.pop(sid, None)
