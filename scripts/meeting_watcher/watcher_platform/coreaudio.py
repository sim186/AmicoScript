"""ctypes bindings for the slice of Core Audio the macOS backend needs.

Kept apart from ``macos.py`` for two reasons: the backend stays readable as
audio logic rather than pointer arithmetic, and the tests can inject a fake
version of this module to exercise the detection and capture logic without a
Mac in the loop.

Everything here is the public C API in
``/System/Library/Frameworks/CoreAudio.framework``. The one exception is
``CATapDescription``, an Objective-C class with no C constructor — it is
reached through PyObjC, whose object pointer is handed back to ctypes.

Reading the process list and its properties needs **no** permission. Creating a
tap does: that is the TCC audio-capture consent, and the only call here that
can fail for a reason the user has to fix.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import struct
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_char,
    c_double,
    c_int32,
    c_uint32,
    c_void_p,
    sizeof,
)

try:
    _coreaudio = ctypes.CDLL(
        "/System/Library/Frameworks/CoreAudio.framework/Versions/A/CoreAudio"
    )
    _cf = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation"
    )
    _objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
except OSError as exc:
    # ImportError, not OSError: `import watcher_platform.macos` must be
    # skippable on a non-Mac host. pytest.importorskip (and everything else
    # that treats "this module does not exist here" as ImportError) only
    # honours ImportError, while ctypes.CDLL raises OSError when a framework
    # is absent — which used to abort test collection on the Linux CI runner.
    raise ImportError(
        "the macOS Core Audio bindings need Apple's frameworks, "
        f"which are not available on this host: {exc}"
    ) from exc
_libsystem = ctypes.CDLL(None)


def fourcc(code: str) -> int:
    """'prs#' -> the UInt32 selector Core Audio expects."""
    return struct.unpack(">I", code.encode("ascii"))[0]


def fourcc_str(value: int) -> str:
    """Render an OSStatus as its four-char code when it is one, else a number."""
    try:
        raw = struct.pack(">i", int(value))
    except (struct.error, ValueError, TypeError):
        return str(value)
    if all(32 <= b < 127 for b in raw):
        return raw.decode("ascii")
    return str(value)


# --- selectors, scopes, keys (verified against the CoreAudio SDK headers) --- #
kAudioObjectSystemObject = 1
kAudioObjectPropertyScopeGlobal = fourcc("glob")
kAudioObjectPropertyScopeInput = fourcc("inpt")
kAudioObjectPropertyScopeOutput = fourcc("outp")
kAudioObjectPropertyElementMain = 0

kAudioHardwarePropertyProcessObjectList = fourcc("prs#")
kAudioHardwarePropertyTranslatePIDToProcessObject = fourcc("id2p")
kAudioHardwarePropertyDefaultOutputDevice = fourcc("dOut")
kAudioHardwarePropertyDefaultInputDevice = fourcc("dIn ")

kAudioProcessPropertyPID = fourcc("ppid")
kAudioProcessPropertyBundleID = fourcc("pbid")
kAudioProcessPropertyIsRunningInput = fourcc("piri")
kAudioProcessPropertyIsRunningOutput = fourcc("piro")

kAudioTapPropertyUID = fourcc("tuid")
kAudioTapPropertyFormat = fourcc("tfmt")

kAudioDevicePropertyDeviceUID = fourcc("uid ")
kAudioDevicePropertyStreamFormat = fourcc("sfmt")
kAudioDevicePropertyBufferFrameSize = fourcc("fsiz")

# Aggregate-device description keys are plain strings, not four-char codes.
kAudioAggregateDeviceUIDKey = "uid"
kAudioAggregateDeviceNameKey = "name"
kAudioAggregateDeviceMainSubDeviceKey = "master"
kAudioAggregateDeviceIsPrivateKey = "private"
kAudioAggregateDeviceTapListKey = "taps"
kAudioAggregateDeviceTapAutoStartKey = "tapautostart"
kAudioSubTapUIDKey = "uid"
kAudioSubTapDriftCompensationKey = "drift"

kAudioFormatFlagIsNonInterleaved = 1 << 5

MAC_TAP_MIN_VERSION = (14, 2)


class AudioObjectPropertyAddress(Structure):
    _fields_ = [
        ("mSelector", c_uint32),
        ("mScope", c_uint32),
        ("mElement", c_uint32),
    ]


class AudioStreamBasicDescription(Structure):
    _fields_ = [
        ("mSampleRate", c_double),
        ("mFormatID", c_uint32),
        ("mFormatFlags", c_uint32),
        ("mBytesPerPacket", c_uint32),
        ("mFramesPerPacket", c_uint32),
        ("mBytesPerFrame", c_uint32),
        ("mChannelsPerFrame", c_uint32),
        ("mBitsPerChannel", c_uint32),
        ("mReserved", c_uint32),
    ]


class AudioBuffer(Structure):
    _fields_ = [
        ("mNumberChannels", c_uint32),
        ("mDataByteSize", c_uint32),
        ("mData", c_void_p),
    ]


class AudioBufferList(Structure):
    # Declared with one buffer; a list with more is read via pointer arithmetic
    # on the mBuffers address, which is what the C macro does too.
    _fields_ = [
        ("mNumberBuffers", c_uint32),
        ("mBuffers", AudioBuffer * 1),
    ]


AudioDeviceIOProc = ctypes.CFUNCTYPE(
    c_int32,        # OSStatus
    c_uint32,       # AudioObjectID inDevice
    c_void_p,       # const AudioTimeStamp* inNow
    c_void_p,       # const AudioBufferList* inInputData
    c_void_p,       # const AudioTimeStamp* inInputTime
    c_void_p,       # AudioBufferList* outOutputData
    c_void_p,       # const AudioTimeStamp* inOutputTime
    c_void_p,       # void* inClientData
)

_coreaudio.AudioObjectGetPropertyDataSize.argtypes = [
    c_uint32, POINTER(AudioObjectPropertyAddress), c_uint32, c_void_p, POINTER(c_uint32)
]
_coreaudio.AudioObjectGetPropertyDataSize.restype = c_int32
_coreaudio.AudioObjectGetPropertyData.argtypes = [
    c_uint32, POINTER(AudioObjectPropertyAddress), c_uint32, c_void_p,
    POINTER(c_uint32), c_void_p
]
_coreaudio.AudioObjectGetPropertyData.restype = c_int32
_coreaudio.AudioObjectSetPropertyData.argtypes = [
    c_uint32, POINTER(AudioObjectPropertyAddress), c_uint32, c_void_p, c_uint32, c_void_p
]
_coreaudio.AudioObjectSetPropertyData.restype = c_int32

_cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, c_uint32]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFRelease.argtypes = [c_void_p]
_cf.CFRelease.restype = None
_kCFStringEncodingUTF8 = 0x08000100

_libsystem.proc_pidpath.argtypes = [c_int32, c_void_p, c_uint32]
_libsystem.proc_pidpath.restype = c_int32


class CoreAudioError(RuntimeError):
    """A Core Audio call returned a non-zero OSStatus."""

    def __init__(self, what: str, status: int):
        super().__init__(f"{what} failed: {fourcc_str(status)}")
        self.status = status


def has_tap_api() -> bool:
    """Whether this system's CoreAudio exposes the macOS 14.2 process-tap API."""
    return hasattr(_coreaudio, "AudioHardwareCreateProcessTap")


def _address(selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress:
    return AudioObjectPropertyAddress(selector, scope, kAudioObjectPropertyElementMain)


# --------------------------------------------------------------------------- #
# Property reads
# --------------------------------------------------------------------------- #
def get_uint32(obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> int | None:
    addr = _address(selector, scope)
    out = c_uint32(0)
    size = c_uint32(sizeof(out))
    status = _coreaudio.AudioObjectGetPropertyData(obj, byref(addr), 0, None, byref(size), byref(out))
    return None if status != 0 else int(out.value)


def get_string(obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> str | None:
    """Read a CFStringRef property and copy it out as a Python str."""
    addr = _address(selector, scope)
    ref = c_void_p()
    size = c_uint32(sizeof(ref))
    status = _coreaudio.AudioObjectGetPropertyData(obj, byref(addr), 0, None, byref(size), byref(ref))
    if status != 0 or not ref.value:
        return None
    try:
        buf = ctypes.create_string_buffer(1024)
        if not _cf.CFStringGetCString(ref, buf, len(buf), _kCFStringEncodingUTF8):
            return None
        return buf.value.decode("utf-8", "replace")
    finally:
        # The property returns a +1 reference; not releasing it leaks one
        # CFString per poll, and this runs twice a second forever.
        _cf.CFRelease(ref)


def get_uint32_list(obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> list[int]:
    addr = _address(selector, scope)
    size = c_uint32(0)
    if _coreaudio.AudioObjectGetPropertyDataSize(obj, byref(addr), 0, None, byref(size)) != 0:
        return []
    count = size.value // sizeof(c_uint32)
    if count <= 0:
        return []
    buf = (c_uint32 * count)()
    if _coreaudio.AudioObjectGetPropertyData(obj, byref(addr), 0, None, byref(size), byref(buf)) != 0:
        return []
    return [int(v) for v in buf]


def get_format(obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> AudioStreamBasicDescription | None:
    addr = _address(selector, scope)
    asbd = AudioStreamBasicDescription()
    size = c_uint32(sizeof(asbd))
    status = _coreaudio.AudioObjectGetPropertyData(obj, byref(addr), 0, None, byref(size), byref(asbd))
    return None if status != 0 else asbd


def set_uint32(obj: int, selector: int, value: int, scope: int = kAudioObjectPropertyScopeGlobal) -> bool:
    addr = _address(selector, scope)
    out = c_uint32(value)
    status = _coreaudio.AudioObjectSetPropertyData(obj, byref(addr), 0, None, sizeof(out), byref(out))
    return status == 0


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #
def process_objects() -> list[int]:
    """AudioObjectIDs for every process Core Audio knows about."""
    return get_uint32_list(kAudioObjectSystemObject, kAudioHardwarePropertyProcessObjectList)


def translate_pid(pid: int) -> int | None:
    """PID -> process AudioObjectID. The tap exclusion list wants these, not PIDs."""
    addr = _address(kAudioHardwarePropertyTranslatePIDToProcessObject)
    qualifier = c_int32(pid)
    out = c_uint32(0)
    size = c_uint32(sizeof(out))
    status = _coreaudio.AudioObjectGetPropertyData(
        kAudioObjectSystemObject, byref(addr), sizeof(qualifier), byref(qualifier),
        byref(size), byref(out),
    )
    return None if status != 0 or not out.value else int(out.value)


def proc_path(pid: int) -> str | None:
    """Executable path of a PID, or None. Needs no special privilege."""
    buf = (c_char * 4096)()
    written = _libsystem.proc_pidpath(pid, byref(buf), sizeof(buf))
    if written <= 0:
        return None
    return buf.value.decode("utf-8", "replace")


def default_device(input_side: bool = False) -> int | None:
    selector = (
        kAudioHardwarePropertyDefaultInputDevice if input_side
        else kAudioHardwarePropertyDefaultOutputDevice
    )
    device = get_uint32(kAudioObjectSystemObject, selector)
    return device or None


def device_uid(device: int) -> str | None:
    return get_string(device, kAudioDevicePropertyDeviceUID)


# --------------------------------------------------------------------------- #
# CoreFoundation object construction
#
# The aggregate-device description is a CFDictionary, and CATapDescription's
# initialiser takes an NSArray. Both are built here with the plain C API —
# CF types are toll-free bridged to their NS counterparts, so this needs no
# Objective-C runtime beyond the handful of message sends below, and therefore
# no PyObjC dependency in the standalone install.
# --------------------------------------------------------------------------- #
_cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
_cf.CFStringCreateWithCString.restype = c_void_p
_cf.CFNumberCreate.argtypes = [c_void_p, ctypes.c_long, c_void_p]
_cf.CFNumberCreate.restype = c_void_p
_cf.CFArrayCreate.argtypes = [c_void_p, c_void_p, ctypes.c_long, c_void_p]
_cf.CFArrayCreate.restype = c_void_p
_cf.CFDictionaryCreate.argtypes = [
    c_void_p, c_void_p, c_void_p, ctypes.c_long, c_void_p, c_void_p
]
_cf.CFDictionaryCreate.restype = c_void_p

_kCFTypeArrayCallBacks = c_void_p.in_dll(_cf, "kCFTypeArrayCallBacks")
_kCFTypeDictionaryKeyCallBacks = c_void_p.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")
_kCFTypeDictionaryValueCallBacks = c_void_p.in_dll(_cf, "kCFTypeDictionaryValueCallBacks")
_kCFBooleanTrue = c_void_p.in_dll(_cf, "kCFBooleanTrue")
_kCFBooleanFalse = c_void_p.in_dll(_cf, "kCFBooleanFalse")
_kCFNumberSInt32Type = 3


class _CFPool:
    """Every CF object made for one call, released together afterwards.

    CFDictionaryCreate retains what it stores, so the intermediates only have
    to outlive the constructing call — tracking them individually would be all
    bookkeeping and no benefit.
    """

    def __init__(self):
        self._objects: list[int] = []

    def _keep(self, ref):
        if ref:
            self._objects.append(ref)
        return ref

    def string(self, value: str):
        return self._keep(_cf.CFStringCreateWithCString(
            None, value.encode("utf-8"), _kCFStringEncodingUTF8
        ))

    def number(self, value: int):
        raw = c_uint32(int(value))
        return self._keep(_cf.CFNumberCreate(None, _kCFNumberSInt32Type, byref(raw)))

    def array(self, items: list):
        buf = (c_void_p * len(items))(*[c_void_p(i) for i in items])
        return self._keep(_cf.CFArrayCreate(
            None, buf, len(items), byref(_kCFTypeArrayCallBacks)
        ))

    def value(self, value):
        if isinstance(value, bool):
            return (_kCFBooleanTrue if value else _kCFBooleanFalse).value
        if isinstance(value, int):
            return self.number(value)
        if isinstance(value, str):
            return self.string(value)
        if isinstance(value, dict):
            return self.dictionary(value)
        if isinstance(value, (list, tuple)):
            return self.array([self.value(v) for v in value])
        raise TypeError(f"cannot bridge {type(value).__name__} to CoreFoundation")

    def dictionary(self, mapping: dict):
        keys = [self.string(k) for k in mapping]
        values = [self.value(v) for v in mapping.values()]
        kbuf = (c_void_p * len(keys))(*[c_void_p(k) for k in keys])
        vbuf = (c_void_p * len(values))(*[c_void_p(v) for v in values])
        return self._keep(_cf.CFDictionaryCreate(
            None, kbuf, vbuf, len(keys),
            byref(_kCFTypeDictionaryKeyCallBacks), byref(_kCFTypeDictionaryValueCallBacks),
        ))

    def release(self) -> None:
        for ref in self._objects:
            try:
                _cf.CFRelease(ref)
            except Exception:
                pass
        self._objects.clear()


# --------------------------------------------------------------------------- #
# The one Objective-C class we need: CATapDescription has no C constructor.
# --------------------------------------------------------------------------- #
_objc.objc_getClass.argtypes = [ctypes.c_char_p]
_objc.objc_getClass.restype = c_void_p
_objc.sel_registerName.argtypes = [ctypes.c_char_p]
_objc.sel_registerName.restype = c_void_p
_MSG_SEND = ctypes.cast(_objc.objc_msgSend, c_void_p).value


def _msg(restype, argtypes, obj, selector: str, *args):
    """One objc_msgSend, typed.

    A fresh function pointer per signature rather than mutating the shared
    objc_msgSend argtypes — on arm64 the calling convention depends on the
    exact argument types, so a stale signature is a crash, not a wrong answer.
    """
    proto = ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)
    return proto(_MSG_SEND)(obj, _objc.sel_registerName(selector.encode("ascii")), *args)


def make_tap_description(exclude_objects: list[int], name: str) -> tuple[int, str]:
    """A private, stereo, system-wide tap description excluding some processes.

    ``exclude_objects`` are process *AudioObjectIDs* (see translate_pid), not
    PIDs. Returns (description pointer, tap UUID string); the caller owns the
    description and must release it with ``release_object``.
    """
    cls = _objc.objc_getClass(b"CATapDescription")
    if not cls:
        raise CoreAudioError("CATapDescription lookup", -1)
    pool = _CFPool()
    try:
        excluded = pool.array([pool.number(o) for o in exclude_objects])
        desc = _msg(c_void_p, [c_void_p], _msg(c_void_p, [], cls, "alloc"),
                    "initStereoGlobalTapButExcludeProcesses:", c_void_p(excluded))
        if not desc:
            raise CoreAudioError("CATapDescription init", -1)
        _msg(None, [c_void_p], desc, "setName:", c_void_p(pool.string(name)))
        # Private keeps the tap out of Audio MIDI Setup, where it could be
        # picked as a system device. The mute behaviour is left at its default
        # so the user still hears the call while it is being recorded.
        _msg(None, [ctypes.c_bool], desc, "setPrivate:", True)
        uuid_ref = _msg(c_void_p, [], _msg(c_void_p, [], desc, "UUID"), "UUIDString")
        buf = ctypes.create_string_buffer(256)
        if not _cf.CFStringGetCString(c_void_p(uuid_ref), buf, len(buf), _kCFStringEncodingUTF8):
            raise CoreAudioError("CATapDescription UUID", -1)
        return int(desc), buf.value.decode("ascii")
    finally:
        pool.release()


def release_object(obj: int) -> None:
    if obj:
        try:
            _msg(None, [], c_void_p(obj), "release")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Taps, aggregate devices, IO procs — the macOS 14.2 capture path
# --------------------------------------------------------------------------- #
if has_tap_api():
    _coreaudio.AudioHardwareCreateProcessTap.argtypes = [c_void_p, POINTER(c_uint32)]
    _coreaudio.AudioHardwareCreateProcessTap.restype = c_int32
    _coreaudio.AudioHardwareDestroyProcessTap.argtypes = [c_uint32]
    _coreaudio.AudioHardwareDestroyProcessTap.restype = c_int32

_coreaudio.AudioHardwareCreateAggregateDevice.argtypes = [c_void_p, POINTER(c_uint32)]
_coreaudio.AudioHardwareCreateAggregateDevice.restype = c_int32
_coreaudio.AudioHardwareDestroyAggregateDevice.argtypes = [c_uint32]
_coreaudio.AudioHardwareDestroyAggregateDevice.restype = c_int32
_coreaudio.AudioDeviceCreateIOProcID.argtypes = [
    c_uint32, AudioDeviceIOProc, c_void_p, POINTER(c_void_p)
]
_coreaudio.AudioDeviceCreateIOProcID.restype = c_int32
_coreaudio.AudioDeviceDestroyIOProcID.argtypes = [c_uint32, c_void_p]
_coreaudio.AudioDeviceDestroyIOProcID.restype = c_int32
_coreaudio.AudioDeviceStart.argtypes = [c_uint32, c_void_p]
_coreaudio.AudioDeviceStart.restype = c_int32
_coreaudio.AudioDeviceStop.argtypes = [c_uint32, c_void_p]
_coreaudio.AudioDeviceStop.restype = c_int32


def create_process_tap(description: int) -> int:
    """Create the tap. **This is the call TCC gates** — a denied permission
    surfaces here as a non-zero status, not as silence."""
    if not has_tap_api():
        raise CoreAudioError("AudioHardwareCreateProcessTap (needs macOS 14.2+)", -1)
    tap = c_uint32(0)
    status = _coreaudio.AudioHardwareCreateProcessTap(c_void_p(description), byref(tap))
    if status != 0 or not tap.value:
        raise CoreAudioError("AudioHardwareCreateProcessTap", status)
    return int(tap.value)


def destroy_process_tap(tap: int) -> None:
    if tap and has_tap_api():
        _coreaudio.AudioHardwareDestroyProcessTap(tap)


def create_aggregate_device(description: dict) -> int:
    pool = _CFPool()
    try:
        device = c_uint32(0)
        status = _coreaudio.AudioHardwareCreateAggregateDevice(
            c_void_p(pool.dictionary(description)), byref(device)
        )
        if status != 0 or not device.value:
            raise CoreAudioError("AudioHardwareCreateAggregateDevice", status)
        return int(device.value)
    finally:
        pool.release()


def destroy_aggregate_device(device: int) -> None:
    if device:
        _coreaudio.AudioHardwareDestroyAggregateDevice(device)


def create_io_proc(device: int, callback) -> c_void_p:
    proc_id = c_void_p()
    status = _coreaudio.AudioDeviceCreateIOProcID(device, callback, None, byref(proc_id))
    if status != 0 or not proc_id.value:
        raise CoreAudioError("AudioDeviceCreateIOProcID", status)
    return proc_id


def destroy_io_proc(device: int, proc_id) -> None:
    if proc_id:
        _coreaudio.AudioDeviceDestroyIOProcID(device, proc_id)


def start_device(device: int, proc_id) -> None:
    status = _coreaudio.AudioDeviceStart(device, proc_id)
    if status != 0:
        raise CoreAudioError("AudioDeviceStart", status)


def stop_device(device: int, proc_id) -> None:
    if proc_id:
        _coreaudio.AudioDeviceStop(device, proc_id)


def buffer_list_chunks(buffer_list_ptr: int) -> list[tuple[int, bytes]]:
    """Copy an AudioBufferList out as [(channels, bytes), ...].

    Called from the real-time IO thread, so it does the minimum: one
    ``string_at`` per buffer and no interpretation of the samples.
    """
    if not buffer_list_ptr:
        return []
    header = AudioBufferList.from_address(buffer_list_ptr)
    count = int(header.mNumberBuffers)
    if count <= 0:
        return []
    base = buffer_list_ptr + AudioBufferList.mBuffers.offset
    out = []
    for i in range(count):
        buf = AudioBuffer.from_address(base + i * sizeof(AudioBuffer))
        if buf.mDataByteSize and buf.mData:
            out.append((int(buf.mNumberChannels),
                        ctypes.string_at(buf.mData, buf.mDataByteSize)))
    return out
