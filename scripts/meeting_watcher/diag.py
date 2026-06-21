"""Audio-session diagnostic for the meeting watcher.

Run this WHILE a call is active (WhatsApp, Teams, Zoom, …). It prints every
audio session Windows reports on the default speaker and microphone, for both
the *multimedia* and *communications* device roles, plus whether the watcher's
mic-session enumeration works at all.

    python diag.py            # one snapshot
    python diag.py --watch    # refresh every 2s until Ctrl+C

Paste the output back so we can see why a call wasn't detected.
"""

from __future__ import annotations

import sys
import time

ERENDER, ECAPTURE = 0, 1
ROLES = {"multimedia": 1, "communications": 2}
STATE = {0: "Inactive", 1: "Active", 2: "Expired"}


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


def _default_via_getallsessions() -> None:
    """What the watcher's _render_sessions() actually sees."""
    try:
        from pycaw.pycaw import AudioUtilities
        print("\nAudioUtilities.GetAllSessions()  (watcher render source)")
        active = []
        for s in AudioUtilities.GetAllSessions():
            try:
                name = s.Process.name() if s.Process else "(system)"
                st = s.State
            except Exception:
                continue
            if st == 1:
                active.append(name)
            print(f"    {name:<28} {STATE.get(st, st)}{' <== ACTIVE' if st == 1 else ''}")
        print(f"  -> active render procs: {sorted(set(active)) or '(none)'}")
    except Exception as exc:
        print(f"  GetAllSessions FAILED: {type(exc).__name__}: {exc}")


def snapshot() -> None:
    print("=" * 64)
    print(f"snapshot @ {time.strftime('%H:%M:%S')}")
    _default_via_getallsessions()
    _dump_endpoint("RENDER / multimedia (speaker)", ERENDER, ROLES["multimedia"])
    _dump_endpoint("RENDER / communications (speaker)", ERENDER, ROLES["communications"])
    _dump_endpoint("CAPTURE / multimedia (mic)", ECAPTURE, ROLES["multimedia"])
    _dump_endpoint("CAPTURE / communications (mic)", ECAPTURE, ROLES["communications"])


def main() -> None:
    try:
        import pycaw  # noqa: F401
    except Exception as exc:
        print(f"pycaw not importable: {exc}\n  pip install -r requirements.txt")
        sys.exit(1)

    if "--watch" in sys.argv:
        try:
            while True:
                snapshot()
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nstopped.")
    else:
        snapshot()


if __name__ == "__main__":
    main()
