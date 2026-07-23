"""Stage 2 — Russian, fully through LiveKit Inference (no extra API keys needed).

Swaps STT/TTS for providers with Russian support and switches the assistant's
language. Semantic turn detection (MultilingualModel) officially supports
Russian, so this is also the config to use for the RU turn-detection demo:
say an unfinished Russian phrase with a mid-sentence pause ("Мне нужно
узнать про... э-э...") and confirm the agent waits instead of cutting in.

Run: uv run stages/stage2_ru_livekit.py console
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, inference, room_io
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tracing

load_dotenv()


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
        llm="openai/gpt-4.1-mini",
        # ElevenLabs Multilingual TTS via LiveKit Inference — swap `voice`
        # for whichever ElevenLabs voice ID you prefer for Russian.
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
    logging.info("Stage 2: RU pipeline — deepgram/nova-3 + gpt-4.1-mini + elevenlabs, via LiveKit Inference")
    agents.cli.run_app(server)
