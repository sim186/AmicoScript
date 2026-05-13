# Community Benchmarks

Performance results from real machines, contributed by AmicoScript users.

## How to Run

Open AmicoScript, expand the **Benchmark** section in the transcribe sidebar, and click **Run Benchmark**. The app will test three standard models on an 11-second reference clip and show your results. Click **Share to Community** to submit them here.

## What is RTF?

**RTF (Real-Time Factor)** = inference time ÷ audio duration.

- `0.1x` → 10× faster than real-time
- `1.0x` → matches real-time
- `2.0x` → 2× slower than real-time (needs 20 s to transcribe 10 s of audio)

Lower is faster. Anything below `1.0x` is real-time capable.

> **Note:** For accurate native results, run `python run.py` directly rather than via Docker. Docker adds a Linux VM layer that hides the underlying CPU/OS identity.

## Reference Audio

All results use the same 11 s English speech clip (JFK, public domain) from the OpenAI Whisper test suite. Model: int8 compute, beam size 5, VAD off.

---

## Results

| Date | CPU | GPU | OS | tiny RTF | small RTF | medium RTF |
|------|-----|-----|----|----------|-----------|------------|
| 2026-05-12 | Apple M4 | CPU only | macOS | 0.0586x | 0.292x | 0.9289x |

---

To add your result, run the benchmark in the app and click **Share to Community**. A pre-filled GitHub issue will open — just submit it.
