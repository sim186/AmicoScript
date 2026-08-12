"""Audio-activity diagnostic for the meeting watcher.

Run this WHILE a call is active (WhatsApp, Teams, Zoom, …). It prints what the
watcher's platform backend can see — which processes are on the speaker, which
are on the microphone, and whether the app lists would have matched — so a
"why wasn't my call detected?" report has the one fact that answers it.

    python diag.py            # one snapshot
    python diag.py --watch    # refresh every 2s until Ctrl+C

On Windows it also dumps the raw audio sessions per endpoint and role, which is
a level of detail only that platform exposes.

Paste the output back so we can see why a call wasn't detected.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import watcher_platform  # noqa: E402

ERENDER, ECAPTURE = 0, 1
ROLES = {"multimedia": 1, "communications": 2}
STATE = {0: "Inactive", 1: "Active", 2: "Expired"}


# --------------------------------------------------------------------------- #
# Cross-platform: what the watcher itself would decide
# --------------------------------------------------------------------------- #
def _decision(backend) -> None:
    import watcher

    speaking = backend.speaking_procs()
    listening = backend.listening_procs()

    print(f"\nspeaking (on the speaker): {sorted(speaking) or '(none)'}")
    if listening is None:
        print("listening (on the mic):    UNAVAILABLE on this host "
              "— the mic heuristic is off, so only the app list can match")
    else:
        print(f"listening (on the mic):    {sorted(listening) or '(none)'}")

    print(f"\ncall apps (speaker alone): {', '.join(sorted(watcher.CALL_APPS))}")
    print(f"chat apps (mic+speaker):   {', '.join(sorted(watcher.CHAT_APPS))}")
    print(f"blocked:                   {', '.join(sorted(watcher.BLOCK_APPS))}")

    in_call, app = watcher.call_in_progress()
    verdict = f"IN A CALL ({app})" if in_call else "not in a call"
    print(f"\n  -> the watcher would say: {verdict}")
    if not in_call and speaking and listening:
        both = speaking & listening
        if both:
            blocked = {n for n in both if any(b in n for b in watcher.BLOCK_APPS)}
            if blocked:
                print(f"     (on mic AND speaker but blocklisted: {sorted(blocked)})")


# --------------------------------------------------------------------------- #
# Windows extra: raw sessions per endpoint and role
# --------------------------------------------------------------------------- #
def _enum(flow: int, role: int):
    """Return (device_name, [(proc_name, state_int), ...]) for an endpoint."""
    import comtypes
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator, PROPERTYKEY  # noqa: F401
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.utils import AudioSession

    enumerator = comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
    )
    dev = enumerator.GetDefaultAudioEndpoint(flow, role)
    try:
        dev_name = dev.GetId()
    except Exception:
        dev_name = "?"
    mgr = dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
    mgr = mgr.QueryInterface(IAudioSessionManager2)
    se = mgr.GetSessionEnumerator()
    rows = []
    for i in range(se.GetCount()):
        ctl = se.GetSession(i).QueryInterface(IAudioSessionControl2)
        s = AudioSession(ctl)
        try:
            name = s.Process.name() if s.Process else "(system)"
        except Exception:
            name = "(unknown)"
        try:
            state = s.State
        except Exception:
            state = -1
        rows.append((name, state))
    return dev_name, rows


def _dump_endpoint(label: str, flow: int, role: int) -> None:
    try:
        dev, rows = _enum(flow, role)
        print(f"\n{label}  [{dev}]")
        if not rows:
            print("    (no sessions)")
        for name, state in rows:
            mark = " <== ACTIVE" if state == 1 else ""
            print(f"    {name:<28} {STATE.get(state, state)}{mark}")
    except Exception as exc:
        print(f"\n{label}  ENUMERATION FAILED: {type(exc).__name__}: {exc}")


def _windows_detail() -> None:
    _dump_endpoint("RENDER / multimedia (speaker)", ERENDER, ROLES["multimedia"])
    _dump_endpoint("RENDER / communications (speaker)", ERENDER, ROLES["communications"])
    _dump_endpoint("CAPTURE / multimedia (mic)", ECAPTURE, ROLES["multimedia"])
    _dump_endpoint("CAPTURE / communications (mic)", ECAPTURE, ROLES["communications"])


def snapshot(backend) -> None:
    print("=" * 64)
    print(f"snapshot @ {time.strftime('%H:%M:%S')}")
    _decision(backend)
    if watcher_platform.platform_key() == "windows":
        _windows_detail()


def main() -> None:
    print(f"platform: {watcher_platform.platform_key() or sys.platform}")
    backend, reason = watcher_platform.get_backend()
    if backend is None:
        print(f"NO BACKEND: {reason}")
        print("  Install the watcher's dependencies: pip install -r requirements.txt")
        sys.exit(1)
    print(f"backend:  {backend.name}")

    if "--watch" in sys.argv:
        try:
            while True:
                snapshot(backend)
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nstopped.")
    else:
        snapshot(backend)


if __name__ == "__main__":
    main()
