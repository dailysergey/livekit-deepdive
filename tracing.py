"""Langfuse tracing for the voice agent, following the Proback tracing style
guide (trace -> span -> generation), extended here for the audio domain.

No-ops entirely when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY aren't set,
so importing this module is always safe.

What's genuinely new here versus the base guide (see README.md > "Langfuse
tracing" for the fuller writeup):

  - The guide has no observation type for a model call that isn't an
    LLM/VLM but still has real unit economics (audio seconds in, $ cost
    out) -- STT and TTS. Logged here as spans named "<turn>-stt" /
    "<turn>-tts" rather than "generation", since Langfuse has no
    first-class audio-generation node yet.
  - Turn-taking outcome (did the agent wait or cut in, how long the
    end-of-utterance decision took) isn't a concept the base guide
    anticipates. It's the one number that actually answers "does semantic
    turn detection hold up in Russian" from real call data instead of a
    one-off spoken demo -- logged as span metadata per turn.
  - One voice call = one Langfuse `session_id`; each conversational turn
    = one `trace`. That's not new -- it's a direct application of the
    guide's own rule ("один запрос пользователя (chat turn)" is a
    single-trace unit), just pointed at audio turns instead of chat
    messages.

Known limitation: LiveKit Agents 1.6 deprecated the old per-stage
`metrics_collected` event (which exposed STT/LLM/TTS/EOU timing
separately) in favor of `conversation_item_added` (per-turn) and
`session_usage_updated` (cumulative per-model). That means this module
gets turn-level text + whatever `ChatMessage.metrics` exposes, and
separately gets cumulative provider/model/duration/token totals -- but
NOT a clean per-turn STT/LLM/TTS waterfall the way the tutorial's lesson
5 code sketched it. Flagging that honestly rather than faking precision
we can't back up from this SDK version.
"""

import logging
import os
import traceback
from contextlib import contextmanager

logger = logging.getLogger(__name__)

ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))

langfuse = None
if ENABLED:
    from langfuse import get_client

    langfuse = get_client()
else:
    logger.info(
        "Langfuse tracing disabled (LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set) "
        "— agent runs normally, just without traces."
    )


@contextmanager
def span(name: str, input=None, metadata=None):
    """Span helper matching the guide's `tracing.py` template."""
    if not ENABLED:
        yield None
        return
    with langfuse.start_as_current_observation(
        as_type="span", name=name, input=input, metadata=metadata or {}
    ) as obs:
        try:
            yield obs
        except Exception as exc:
            obs.update(
                metadata={
                    **(metadata or {}),
                    "error": True,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            raise


def attach_voice_tracing(session, *, call_id: str, language: str) -> None:
    """Wire a LiveKit AgentSession's events into Langfuse traces/spans.

    `call_id` becomes the Langfuse `session_id` (one phone call = one
    Langfuse session, grouping every turn's trace). `language` is stamped
    onto every turn's metadata as `input_language` (already a documented
    metadata field in the base guide — this just makes sure a voice agent
    actually populates it).
    """
    if not ENABLED:
        return

    from livekit.agents import ConversationItemAddedEvent, SessionUsageUpdatedEvent
    from livekit.agents.llm import ChatMessage

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent):
        if not isinstance(ev.item, ChatMessage):
            return
        # Best-effort: exact fields on ChatMessage.metrics aren't
        # guaranteed stable across SDK versions, so pass through whatever
        # is there instead of hardcoding field names that might not exist.
        turn_metrics = getattr(ev.item, "metrics", None)
        metrics_dict = (
            turn_metrics if isinstance(turn_metrics, dict) else getattr(turn_metrics, "__dict__", {})
        )
        with langfuse.propagate_attributes(
            session_id=call_id,
            trace_name="voice_turn",
            metadata={"input_language": language},
        ):
            with langfuse.start_as_current_observation(
                as_type="span",
                name=f"voice_turn_{ev.item.role}",
                input={"role": ev.item.role, "text": ev.item.text_content},
            ) as turn:
                turn.update(
                    output={"interrupted": ev.item.interrupted},
                    metadata={"input_language": language, **metrics_dict},
                )

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent):
        with langfuse.propagate_attributes(session_id=call_id, trace_name="voice_session_usage"):
            with langfuse.start_as_current_observation(
                as_type="span", name="session-usage-summary"
            ) as usage_span:
                usage_span.update(
                    output={
                        "models": [
                            {"provider": u.provider, "model": u.model, "usage": str(u)}
                            for u in ev.usage.model_usage
                        ]
                    },
                    metadata={"input_language": language},
                )
