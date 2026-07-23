"""Stage 3 — Russian, LLM swapped for an open-weight model via OpenRouter.

STT/TTS stay on LiveKit Inference (same as stage 2); only the LLM hop moves
to OpenRouter, calling an open-weight model. That's the part that matters
for an on-prem path: an open-weight model reachable through OpenRouter today
can later be pointed at a self-hosted vLLM/TGI endpoint with the same
OpenAI-compatible client, without touching the rest of the pipeline.

Requires OPENROUTER_API_KEY in .env (not present by default — add it before
running this stage). Get one at https://openrouter.ai/keys.

Model default is "qwen/qwen-2.5-72b-instruct" — strong Russian support and
Apache-licensed open weights. Override via OPENROUTER_MODEL if the exact
slug has moved on openrouter.ai/models by the time you run this.

Run: uv run stages/stage3_ru_openrouter.py console
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, inference, room_io
from livekit.plugins import noise_cancellation, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tracing

load_dotenv()

OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "Ты — полезный голосовой AI-ассистент. Отвечай по-русски, "
                "короткими фразами, не более 3 предложений за раз."
            ),
        )


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="ru"),
        llm=openai.LLM.with_openrouter(model=OPENROUTER_MODEL),
        tts=inference.TTS(
            model="elevenlabs/eleven_turbo_v2_5",
            voice="Xb7hH8MSUJpSbSDYk0k2",
            language="ru",
        ),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )
    tracing.attach_voice_tracing(session, call_id=ctx.room.name, language="ru")

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not os.getenv("OPENROUTER_API_KEY"):
        sys.exit(
            "OPENROUTER_API_KEY не задан в .env — получите ключ на "
            "https://openrouter.ai/keys и добавьте его перед запуском stage 3."
        )
    logging.info(f"Stage 3: RU pipeline — deepgram/nova-3 + {OPENROUTER_MODEL} (OpenRouter) + elevenlabs")
    agents.cli.run_app(server)
