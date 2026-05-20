import collections
import queue
from typing import Iterator

import numpy as np
import torch
from silero_vad import load_silero_vad

import display
from config import CFG

_SILENCE = "SILENCE"
_SPEAKING = "SPEAKING"

_chunk_duration_ms = (CFG.vad_chunk_samples / CFG.sample_rate) * 1000
_lookback_maxlen = int(CFG.vad_lookback_ms / _chunk_duration_ms)


def vad_segments(chunk_queue: queue.Queue) -> Iterator[np.ndarray]:
    model = load_silero_vad()

    lookback: collections.deque = collections.deque(maxlen=_lookback_maxlen)
    state = _SILENCE
    utterance_buffer: list[np.ndarray] = []
    silence_chunks = 0

    while True:
        try:
            chunk = chunk_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if chunk is None:
            if state == _SPEAKING and utterance_buffer:
                audio = np.concatenate(utterance_buffer)
                duration_ms = len(audio) / CFG.sample_rate * 1000
                if duration_ms >= CFG.vad_min_speech_ms:
                    yield audio
            return

        tensor = torch.from_numpy(chunk).unsqueeze(0)
        speech_prob = model(tensor, CFG.sample_rate).item()

        lookback.append(chunk)

        if state == _SILENCE:
            display.silence()
            if speech_prob > CFG.vad_threshold:
                state = _SPEAKING
                utterance_buffer = list(lookback)
                silence_chunks = 0
                display.speech_start()
        else:
            display.speaking()
            utterance_buffer.append(chunk)

            if speech_prob < CFG.vad_threshold:
                silence_chunks += 1
            else:
                silence_chunks = 0

            silence_ms = silence_chunks * _chunk_duration_ms
            utterance_samples = sum(len(c) for c in utterance_buffer)
            utterance_duration_s = utterance_samples / CFG.sample_rate

            soft_split = (
                utterance_duration_s >= CFG.vad_soft_split_s
                and speech_prob < CFG.vad_soft_split_threshold
            )

            if silence_ms > CFG.vad_silence_duration_ms or soft_split or utterance_duration_s > CFG.vad_max_utterance_s:
                audio = np.concatenate(utterance_buffer)
                duration_ms = len(audio) / CFG.sample_rate * 1000
                if duration_ms >= CFG.vad_min_speech_ms:
                    yield audio
                utterance_buffer = []
                silence_chunks = 0
                state = _SILENCE
