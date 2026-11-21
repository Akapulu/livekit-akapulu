"""Mint LiveKit tokens and open an Akapulu live session.

LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and AKAPULU_API_KEY come
from the environment unless passed in. A missing or blank value raises.

``create_akapulu_livekit_call`` creates a LiveKit room, mints browser and
voice-agent tokens, mints the Avatar token with ``publish_on_behalf`` of the
agent, and POSTs ``/api/live-sessions/`` with ``transport=livekit``. It
returns the LiveKit URL, room name, agent token, browser token, and
``session_id``. The Avatar token is sent to Akapulu Labs and is not returned.

A bot that already has a LiveKit room skips that helper. Mint the Avatar
token with ``mint_avatar_token``, POST with ``post_akapulu_livekit_session``,
then pass ``conversation_session_id`` into ``AvatarSession``, or let
``AvatarSession.start`` mint and POST itself.
"""

from __future__ import annotations

import json
import os
import uuid

import aiohttp
from livekit import api
from livekit.agents.voice.room_io import ATTRIBUTE_PUBLISH_ON_BEHALF

BROWSER_NAME = "You"
AVATAR_IDENTITY = "Avatar"
AVATAR_NAME = "Avatar"
STATUS_TOPIC = "akapulu.avatar.status"
SPEAKING_TOPIC = "akapulu.avatar.speaking"
SAMPLE_RATE = 24000
ROOM_EMPTY_TIMEOUT_SECS = 2 * 60 * 60


def django_origin() -> str:
    raw = os.environ.get("AKAPULU_API_URL")
    if raw is None or not str(raw).strip():
        raise RuntimeError("AKAPULU_API_URL is missing")
    origin = str(raw).strip().rstrip("/")
    if not (origin.startswith("https://") or origin.startswith("http://")):
        raise RuntimeError("AKAPULU_API_URL must be an http(s) origin")
    return origin


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{name} is missing")
    return value.strip()


def _required_text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise RuntimeError(f"akapulu call {label} is missing")
    return value.strip()


def mint_avatar_token(
    *,
    livekit_api_key: str,
    livekit_api_secret: str,
    room_name: str,
    agent_identity: str,
    avatar_identity: str = AVATAR_IDENTITY,
    avatar_name: str = AVATAR_NAME,
) -> str:
    """Mint the Avatar participant JWT (publish_on_behalf of the voice agent)."""
    key = _required_text(livekit_api_key, "livekit_api_key")
    secret = _required_text(livekit_api_secret, "livekit_api_secret")
    room = _required_text(room_name, "room_name")
    agent = _required_text(agent_identity, "agent_identity")
    identity = _required_text(avatar_identity, "avatar_identity")
    name = _required_text(avatar_name, "avatar_name")
    return (
        api.AccessToken(api_key=key, api_secret=secret)
        .with_kind("agent")
        .with_identity(identity)
        .with_name(name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_attributes({ATTRIBUTE_PUBLISH_ON_BEHALF: agent})
        .to_jwt()
    )


def mint_participant_token(
    *,
    livekit_api_key: str,
    livekit_api_secret: str,
    room_name: str,
    identity: str,
    name: str,
    kind: str | None = None,
    can_publish: bool = True,
    can_subscribe: bool = True,
    can_publish_data: bool = True,
) -> str:
    """Mint a browser or voice-agent participant JWT for an existing room."""
    key = _required_text(livekit_api_key, "livekit_api_key")
    secret = _required_text(livekit_api_secret, "livekit_api_secret")
    room = _required_text(room_name, "room_name")
    ident = _required_text(identity, "identity")
    display = _required_text(name, "name")
    token = (
        api.AccessToken(api_key=key, api_secret=secret)
        .with_identity(ident)
        .with_name(display)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=can_publish_data,
            )
        )
    )
    if kind is not None:
        if type(kind) is not str or not kind.strip():
            raise RuntimeError("akapulu call kind is missing")
        token = token.with_kind(kind.strip())
    return token.to_jwt()


async def post_akapulu_livekit_session(
    *,
    session: aiohttp.ClientSession,
    api_origin: str,
    api_key: str,
    avatar_id: str,
    show_ai_label: bool,
    room_url: str,
    room_name: str,
    avatar_token: str,
) -> str:
    """POST ``/api/live-sessions/`` with ``transport=livekit``. Returns session_id."""
    if type(session) is not aiohttp.ClientSession:
        raise RuntimeError("akapulu call session is missing")
    origin = _required_text(api_origin, "api_origin").rstrip("/")
    if not (origin.startswith("https://") or origin.startswith("http://")):
        raise RuntimeError("akapulu call api_origin must be an http(s) origin")
    key = _required_text(api_key, "api_key")
    avatar = _required_text(avatar_id, "avatar_id")
    try:
        avatar = str(uuid.UUID(avatar))
    except ValueError as exc:
        raise RuntimeError("akapulu call avatar_id is not a UUID") from exc
    if type(show_ai_label) is not bool:
        raise RuntimeError(f"akapulu call show_ai_label {show_ai_label!r} is invalid")
    url_room = _required_text(room_url, "room_url")
    if not (url_room.startswith("wss://") or url_room.startswith("ws://")):
        raise RuntimeError("akapulu call room_url must be wss://")
    name = _required_text(room_name, "room_name")
    token = _required_text(avatar_token, "avatar_token")
    url = f"{origin}/api/live-sessions/"
    async with session.post(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "avatar_id": avatar,
            "show_ai_label": show_ai_label,
            "transport": "livekit",
            "room_url": url_room,
            "room_name": name,
            "avatar_token": token,
        },
    ) as response:
        raw = await response.text()
        if response.status != 200:
            raise RuntimeError(f"POST /api/live-sessions/ returned {response.status} {raw}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("POST /api/live-sessions/ is not JSON") from exc
    if type(payload) is not dict or "session_id" not in payload:
        raise RuntimeError("POST /api/live-sessions/ is missing session_id")
    try:
        return str(uuid.UUID(str(payload["session_id"])))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("POST /api/live-sessions/ session_id must be a UUID") from exc


async def create_akapulu_livekit_call(
    *,
    avatar_id: str,
    show_ai_label: bool,
    session: aiohttp.ClientSession,
    room_name: str | None = None,
    agent_identity: str = "agent",
    browser_identity: str = BROWSER_NAME,
    empty_timeout_secs: int = ROOM_EMPTY_TIMEOUT_SECS,
) -> tuple[str, str, str, str, str]:
    """Return livekit_url, room_name, agent_token, browser_token, session_id.

    Reads LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and
    AKAPULU_API_KEY. Creates a LiveKit room, mints the browser and voice-agent
    tokens, mints the Avatar token for Akapulu Labs, and posts the live
    session. The Avatar token is not returned.
    """
    livekit_url = required_env("LIVEKIT_URL")
    if not (livekit_url.startswith("wss://") or livekit_url.startswith("ws://")):
        raise RuntimeError("LIVEKIT_URL must be wss://")
    livekit_key = required_env("LIVEKIT_API_KEY")
    livekit_secret = required_env("LIVEKIT_API_SECRET")
    akapulu_key = required_env("AKAPULU_API_KEY")
    origin = django_origin()
    agent = _required_text(agent_identity, "agent_identity")
    browser = _required_text(browser_identity, "browser_identity")
    if type(empty_timeout_secs) is not int or empty_timeout_secs <= 0:
        raise RuntimeError(f"akapulu call empty_timeout_secs {empty_timeout_secs!r} is invalid")
    if room_name is None:
        name = f"akapulu-{uuid.uuid4().hex}"
    else:
        name = _required_text(room_name, "room_name")

    lk = api.LiveKitAPI(livekit_url, livekit_key, livekit_secret)
    try:
        await lk.room.create_room(
            api.CreateRoomRequest(name=name, empty_timeout=empty_timeout_secs)
        )
    finally:
        await lk.aclose()

    agent_token = mint_participant_token(
        livekit_api_key=livekit_key,
        livekit_api_secret=livekit_secret,
        room_name=name,
        identity=agent,
        name=agent,
        kind="agent",
    )
    browser_token = mint_participant_token(
        livekit_api_key=livekit_key,
        livekit_api_secret=livekit_secret,
        room_name=name,
        identity=browser,
        name=BROWSER_NAME,
    )
    avatar_token = mint_avatar_token(
        livekit_api_key=livekit_key,
        livekit_api_secret=livekit_secret,
        room_name=name,
        agent_identity=agent,
    )
    session_id = await post_akapulu_livekit_session(
        session=session,
        api_origin=origin,
        api_key=akapulu_key,
        avatar_id=avatar_id,
        show_ai_label=show_ai_label,
        room_url=livekit_url,
        room_name=name,
        avatar_token=avatar_token,
    )
    return livekit_url, name, agent_token, browser_token, session_id
