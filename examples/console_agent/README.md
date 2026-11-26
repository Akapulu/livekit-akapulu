# Agent Console example

Minimal LiveKit Agents worker that attaches [`AvatarSession`](../../README.md) and is meant for the [LiveKit Agent Console](https://docs.livekit.io/testing/agent-console/).

This is not a browser app. You run the worker locally, then talk to it from Console.

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
- `AKAPULU_API_KEY` from the Akapulu dashboard.
- `AKAPULU_AVATAR_ID`: defaults to Hollis (`1285bfe4-3512-4b34-93ad-196098597a1c`) in `.env.example`.

The default Cartesia voice is Carson (male), for Hollis. Change `_CARTESIA_VOICE_ID` in `agent.py` if your avatar face is female.

## Run and open Console

```bash
python agent.py dev
```

In the LiveKit Cloud UI (same project as your `LIVEKIT_*` keys):

1. **Agents** → **Launch Console**
2. Leave the agent dropdown on **No agent selected**
3. **Start session**

The local worker registers unnamed so Console auto-dispatches to it. The Akapulu worker joins as `Avatar` and publishes the face and played audio. After you start a session, the avatar can take around **10 seconds** to load before the face is ready and the bot greets.

Local terminal audio only (no Cloud Console):

```bash
python agent.py console
```

Console mode is audio-first; use `dev` + Agent Console to see the avatar video.

## Notes

- After **Start session**, wait about 10 seconds for the avatar worker to load before expecting video or the greeting.
- STT stays muted until `wait_until_call_ready` (same idea as the Pipecat Flux gate).
- Loading status is on LiveKit data topic `akapulu.avatar.status` (Agent Console does not show a spinner for that topic).
- Full plugin docs: https://docs.akapulu.com/guides/calls/livekit
