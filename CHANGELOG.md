# Changelog

## [0.1.2] - 2026-11-21

- Rename `conversation_session_id` to `session_id` on `AvatarSession`.
- Rename participant constants to `LIVEKIT_AVATAR_*` / `LIVEKIT_BROWSER_*`.
- Default the Akapulu API origin to production when `AKAPULU_API_URL` is unset.
- Remove the hosted/landing package path; pass `session_id` from
  `create_akapulu_livekit_call` (or a prior live-sessions POST).
- Examples await `wait_until_call_ready` before greeting.

## [0.1.1] - 2026-11-21

- When subscriber `call_is_ready` unlocks after decoded Avatar video frames, send
  `{"type":"call_ready"}` on the private audio socket so the worker can stamp
  Django `call_is_ready_at`.

## Unreleased

- `examples/console_agent`: minimal LiveKit Agents worker for Agent Console (`python agent.py dev`).

## [0.1.0] - 2026-11-20

- Initial public release.
- `AvatarSession` LiveKit Agents plugin: mint Avatar JWT, `POST /api/live-sessions/` with `transport=livekit`, worker sockets, 24 kHz PCM, speaking / idle, status and speaking data topics, subscriber `call_is_ready` video gate, hangup `aclose`.
- `create_akapulu_livekit_call` creates a LiveKit room, mints browser and agent tokens, posts the live session, and returns `livekit_url`, `room_name`, `agent_token`, `browser_token`, `session_id`.
- `mint_avatar_token`, `mint_participant_token`, and `post_akapulu_livekit_session` for bots that already have a room.
