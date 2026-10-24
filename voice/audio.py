"""
Audio I/O for the Live API voice session.

Uses `sounddevice` (cross-platform, numpy-friendly, easier on Windows than PyAudio).

- MicStream: captures 16 kHz mono 16-bit PCM from the default input device
  and pushes raw bytes onto an asyncio.Queue.
- SpeakerPlayer: plays back 24 kHz mono 16-bit PCM chunks received from Gemini
  on a background thread, with barge-in support (flush on interrupt).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from config import LIVE

# Lazy import — sounddevice pulls native deps that aren't needed unless voice
# mode is actually used. Keeps `import main` working without audio libs.
try:
    import numpy as np
    import sounddevice as sd
    _AUDIO_OK = True
    _AUDIO_IMPORT_ERR: Optional[Exception] = None
except Exception as e:  # pragma: no cover
    np = None  # type: ignore
    sd = None  # type: ignore
    _AUDIO_OK = False
    _AUDIO_IMPORT_ERR = e

logger = logging.getLogger(__name__)

CHANNELS = 1
DTYPE = "int16"
MIC_BLOCK_MS = 20   # 20ms chunks -> 320 samples @ 16 kHz = low-latency streaming


class MicStream:
    """Async microphone capture -> asyncio.Queue[bytes] of 16-bit PCM."""

    def __init__(self, sample_rate: Optional[int] = None):
        self.sample_rate = sample_rate or LIVE["input_sample_rate"]
        self.block_size = int(self.sample_rate * MIC_BLOCK_MS / 1000)
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream: Optional[sd.InputStream] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

    def _callback(self, indata, frames, time, status):
        if status:
            logger.debug(f"Mic status: {status}")
        if not self._running or self._loop is None:
            return
        # indata is an int16 numpy array; convert to bytes and hand to the event loop
        pcm = bytes(indata)
        try:
            asyncio.run_coroutine_threadsafe(self.queue.put(pcm), self._loop)
        except RuntimeError:
            pass

    def start(self):
        if self._running:
            return
        if not _AUDIO_OK:
            raise RuntimeError(
                f"Audio libraries not installed: {_AUDIO_IMPORT_ERR}. "
                "Install with: pip install sounddevice numpy"
            )
        self._loop = asyncio.get_event_loop()
        self._running = True
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=self.block_size,
                callback=self._callback,
            )
            self._stream.start()
            logger.info(f"Mic started @ {self.sample_rate} Hz")
        except Exception as e:
            self._running = False
            logger.error(f"Failed to start microphone: {e}")
            raise

    def stop(self):
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("Mic stopped")


class SpeakerPlayer:
    """
    Callback-driven speaker player for 16-bit PCM chunks with a jitter buffer.

    The Live API emits PCM chunks as the model synthesizes speech — sizes and
    arrival times vary with network jitter. If we wrote each chunk directly to
    the output device, the sound card's buffer would drain to zero between
    chunks and playback would sound choppy / gapped.

    Instead:
    - `play()` appends bytes to an internal ring buffer.
    - sounddevice runs a callback that pulls samples from the buffer every tick.
    - Playback only starts once `prebuffer_ms` of audio has accumulated, which
      absorbs normal network jitter (~50–150 ms typical).
    - If the buffer underruns mid-sentence, we output silence (no glitch) and
      wait for `prebuffer_ms` again before resuming — prevents stuttering.
    - `flush()` clears everything and forces another pre-buffer on next audio
      (used on barge-in / interrupt).
    """

    def __init__(self, sample_rate: Optional[int] = None, prebuffer_ms: int = 200):
        self.sample_rate = sample_rate or LIVE["output_sample_rate"]
        self._prebuffer_bytes = int(self.sample_rate * prebuffer_ms / 1000) * 2  # int16 mono
        self._prebuffer_ms = prebuffer_ms
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream: Optional[sd.OutputStream] = None
        self._running = False
        # Flip to True once we've accumulated enough to start playing.
        # Resets to False on flush() or full underrun.
        self._playing = False

    def _callback(self, outdata, frames, _time, status):
        if status:
            logger.debug(f"Speaker status: {status}")

        needed = frames * 2  # bytes (int16 mono)
        with self._lock:
            if not self._playing:
                if len(self._buffer) >= self._prebuffer_bytes:
                    self._playing = True
                else:
                    outdata.fill(0)
                    return

            if len(self._buffer) >= needed:
                chunk = bytes(self._buffer[:needed])
                del self._buffer[:needed]
                outdata[:, 0] = np.frombuffer(chunk, dtype=np.int16)
            elif len(self._buffer) > 0:
                # Partial underrun — emit what we have, pad rest with silence.
                samples = np.frombuffer(bytes(self._buffer), dtype=np.int16)
                outdata[: len(samples), 0] = samples
                outdata[len(samples) :, 0] = 0
                self._buffer.clear()
                # Re-prebuffer before continuing to avoid stuttering.
                self._playing = False
            else:
                # Full underrun.
                outdata.fill(0)
                self._playing = False

    def start(self):
        if self._running:
            return
        if not _AUDIO_OK:
            raise RuntimeError(
                f"Audio libraries not installed: {_AUDIO_IMPORT_ERR}. "
                "Install with: pip install sounddevice numpy"
            )
        self._running = True
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=self._callback,
                # 'high' asks PortAudio for a larger hardware buffer — big win
                # on Windows WASAPI where default latency is often too tight.
                latency="high",
            )
            self._stream.start()
        except Exception as e:
            self._running = False
            logger.error(f"Failed to start speaker: {e}")
            raise

        logger.info(
            f"Speaker started @ {self.sample_rate} Hz "
            f"(prebuffer {self._prebuffer_ms} ms, latency=high)"
        )

    def play(self, pcm_chunk: bytes):
        """Enqueue a PCM chunk for playback."""
        if not self._running or not pcm_chunk:
            return
        with self._lock:
            self._buffer.extend(pcm_chunk)

    def flush(self):
        """Drop everything currently queued — used on barge-in / interrupt."""
        with self._lock:
            self._buffer.clear()
            self._playing = False

    def stop(self):
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            self._buffer.clear()
            self._playing = False
        logger.info("Speaker stopped")
