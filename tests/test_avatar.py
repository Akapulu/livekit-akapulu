"""Hermetic checks for AvatarSession source (no room / no network)."""

from pathlib import Path

_AVATAR = (
    Path(__file__).resolve().parents[1] / "src" / "livekit_akapulu" / "avatar.py"
).read_text()


def test_avatar_skips_publish_when_room_closed() -> None:
    if "def _room_can_publish(self)" not in _AVATAR:
        raise RuntimeError("avatar missing _room_can_publish")
    publish_speaking = _AVATAR.split("async def _publish_speaking(", 1)[1].split(
        "async def _send_speaking(", 1
    )[0]
    if "_room_can_publish()" not in publish_speaking:
        raise RuntimeError("_publish_speaking does not gate on _room_can_publish")
    if "speaking skipped (room closed)" not in publish_speaking:
        raise RuntimeError("_publish_speaking does not skip when room is closed")
    publish_status = _AVATAR.split("async def _publish_status(", 1)[1].split(
        "async def _read_control_status(", 1
    )[0]
    if "_room_can_publish()" not in publish_status:
        raise RuntimeError("_publish_status does not gate on _room_can_publish")
    if "status skipped (room closed)" not in publish_status:
        raise RuntimeError("_publish_status does not skip when room is closed")


def test_avatar_aclose_unblocks_idle_and_closes_audio_first() -> None:
    aclose = _AVATAR.split("async def aclose(self) -> None:", 1)[1]
    if "self._idle_event.set()" not in aclose:
        raise RuntimeError("aclose does not unblock idle wait on hangup")
    if "self._audio_ready.set()" not in aclose or "self._call_ready.set()" not in aclose:
        raise RuntimeError("aclose does not unblock ready waits on hangup")
    audio_close_at = aclose.find("self._audio_ws.close()")
    main_cancel_at = aclose.find("cancel_and_wait(self._main_atask)")
    if audio_close_at < 0 or main_cancel_at < 0:
        raise RuntimeError("aclose missing audio close or main-task cancel")
    if audio_close_at > main_cancel_at:
        raise RuntimeError("aclose must close audio socket before cancelling main task")


def test_avatar_watches_subscribed_track_not_camera_source_filter() -> None:
    watch = _AVATAR.split("async def _watch_avatar_video(", 1)[1].split(
        "async def wait_until_call_ready(", 1
    )[0]
    if "VideoStream.from_participant" in watch:
        raise RuntimeError(
            "_watch_avatar_video still uses from_participant "
            "(misses SOURCE_UNKNOWN publishes)"
        )
    if "VideoStream.from_track" not in watch:
        raise RuntimeError("avatar does not decode Avatar video via from_track")
    if "_subscribed_avatar_video_track" not in _AVATAR:
        raise RuntimeError("avatar does not wait for a subscribed Avatar video track")
    if "set_subscribed(True)" not in _AVATAR:
        raise RuntimeError("avatar does not force-subscribe Avatar video")
    if "watch ended without call_is_ready" not in _AVATAR:
        raise RuntimeError("wait_until_call_ready does not surface watch exit")


def test_avatar_uses_shared_call_helpers() -> None:
    if "mint_avatar_token(" not in _AVATAR:
        raise RuntimeError("avatar does not mint via mint_avatar_token")
    if "post_akapulu_livekit_session(" not in _AVATAR:
        raise RuntimeError("avatar does not connect via post_akapulu_livekit_session")
    if "async def _post_connect(" in _AVATAR:
        raise RuntimeError("avatar still has inline _post_connect")




def test_avatar_uses_session_id_not_landing_name() -> None:
    if "conversation_session_id" in _AVATAR:
        raise RuntimeError("avatar still uses conversation_session_id")
    if "session_id: NotGivenOr[str]" not in _AVATAR:
        raise RuntimeError("avatar missing session_id ctor arg")
    if "Landing path" in _AVATAR or "Landing already" in _AVATAR:
        raise RuntimeError("avatar still documents a landing path")
    if "_preset_session_id" in _AVATAR:
        raise RuntimeError("avatar still uses _preset_session_id")
    if "api_url: NotGivenOr" in _AVATAR or "api_key: NotGivenOr" in _AVATAR:
        raise RuntimeError("avatar still accepts api_url/api_key ctor args")
    if "livekit_api_key: NotGivenOr" in _AVATAR:
        raise RuntimeError("start still accepts LiveKit secret kwargs")

def test_avatar_tells_worker_call_ready() -> None:
    mark = _AVATAR.split("async def _mark_call_ready(", 1)[1].split(
        "def _on_video_watch_done(", 1
    )[0]
    if '{"type": "call_ready"}' not in mark:
        raise RuntimeError("_mark_call_ready does not send call_ready on the audio socket")
    if "_send_or_queue" not in mark:
        raise RuntimeError("_mark_call_ready does not send call_ready via _send_or_queue")


if __name__ == "__main__":
    test_avatar_skips_publish_when_room_closed()
    test_avatar_aclose_unblocks_idle_and_closes_audio_first()
    test_avatar_watches_subscribed_track_not_camera_source_filter()
    test_avatar_uses_shared_call_helpers()
    test_avatar_tells_worker_call_ready()
    test_avatar_uses_session_id_not_landing_name()
    print("ok")
