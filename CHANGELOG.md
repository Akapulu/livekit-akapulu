# Changelog

## Unreleased

- `examples/console_agent`: minimal LiveKit Agents worker for Agent Console (`python agent.py dev`).

## [0.1.0] - 2026-11-20

- Initial public release.
- `AvatarSession` LiveKit Agents plugin: mint Avatar JWT, `POST /api/live-sessions/` with `transport=livekit`, worker sockets, 24 kHz PCM, speaking / idle, status and speaking data topics, subscriber `call_is_ready` video gate, hangup `aclose`.
- `create_akapulu_livekit_call` creates a LiveKit room, mints browser and agent tokens, posts the live session, and returns `livekit_url`, `room_name`, `agent_token`, `browser_token`, `session_id`.
- `mint_avatar_token`, `mint_participant_token`, and `post_akapulu_livekit_session` for bots that already have a room.
