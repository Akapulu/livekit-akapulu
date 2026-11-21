"""Hermetic checks for call helpers (no network)."""

from pathlib import Path

_CALL = (
    Path(__file__).resolve().parents[1] / "src" / "livekit_akapulu" / "call.py"
).read_text()


def test_create_helper_posts_transport_livekit() -> None:
    if '"transport": "livekit"' not in _CALL:
        raise RuntimeError("post helper does not set transport=livekit")
    if "async def create_akapulu_livekit_call(" not in _CALL:
        raise RuntimeError("create_akapulu_livekit_call is missing")
    if "async def post_akapulu_livekit_session(" not in _CALL:
        raise RuntimeError("post_akapulu_livekit_session is missing")
    if "def mint_avatar_token(" not in _CALL:
        raise RuntimeError("mint_avatar_token is missing")
    if "ATTRIBUTE_PUBLISH_ON_BEHALF" not in _CALL:
        raise RuntimeError("mint_avatar_token does not set publish_on_behalf")


def test_create_helper_returns_five_values() -> None:
    create = _CALL.split("async def create_akapulu_livekit_call(", 1)[1]
    if "return livekit_url, name, agent_token, browser_token, session_id" not in create:
        raise RuntimeError("create_akapulu_livekit_call return shape changed")


def test_django_origin_defaults_to_prod() -> None:
    if "_PROD_DJANGO_ORIGIN" not in _CALL:
        raise RuntimeError("django_origin has no production default")
    if 'raise RuntimeError("AKAPULU_API_URL is missing")' in _CALL:
        raise RuntimeError("django_origin still requires AKAPULU_API_URL")
    if "LIVEKIT_AVATAR_IDENTITY" not in _CALL or "LIVEKIT_BROWSER_IDENTITY" not in _CALL:
        raise RuntimeError("LiveKit participant constants were not renamed")


if __name__ == "__main__":
    test_create_helper_posts_transport_livekit()
    test_create_helper_returns_five_values()
    test_django_origin_defaults_to_prod()
    print("ok")
