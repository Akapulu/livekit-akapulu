# livekit-akapulu

LiveKit Agents plugin for an [Akapulu Labs](https://akapulu.com) talking avatar. Maintained by Akapulu Labs.

`AvatarSession` sits on your LiveKit Agents voice session. It sends assistant audio to the Akapulu Labs worker. The worker joins the LiveKit room for that call and publishes the avatar video and audio. Your voice agent itself does not publish the face.

Requires `livekit-agents` 1.8 or newer.

## Install

```bash
uv add livekit-akapulu
```

## Try in Agent Console

A minimal worker lives in [`examples/console_agent`](examples/console_agent). Copy `.env.example`, run `python agent.py dev`, then open **Launch Console** in your LiveKit Cloud project. Details: [`examples/console_agent/README.md`](examples/console_agent/README.md).

## Use

### Plugin path (voice agent already in a LiveKit room)

```python
from livekit_akapulu import AvatarSession

avatar = AvatarSession(show_ai_label=True)
await avatar.start(agent_session, ctx.room)
await avatar.wait_until_call_ready()
```

`AvatarSession.start` mints the Avatar participant JWT (`publish_on_behalf` of your agent), `POST /api/live-sessions/` with `transport=livekit`, opens the worker sockets, and forwards assistant PCM. Set `AKAPULU_API_URL`, `AKAPULU_API_KEY`, `AKAPULU_AVATAR_ID`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.

### Connect helper (create room + session from your server)

`create_akapulu_livekit_call` runs on your server before the agent starts. It reads `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and `AKAPULU_API_KEY`. A missing or blank value raises. It creates a LiveKit room, mints the browser and voice-agent tokens, mints the Avatar token for Akapulu Labs, and `POST /api/live-sessions/`. It returns the LiveKit URL, room name, agent token, browser token, and `session_id`. The Avatar token is sent to Akapulu Labs and is not returned.

```python
from livekit_akapulu import AvatarSession, create_akapulu_livekit_call

livekit_url, room_name, agent_token, browser_token, session_id = (
    await create_akapulu_livekit_call(
        avatar_id=avatar_id,
        show_ai_label=True,
        session=session,
    )
)
avatar = AvatarSession(conversation_session_id=session_id)
```

### Hosted / landing path

When Django already created the conversation session and Avatar token, pass that id:

```python
avatar = AvatarSession(conversation_session_id=session_id)
await avatar.start(agent_session, room)
```

## Status and speaking topics

While the worker boots, the plugin republishes loading status on LiveKit data topic `akapulu.avatar.status` (same fields as Pipecat RTVI `type: status`: `call_is_ready`, `completion_percent`, `latest_update_text`). Bot speaking edges go on `akapulu.avatar.speaking`. Keep the loading UI up until `call_is_ready` is true. `wait_until_call_ready()` waits until this session has decoded enough Avatar video frames.

## Wire format

Same as `pipecat-akapulu`:

- ingest audio: `A` + uint32 sequence + 24 kHz s16 PCM
- speaking: `{"type":"speaking","who":"bot"|"user","state":"started"|"stopped"|"interrupted"}`
- worker `{"type":"idle"}` after the last live speech PCM is published
