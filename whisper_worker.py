import numpy as np
from faster_whisper import WhisperModel

import display
from config import CFG


def load_model() -> WhisperModel:
    display.loading(f"Loading Whisper {CFG.whisper_model} on {CFG.whisper_device} ({CFG.whisper_compute_type})...")
    model = WhisperModel(CFG.whisper_model, device=CFG.whisper_device, compute_type=CFG.whisper_compute_type)
    display.loading("Whisper model ready.")
    return model


def transcribe_utterance(model: WhisperModel, audio: np.ndarray, initial_prompt: str = "") -> list[str]:
    segments, _ = model.transcribe(
        audio,
        beam_size=CFG.whisper_beam_size,
        language=CFG.whisper_language,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        initial_prompt=initial_prompt or None,
    )
    texts = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            texts.append(text)
    return texts
