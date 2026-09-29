"""Local speech-to-text with faster-whisper. Audio never leaves the server and is
deleted right after transcription.

    WHISPER_MODEL        tiny | base | small (default) | medium
    WHISPER_LANGUAGE     e.g. "es"; empty = detect per recording
    WHISPER_MODELS_DIR   where models are downloaded on first use (default /models)
"""

import os
import threading
from typing import Protocol


class VoiceUnavailable(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, path: str) -> str: ...


class WhisperTranscriber:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()  # one transcription at a time on a small CPU

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceUnavailable("voice support is not installed (pip install '.[voice]')") from exc
            self._model = WhisperModel(
                os.getenv("WHISPER_MODEL", "small"),
                device="cpu",
                compute_type="int8",
                download_root=os.getenv("WHISPER_MODELS_DIR", "/models"),
            )
        return self._model

    def transcribe(self, path: str) -> str:
        with self._lock:
            segments, _ = self._load().transcribe(
                path,
                language=os.getenv("WHISPER_LANGUAGE") or None,
                vad_filter=True,  # skip silence
                beam_size=1,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()


_transcriber: WhisperTranscriber | None = None


def get_transcriber() -> Transcriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = WhisperTranscriber()
    return _transcriber
