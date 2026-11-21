"""Send assistant PCM to the Akapulu avatar worker over LiveKit.

Plugin path: mints an Avatar participant JWT (publish_on_behalf of the voice
agent), POSTs ``/api/live-sessions/`` with ``transport=livekit``, opens the
worker control + audio sockets, and feeds assistant PCM over the same wire
format as ``pipecat-akapulu``:

  A + uint32 ingest sequence + 24 kHz s16
  {"type":"speaking","who":"bot"|"user","state":"started"|"stopped"|"interrupted"}
  {"type":"call_ready"} after this session has decoded enough Avatar video frames

Landing path: pass ``conversation_session_id`` for a session Django already
created (with avatar token). Skips mint + POST connect; still opens worker
sockets and feeds PCM.

``AKAPULU_API_URL`` selects the Django origin. Empty or non-http(s) raises.
``AKAPULU_API_KEY`` and ``AKAPULU_AVATAR_ID`` are required unless passed in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import time
import uuid
from collections.abc import Iterator
from urllib import error as urllib_error
from urllib import request as urllib_request

import aiohttp
from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    AgentSession,
    NotGivenOr,
    get_job_context,
    utils,
)
from livekit.agents.utils import is_given
from livekit.agents.voice.avatar import (
    AudioSegmentEnd,
    AvatarSession as BaseAvatarSession,
    QueueAudioOutput,
)

from livekit_akapulu.call import (
    AVATAR_IDENTITY,
    AVATAR_NAME,
    SAMPLE_RATE,
    SPEAKING_TOPIC,
    STATUS_TOPIC,
    django_origin,
    mint_avatar_token,
    post_akapulu_livekit_session,
    required_env,
)

logger = logging.getLogger("akapulu.livekit.avatar")

_SESSION_TIMEOUT_SECS = 180.0
# Avatar video frames this session must decode before call_is_ready.
_READY_VIDEO_FRAMES = 5


class AvatarSession(BaseAvatarSession):
    """Send assistant audio to the Akapulu GPU worker over LiveKit transport."""

    def __init__(
        self,
        *,
        avatar_id: NotGivenOr[str] = NOT_GIVEN,
        api_url: NotGivenOr[str] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        conversation_session_id: NotGivenOr[str] = NOT_GIVEN,
        avatar_participant_identity: str = AVATAR_IDENTITY,
        avatar_participant_name: str = AVATAR_NAME,
        show_ai_label: bool = True,
    ) -> None:
        super().__init__()
        resolved_id = avatar_id if is_given(avatar_id) else os.getenv("AKAPULU_AVATAR_ID")
        if not isinstance(resolved_id, str) or not resolved_id.strip():
            raise RuntimeError("AKAPULU_AVATAR_ID is missing")
        try:
            self._avatar_id = str(uuid.UUID(resolved_id.strip()))
        except ValueError as exc:
            raise RuntimeError("AKAPULU_AVATAR_ID must be a UUID") from exc
        if is_given(api_url):
            if not isinstance(api_url, str) or not api_url.strip():
                raise RuntimeError("api_url is missing")
            origin = api_url.strip().rstrip("/")
            if not (origin.startswith("https://") or origin.startswith("http://")):
                raise RuntimeError("api_url must be an http(s) origin")
            self._api_origin = origin
        else:
            self._api_origin = django_origin()
        self._api_key = (
            api_key.strip()
            if is_given(api_key) and isinstance(api_key, str)
            else required_env("AKAPULU_API_KEY")
        )
        if not isinstance(show_ai_label, bool):
            raise RuntimeError("show_ai_label must be a boolean")
        self._show_ai_label = show_ai_label
        self._avatar_participant_identity = avatar_participant_identity
        self._avatar_participant_name = avatar_participant_name
        self._preset_session_id: str | None = None
        if is_given(conversation_session_id):
            if not isinstance(conversation_session_id, str) or not conversation_session_id.strip():
                raise RuntimeError("conversation_session_id is missing")
            try:
                self._preset_session_id = str(uuid.UUID(conversation_session_id.strip()))
            except ValueError as exc:
                raise RuntimeError("conversation_session_id must be a UUID") from exc
        self._session_id: str | None = None
        self._audio_buffer: QueueAudioOutput | None = None
        self._main_atask: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None
        self._audio_resampler: rtc.AudioResampler | None = None
        self._seq = 0
        self._bot_speaking = False
        self._playback_position = 0.0
        self._audio_playing = False
        self._pending: list[bytes | str] = []
        self._audio_ws: aiohttp.ClientWebSocketResponse | None = None
        self._audio_ready = asyncio.Event()
        self._call_ready = asyncio.Event()
        self._idle_event: asyncio.Event | None = None
        self._video_frames = 0
        self._ready_emitted = False
        self._video_watch_atask: asyncio.Task | None = None
        self._closed = False
        self._send_lock = asyncio.Lock()

    @property
    def avatar_identity(self) -> str:
        return self._avatar_participant_identity

    @property
    def provider(self) -> str:
        return "akapulu"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def start(
        self,
        agent_session: AgentSession,
        room: rtc.Room,
        *,
        livekit_url: NotGivenOr[str] = NOT_GIVEN,
        livekit_api_key: NotGivenOr[str] = NOT_GIVEN,
        livekit_api_secret: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        await super().start(agent_session, room)
        livekit_url = livekit_url or (os.getenv("LIVEKIT_URL") or NOT_GIVEN)
        livekit_api_key = livekit_api_key or (os.getenv("LIVEKIT_API_KEY") or NOT_GIVEN)
        livekit_api_secret = livekit_api_secret or (os.getenv("LIVEKIT_API_SECRET") or NOT_GIVEN)
        if not livekit_url or not livekit_api_key or not livekit_api_secret:
            raise RuntimeError(
                "livekit_url, livekit_api_key, and livekit_api_secret must be set"
            )
        if not isinstance(livekit_url, str) or not (
            livekit_url.startswith("wss://") or livekit_url.startswith("ws://")
        ):
            raise RuntimeError("livekit_url must be wss://")
        if not room.name:
            raise RuntimeError("livekit room name is missing")

        self._http = aiohttp.ClientSession()
        if self._preset_session_id is not None:
            # Landing already created the ConversationSession + avatar token.
            self._session_id = self._preset_session_id
        else:
            try:
                job_ctx = get_job_context()
                local_identity = job_ctx.local_participant_identity
            except RuntimeError:
                if not room.isconnected():
                    raise RuntimeError("failed to get local participant identity")
                local_identity = room.local_participant.identity
            if not isinstance(local_identity, str) or not local_identity:
                raise RuntimeError("local participant identity is missing")

            avatar_token = mint_avatar_token(
                livekit_api_key=livekit_api_key,
                livekit_api_secret=livekit_api_secret,
                room_name=room.name,
                agent_identity=local_identity,
                avatar_identity=self._avatar_participant_identity,
                avatar_name=self._avatar_participant_name,
            )
            self._session_id = await post_akapulu_livekit_session(
                session=self._http,
                api_origin=self._api_origin,
                api_key=self._api_key,
                avatar_id=self._avatar_id,
                show_ai_label=self._show_ai_label,
                room_url=livekit_url,
                room_name=room.name,
                avatar_token=avatar_token,
            )
        logger.info(
            "akapulu livekit session_id=%s api=%s avatar_id=%s preset=%s",
            self._session_id,
            self._api_origin,
            self._avatar_id,
            self._preset_session_id is not None,
        )

        self._audio_buffer = QueueAudioOutput(sample_rate=SAMPLE_RATE, wait_playback_start=True)
        await self._audio_buffer.start()
        self._audio_buffer.on("clear_buffer", self._on_clear_buffer)
        agent_session.output.replace_audio_tail(self._audio_buffer)
        self._main_atask = asyncio.create_task(self._main_task(), name="AkapuluAvatar._main_task")
        self._video_watch_atask = asyncio.create_task(
            self._watch_avatar_video(), name="AkapuluAvatar._watch_avatar_video"
        )
        self._video_watch_atask.add_done_callback(self._on_video_watch_done)

    def _on_clear_buffer(self) -> None:
        async def _interrupt() -> None:
            if self._idle_event is not None and not self._idle_event.is_set():
                self._idle_event.set()
            if self._audio_playing or self._bot_speaking:
                if self._audio_buffer is not None:
                    self._audio_buffer.notify_playback_finished(
                        playback_position=self._playback_position,
                        interrupted=True,
                    )
                self._playback_position = 0.0
                self._audio_playing = False
                await self._send_speaking("bot", "interrupted")
                self._bot_speaking = False

        task = asyncio.create_task(_interrupt())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    def _resample_audio(self, frame: rtc.AudioFrame) -> Iterator[rtc.AudioFrame]:
        if self._audio_resampler is not None and frame.sample_rate != self._audio_resampler._input_rate:
            self._audio_resampler = None
        if self._audio_resampler is None and (
            frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1
        ):
            self._audio_resampler = rtc.AudioResampler(
                input_rate=frame.sample_rate,
                output_rate=SAMPLE_RATE,
                num_channels=1,
            )
        if self._audio_resampler:
            yield from self._audio_resampler.push(frame)
        else:
            yield frame

    def _status_url(self) -> str:
        if not self._session_id:
            raise RuntimeError("session_id is missing")
        return f"{self._api_origin}/api/live-sessions/{self._session_id}/status/"

    def _fetch_status(self) -> dict:
        request = urllib_request.Request(self._status_url(), method="GET")
        try:
            with urllib_request.urlopen(request, timeout=15) as response:
                if response.status != 200:
                    raise RuntimeError(f"status lookup returned {response.status}")
                raw = response.read()
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"status lookup returned {exc.code} {body}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("status lookup is not JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("status lookup is not an object")
        return payload

    async def _wait_worker_wss(self) -> str:
        deadline = time.perf_counter() + _SESSION_TIMEOUT_SECS
        while True:
            payload = await asyncio.to_thread(self._fetch_status)
            url = payload.get("worker_wss_url")
            if isinstance(url, str) and url.startswith("wss://"):
                return url
            if url is not None and url != "":
                raise RuntimeError("worker_wss_url is not wss://")
            if time.perf_counter() >= deadline:
                raise RuntimeError("akapulu avatar worker url was not posted")
            await asyncio.sleep(0.5)

    def _room_can_publish(self) -> bool:
        return (
            not self._closed
            and self._room is not None
            and self._room.isconnected()
        )

    async def _send_or_queue(self, message: bytes | str) -> None:
        async with self._send_lock:
            if self._closed:
                return
            if self._audio_ws is None:
                self._pending.append(message)
                return
            if self._audio_ws.closed:
                return
            if isinstance(message, str):
                await self._audio_ws.send_str(message)
            else:
                await self._audio_ws.send_bytes(message)

    async def _publish_speaking(self, who: str, state: str) -> None:
        """Tell the browser when bot playback starts/stops (harness wait)."""
        if who != "bot":
            return
        if state not in ("started", "stopped", "interrupted"):
            raise RuntimeError(f"akapulu avatar speaking state is {state!r}")
        if not self._room_can_publish():
            logger.info(
                "akapulu avatar speaking skipped (room closed) who=%s state=%s",
                who,
                state,
            )
            return
        speaking = state == "started"
        payload = {
            "type": "speaking",
            "who": who,
            "state": state,
            "bot_speaking": speaking,
        }
        await self._room.local_participant.publish_data(
            json.dumps(payload),
            reliable=True,
            topic=SPEAKING_TOPIC,
        )

    async def _send_speaking(self, who: str, state: str) -> None:
        await self._send_or_queue(
            json.dumps({"type": "speaking", "who": who, "state": state})
        )
        await self._publish_speaking(who, state)
        if not self._closed:
            logger.info("akapulu avatar speaking who=%s state=%s", who, state)


    async def _mark_call_ready(self) -> None:
        if self._ready_emitted or self._closed:
            return
        self._ready_emitted = True
        self._call_ready.set()
        logger.info(
            "akapulu avatar call_is_ready after video_frames=%s",
            self._video_frames,
        )
        await self._publish_status(
            {
                "type": "status",
                "call_is_ready": True,
                "latest_update_text": "Media stream active...",
                "completion_percent": 100,
            }
        )
        await self._send_or_queue(json.dumps({"type": "call_ready"}))

    def _on_video_watch_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "akapulu avatar video watch failed before call_is_ready",
                exc_info=exc,
            )

    async def _find_avatar_participant(self) -> rtc.Participant:
        assert self._room is not None
        room = self._room
        deadline = time.perf_counter() + _SESSION_TIMEOUT_SECS
        while not self._closed:
            for remote in room.remote_participants.values():
                if remote.identity == self._avatar_participant_identity:
                    return remote
            if room.local_participant.identity == self._avatar_participant_identity:
                return room.local_participant
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    "akapulu avatar participant was not found for video ready"
                )
            await asyncio.sleep(0.05)
        raise RuntimeError("akapulu avatar closed before Avatar participant joined")

    async def _subscribed_avatar_video_track(
        self, participant: rtc.Participant
    ) -> rtc.Track:
        """Return Avatar's subscribed video track (any TrackSource).

        ``VideoStream.from_participant(..., SOURCE_CAMERA)`` never fires when
        the publisher left TrackPublishOptions.source at SOURCE_UNKNOWN (LiveKit
        default). Daily captures by participant id without a source filter; match
        that here with ``from_track`` once any KIND_VIDEO publication is live.
        """
        deadline = time.perf_counter() + _SESSION_TIMEOUT_SECS
        while not self._closed:
            for pub in participant.track_publications.values():
                if pub.kind != rtc.TrackKind.KIND_VIDEO:
                    continue
                if isinstance(pub, rtc.RemoteTrackPublication) and not pub.subscribed:
                    pub.set_subscribed(True)
                track = pub.track
                if track is not None:
                    logger.info(
                        "akapulu avatar video track sid=%s source=%s name=%s",
                        pub.sid,
                        pub.source,
                        pub.name,
                    )
                    return track
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    "akapulu avatar video track was not subscribed for video ready"
                )
            await asyncio.sleep(0.05)
        raise RuntimeError("akapulu avatar closed before Avatar video track subscribed")

    async def _watch_avatar_video(self) -> None:
        """Decode Avatar video frames until the ready buffer is full."""
        assert self._room is not None
        participant = await self._find_avatar_participant()
        if self._closed:
            return
        track = await self._subscribed_avatar_video_track(participant)
        if self._closed:
            return
        stream = rtc.VideoStream.from_track(track=track)
        try:
            async for _event in stream:
                if self._closed or self._ready_emitted:
                    return
                self._video_frames += 1
                if self._video_frames < _READY_VIDEO_FRAMES:
                    logger.info(
                        "akapulu avatar video frame %s/%s",
                        self._video_frames,
                        _READY_VIDEO_FRAMES,
                    )
                    continue
                await self._mark_call_ready()
                return
        finally:
            await stream.aclose()

    async def wait_until_call_ready(self, *, timeout: float = _SESSION_TIMEOUT_SECS) -> None:
        """Wait until this session has decoded enough Avatar video frames.

        ``wait_for_join`` only means the Avatar participant and video track
        exist. The opening greeting must wait until this bot has received
        ``_READY_VIDEO_FRAMES`` decoded frames, same gate as the Pipecat
        hosted bot. Unlock also sends ``{"type":"call_ready"}`` on the
        private audio socket so the worker can stamp Django
        ``call_is_ready_at``.
        """
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise RuntimeError(f"akapulu avatar call_is_ready timeout is {timeout!r}")
        ready_wait = asyncio.create_task(self._call_ready.wait())
        watch = self._video_watch_atask
        waiters: set[asyncio.Task] = {ready_wait}
        if watch is not None:
            waiters.add(watch)
        done, pending = await asyncio.wait(
            waiters,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if watch is not None and watch in done and not watch.cancelled():
            watch_exc = watch.exception()
            if watch_exc is not None:
                raise RuntimeError(
                    "akapulu avatar video watch failed before call_is_ready"
                ) from watch_exc
            if not self._call_ready.is_set():
                raise RuntimeError(
                    "akapulu avatar video watch ended without call_is_ready"
                )
        if not self._call_ready.is_set():
            raise TimeoutError(
                f"akapulu avatar call_is_ready was not set within {timeout}s"
            )

    async def _publish_status(self, body: dict) -> None:
        """Forward Modal worker loading status to the browser over LiveKit data."""
        ready = body.get("call_is_ready")
        update_text = body.get("latest_update_text")
        percent = body.get("completion_percent")
        if not isinstance(ready, bool):
            raise RuntimeError("akapulu avatar status call_is_ready is missing")
        if not isinstance(update_text, str) or not update_text:
            raise RuntimeError("akapulu avatar status latest_update_text is missing")
        if type(percent) is not int:
            raise RuntimeError("akapulu avatar status completion_percent is missing")
        # Worker status always claims call_is_ready false. Official ready is
        # subscriber video frames (see _mark_call_ready), which also tells
        # the worker to stamp Django call_is_ready_at.
        ready_out = self._call_ready.is_set()
        if not self._room_can_publish():
            logger.info(
                "akapulu avatar status skipped (room closed) ready=%s percent=%s",
                ready_out,
                percent,
            )
            return
        payload = {
            "type": "status",
            "call_is_ready": ready_out,
            "latest_update_text": update_text,
            "completion_percent": percent,
        }
        await self._room.local_participant.publish_data(
            json.dumps(payload),
            reliable=True,
            topic=STATUS_TOPIC,
        )
        logger.info(
            "akapulu avatar status ready=%s percent=%s text=%s",
            ready_out,
            percent,
            update_text,
        )

    async def _read_control_status(
        self, control: aiohttp.ClientWebSocketResponse
    ) -> None:
        """Keep reading ``/v1/live`` after streams; status keeps coming until hangup."""
        while not self._closed:
            msg = await control.receive()
            if msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
            ):
                # Modal closes /v1/live on hangup. Same as audio socket close.
                logger.info("akapulu avatar control socket closed")
                return
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            body = json.loads(msg.data)
            if not isinstance(body, dict):
                raise RuntimeError("akapulu avatar control message is not an object")
            kind = body.get("type")
            if kind == "status":
                await self._publish_status(body)
                continue
            if kind == "streams":
                raise RuntimeError("akapulu avatar streams arrived twice")
            raise RuntimeError(f"akapulu avatar session type is {kind!r}")

    async def _send_pcm(self, pcm: bytes) -> None:
        if len(pcm) < 2:
            return
        seq = self._seq
        self._seq += 1
        await self._send_or_queue(b"A" + struct.pack(">I", seq) + pcm)

    async def _open_audio(self, audio_url: str) -> None:
        assert self._http is not None
        ws = await self._http.ws_connect(audio_url, heartbeat=30.0, max_msg_size=0)
        async with self._send_lock:
            self._audio_ws = ws
            await ws.send_str(json.dumps({"type": "format", "sample_rate": SAMPLE_RATE}))
            pending = self._pending
            self._pending = []
            for message in pending:
                if isinstance(message, str):
                    await ws.send_str(message)
                else:
                    await ws.send_bytes(message)
        self._audio_ready.set()
        logger.info("akapulu avatar audio socket open")

    async def _main_task(self) -> None:
        assert self._http is not None
        assert self._audio_buffer is not None
        assert self._session_id is not None
        control: aiohttp.ClientWebSocketResponse | None = None
        try:
            worker_wss = await self._wait_worker_wss()
            control = await self._http.ws_connect(worker_wss, heartbeat=30.0, max_msg_size=0)
            await control.send_str(
                json.dumps(
                    {
                        "type": "livekit",
                        "sample_rate": SAMPLE_RATE,
                        "session_id": self._session_id,
                    }
                )
            )
            audio_url: str | None = None
            while not self._closed:
                msg = await control.receive()
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    if self._closed:
                        return
                    raise RuntimeError("akapulu avatar control socket closed")
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                body = json.loads(msg.data)
                if not isinstance(body, dict):
                    raise RuntimeError("akapulu avatar control message is not an object")
                kind = body.get("type")
                if kind == "status":
                    await self._publish_status(body)
                    continue
                if kind == "streams":
                    if "video" in body:
                        raise RuntimeError("akapulu avatar session still has a video socket")
                    audio_url = body.get("audio")
                    if not isinstance(audio_url, str) or not audio_url.startswith("wss://"):
                        raise RuntimeError("akapulu avatar audio URL is missing")
                    break
                raise RuntimeError(f"akapulu avatar session type is {kind!r}")
            if audio_url is None:
                raise RuntimeError("akapulu avatar streams did not arrive")
            await self._open_audio(audio_url)

            async def _forward_audio() -> None:
                assert self._audio_buffer is not None
                await self._audio_ready.wait()
                await self._call_ready.wait()
                if self._closed:
                    return
                async for audio_frame in self._audio_buffer:
                    if self._closed:
                        return
                    if isinstance(audio_frame, rtc.AudioFrame):
                        if not self._bot_speaking:
                            self._bot_speaking = True
                            self._audio_playing = True
                            await self._send_speaking("bot", "started")
                            if self._closed:
                                return
                            self._audio_buffer.notify_playback_started()
                        for resampled in self._resample_audio(audio_frame):
                            if self._closed:
                                return
                            pcm = resampled.data.tobytes()
                            self._playback_position += resampled.duration
                            await self._send_pcm(pcm)
                    elif isinstance(audio_frame, AudioSegmentEnd):
                        if self._bot_speaking:
                            # Tell the worker TTS ended; wait for publish idle
                            # before browser BotStoppedSpeaking / playback done.
                            self._idle_event = asyncio.Event()
                            await self._send_or_queue(
                                json.dumps(
                                    {
                                        "type": "speaking",
                                        "who": "bot",
                                        "state": "stopped",
                                    }
                                )
                            )
                            logger.info(
                                "akapulu avatar speaking who=bot state=stopped "
                                "(waiting for idle)"
                            )
                            await self._idle_event.wait()
                            self._idle_event = None
                            if self._closed:
                                self._audio_playing = False
                                self._bot_speaking = False
                                return
                            await self._publish_speaking("bot", "stopped")
                            self._audio_buffer.notify_playback_finished(
                                playback_position=self._playback_position,
                                interrupted=False,
                            )
                            self._playback_position = 0.0
                            self._audio_playing = False
                            self._bot_speaking = False
                            logger.info("akapulu avatar speaking who=bot state=stopped after idle")

            async def _recv_audio() -> None:
                assert self._audio_ws is not None
                async for msg in self._audio_ws:
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                    ):
                        return
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        body = json.loads(msg.data)
                        if not isinstance(body, dict):
                            raise RuntimeError(
                                f"akapulu avatar audio socket rejected text {body!r}"
                            )
                        kind = body.get("type")
                        if kind == "idle":
                            if self._idle_event is None:
                                logger.info("akapulu avatar idle with no pending stop")
                            else:
                                self._idle_event.set()
                                logger.info("akapulu avatar idle ack")
                            continue
                        if kind == "cut":
                            logger.info(
                                "akapulu avatar cut drained=%s next_idx=%s",
                                body.get("drained"),
                                body.get("next_idx"),
                            )
                            continue
                        raise RuntimeError(
                            f"akapulu avatar audio socket rejected text {body!r}"
                        )
                    raise RuntimeError("akapulu avatar audio socket received playback bytes")

            tasks = [
                asyncio.create_task(_forward_audio(), name="akapulu-forward-audio"),
                asyncio.create_task(_recv_audio(), name="akapulu-recv-audio"),
                asyncio.create_task(
                    self._read_control_status(control),
                    name="akapulu-control-status",
                ),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await utils.aio.cancel_and_wait(*pending)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._closed:
                logger.info("akapulu avatar main task stopped after hangup")
                return
            logger.exception("akapulu avatar main task failed")
            raise
        finally:
            if control is not None and not control.closed:
                await control.close()

    async def aclose(self) -> None:
        """Stop sockets and tasks before the LiveKit room disconnects."""
        if self._closed and self._main_atask is None and self._http is None:
            return
        self._closed = True
        # Unblock waits so cancel is not racing a hung idle/ready await.
        if self._idle_event is not None and not self._idle_event.is_set():
            self._idle_event.set()
        self._audio_ready.set()
        self._call_ready.set()
        if self._audio_ws is not None and not self._audio_ws.closed:
            await self._audio_ws.close()
            self._audio_ws = None
        if self._video_watch_atask is not None and not self._video_watch_atask.done():
            await utils.aio.cancel_and_wait(self._video_watch_atask)
            self._video_watch_atask = None
        if self._main_atask is not None and not self._main_atask.done():
            await utils.aio.cancel_and_wait(self._main_atask)
            self._main_atask = None
        if self._audio_buffer is not None:
            await self._audio_buffer.aclose()
            self._audio_buffer = None
        if self._http is not None and not self._http.closed:
            await self._http.close()
            self._http = None
        await super().aclose()
