"""Minimal Akapulu avatar agent for LiveKit Agent Console.

Run from this directory (with env loaded):

    python agent.py dev

Then open your LiveKit Cloud project → Agents → Launch Console, and talk
to the agent. The Akapulu worker publishes the face as the Avatar
participant.

Requires LIVEKIT_* (Cloud project with Inference) plus AKAPULU_*.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    TurnHandlingOptions,
    inference,
)
from livekit_akapulu import AvatarSession

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

_REQUIRED_ENV = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "AKAPULU_API_KEY",
    "AKAPULU_AVATAR_ID",
)

# Carson (male). Change if your avatar_id is a female face.
_CARTESIA_VOICE_ID = "86e30c1d-714b-4074-a1f2-1cb6b552fb49"
_EAGER_EOT_THRESHOLD = 0.5
_AVATAR_JOIN_TIMEOUT_SECS = 180.0
_AGENT_NAME = "akapulu-avatar"


def _require_env() -> None:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing credentials: "
            + ", ".join(missing)
            + f". Copy .env.example to {_ENV_PATH.name} and fill it in."
        )


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a helpful voice AI assistant for Akapulu Labs. "
                "Keep replies short and conversational. "
                "Do not use markdown, bullets, emoji, or stage directions."
            ),
        )


server = AgentServer()


@server.rtc_session(agent_name=_AGENT_NAME)
async def voice_bot(ctx: agents.JobContext) -> None:
    _require_env()

    session = AgentSession(
        stt=inference.STT(
            model="deepgram/flux-general-multi",
            language="multi",
            extra_kwargs={"eager_eot_threshold": _EAGER_EOT_THRESHOLD},
        ),
        llm=inference.LLM(model="openai/gpt-5.4-mini"),
        tts=inference.TTS(
            model="cartesia/sonic-3.6",
            voice=_CARTESIA_VOICE_ID,
            language="en",
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
        ),
    )

    avatar = AvatarSession(show_ai_label=True)
    await avatar.start(session, room=ctx.room)
    ctx.add_shutdown_callback(avatar.aclose)
    await session.start(room=ctx.room, agent=Assistant())
    # Mute Flux until call_is_ready so speech during load is not a turn.
    session.input.set_audio_enabled(False)
    await avatar.wait_for_join(timeout=_AVATAR_JOIN_TIMEOUT_SECS)
    await avatar.wait_until_call_ready(timeout=_AVATAR_JOIN_TIMEOUT_SECS)
    session.input.set_audio_enabled(True)
    await session.generate_reply(
        instructions="Greet the user briefly and offer to help.",
    )


if __name__ == "__main__":
    _require_env()
    agents.cli.run_app(server)
