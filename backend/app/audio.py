import logging
import os
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path
from threading import Lock

from fastapi import HTTPException

log = logging.getLogger(__name__)
processing = Lock()
MAX_BYTES = 100 * 1024 * 1024
SAMPLE_RATE = 16_000
MAX_SECONDS = 60 * 60
FORMATS = {".mp3": "mp3", ".wav": "wav", ".m4a": "m4a", ".mp4": "mp4"}


def decode(file, extension):
    import av
    import numpy as np

    samples = []
    count = 0
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)

    def collect(frames):
        nonlocal count
        for frame in frames:
            count += frame.samples
            if count > MAX_SECONDS * SAMPLE_RATE:
                raise HTTPException(422, "Recordings must be at most 60 minutes long.")
            samples.append(frame.to_ndarray().reshape(-1))

    try:
        with av.open(file, mode="r") as source:
            if FORMATS[extension] not in source.format.name.split(",") or not source.streams.audio:
                raise HTTPException(422, "The audio contents do not match the selected file format.")
            for frame in source.decode(audio=0):
                collect(resampler.resample(frame))
            collect(resampler.resample(None))
    except av.FFmpegError as error:
        raise HTTPException(422, "This recording could not be decoded.") from error
    if not count:
        raise HTTPException(422, "The file contains no audio samples.")
    return np.concatenate(samples)


@lru_cache(maxsize=1)
def whisper():
    from faster_whisper import WhisperModel

    return WhisperModel(
        os.getenv("PROMISE_KEEPER_WHISPER_MODEL", "small"),
        device="cpu",
        compute_type="int8",
        cpu_threads=min(4, os.cpu_count() or 1),
        download_root=str(Path(__file__).resolve().parents[1] / "data" / "models"),
    )


def diarization_available():
    configured = os.getenv("HF_TOKEN") or os.getenv("PROMISE_KEEPER_DIARIZATION_MODEL")
    return bool(configured and find_spec("pyannote") and find_spec("pyannote.audio"))


@lru_cache(maxsize=1)
def speaker_pipeline():
    from pyannote.audio import Pipeline

    return Pipeline.from_pretrained(
        os.getenv("PROMISE_KEEPER_DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1"),
        token=os.getenv("HF_TOKEN"),
    )


def speaker_for(start, end, turns):
    best_speaker = None
    best_overlap = 0
    for turn_start, turn_end, speaker in turns:
        if turn_end <= start:
            continue
        if turn_start >= end:
            break
        overlap = min(end, turn_end) - max(start, turn_start)
        if overlap > best_overlap:
            best_speaker, best_overlap = speaker, overlap
    return best_speaker


def transcribe(file, extension, diarize, *, language=None):
    if find_spec("faster_whisper") is None:
        raise HTTPException(503, "Install the audio dependencies listed in the README before transcribing.")
    if diarize and not diarization_available():
        raise HTTPException(503, "Speaker detection is not configured. See the optional setup in the README.")
    if not processing.acquire(blocking=False):
        raise HTTPException(409, "A recording is already processing. Try again when it finishes.")
    try:
        waveform = decode(file, extension)
        try:
            segments, _ = whisper().transcribe(waveform, vad_filter=True, language=language)
            result = [
                {"start": segment.start, "end": segment.end, "text": segment.text.strip(), "speaker": None}
                for segment in segments if segment.text.strip()
            ]
        except Exception as error:
            log.exception("Local transcription failed")
            raise HTTPException(503, "Transcription failed. Check the Whisper model setup and backend log.") from error
        if not result:
            raise HTTPException(422, "No speech was detected in the recording.")
        if diarize:
            try:
                import torch

                output = speaker_pipeline()({"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": SAMPLE_RATE})
                turns = sorted((turn.start, turn.end, speaker) for turn, speaker in output.exclusive_speaker_diarization)
                for segment in result:
                    segment["speaker"] = speaker_for(segment["start"], segment["end"], turns)
            except Exception as error:
                log.exception("Speaker detection failed")
                raise HTTPException(503, "Speaker detection failed. Check model access or retry with speaker detection off.") from error
        return result
    finally:
        processing.release()
