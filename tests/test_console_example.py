"""Hermetic checks for the Agent Console example."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "examples" / "console_agent" / "agent.py"


def test_console_agent_exists_and_gates_stt() -> None:
    if not AGENT.is_file():
        raise RuntimeError("examples/console_agent/agent.py is missing")
    text = AGENT.read_text(encoding="utf-8")
    if "AvatarSession" not in text:
        raise RuntimeError("console agent does not use AvatarSession")
    mute = text.find("session.input.set_audio_enabled(False)")
    wait = text.find("await avatar.wait_until_call_ready")
    unmute = text.find("session.input.set_audio_enabled(True)")
    if not (0 <= mute < wait < unmute):
        raise RuntimeError(
            f"console agent STT mute order wrong: mute={mute} wait={wait} unmute={unmute}"
        )
    if 'agent_name="akapulu-avatar"' not in text and "agent_name=_AGENT_NAME" not in text:
        raise RuntimeError("console agent must register an agent_name for Console dispatch")


if __name__ == "__main__":
    test_console_agent_exists_and_gates_stt()
    print("test_console_example OK", flush=True)
