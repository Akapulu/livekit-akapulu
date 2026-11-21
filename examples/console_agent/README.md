# Agent Console example

Minimal LiveKit Agents worker that attaches [`AvatarSession`](../../README.md) and is meant for the [LiveKit Agent Console](https://docs.livekit.io/testing/agent-console/).

This is not a browser app. You run the worker locally (or wherever you host agents), then talk to it from Console.

## Setup

```bash
cd examples/console_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env`:

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` from your LiveKit Cloud project (Inference must be enabled; this agent uses LiveKit Inference for Flux / GPT / Cartesia).
- `AKAPULU_API_URL` (usually `https://akapulu.com`)
- `AKAPULU_API_KEY` and `AKAPULU_AVATAR_ID` from the [Akapulu dashboard](https://akapulu.com/)

The default Cartesia voice is Carson (male). Change `_CARTESIA_VOICE_ID` in `agent.py` if your avatar face is female.

## Run and open Console

```bash
python agent.py dev
```

That registers the agent (`akapulu-avatar`) with your LiveKit project. In the LiveKit Cloud UI: **Agents** → **Launch Console** (or open an agent and use Launch Console). Connect and talk. The Akapulu worker joins as `Avatar` and publishes the face and played audio.

Local terminal audio only (no Cloud Console):

```bash
python agent.py console
```

Console mode is audio-first; use `dev` + Agent Console to see the avatar video.

## Notes

- STT stays muted until `wait_until_call_ready` (same idea as the Pipecat Flux gate).
- Loading status is on LiveKit data topic `akapulu.avatar.status`.
- Full plugin docs: https://docs.akapulu.com/guides/calls/livekit
