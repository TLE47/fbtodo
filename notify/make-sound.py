#!/usr/bin/env python3
"""Make a freebuff alert sound from a recorded note in a SoundFont.

    python3 make-sound.py [--voice piano|steel|nylon] [--seconds N] [output.wav]

Nothing here is synthesised. The script finds the sample nearest the pitch it
wants in MuseScore's "MS Basic" bank, shifts it by the semitones that remain
(varispeed, so the instrument's own character is kept), and trims it to a short,
gentle chime:

    piano   F4 — the piano's F4, from the softer "Piano MF" dynamic layer, cut
            to a 0.25 s tap: the sound of a key being pressed, not a held note
    steel   D3 — the steel-string guitar's open D
    nylon   D3 — the nylon-string guitar's open D

--seconds overrides the voice's length, so "--seconds 0.15" makes an even
lighter tap.

Without an output path it writes <voice>.wav next to this script, so the voices
live side by side and the alert can pick any of them.

SoundFonts are searched in MuseScore's app bundle and the usual user font
folders. Samples are 16-bit PCM in .sf2 and Vorbis in .sf3; ffmpeg decodes the
latter when it is available.
"""

from __future__ import annotations

import argparse
import array
import glob
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

KEEP_SECONDS = 1.0
FADE_OUT = 0.12
PEAK = 0.8
MIN_SAMPLE_BYTES = 4096
TAP_SECONDS = 0.25  # a piano key tap: pressed and released, not held
TAP_DECAY = 0.09    # seconds for the tap to lose 8.7 dB (1/e in amplitude)
TAP_FADE = 0.03     # short closing fade, just enough to end on exact silence

# "name fragment to look for" (earliest wins), "fragments that mean not this
# instrument", preferred member of a stereo pair, and the pitch we want.
VOICES = {
    "piano": {
        "fragments": ("piano mf", "piano ff"),  # mezzo-forte first: gentler
        "banned": ("electric",),
        "channel": "(l)",
        "key": 65,
        "name": "F4",
        "hz": 349.23,
        "blurb": "piano F4",
        "seconds": TAP_SECONDS,
        "decay": TAP_DECAY,
        "fade": TAP_FADE,
    },
    "steel": {
        "fragments": ("steel",),
        "banned": ("drum", "palm", "jazz", "electric", "harm", "ext"),
        "key": 50,
        "name": "D3",
        "hz": 146.83,
        "blurb": "steel-string guitar's open D",
    },
    "nylon": {
        "fragments": ("nylon",),
        "banned": ("drum", "palm", "jazz", "electric", "harm", "ext"),
        "key": 50,
        "name": "D3",
        "hz": 146.83,
        "blurb": "nylon-string guitar's open D",
    },
}
SOUNDFONTS = [
    "/Applications/MuseScore 4.app/Contents/Resources/sound/*.sf3",
    "/Applications/MuseScore 4.app/Contents/Resources/sound/*.sf2",
    "/Library/Audio/Sounds/Banks/*.sf2",
    os.path.expanduser("~/Documents/MuseScore4/SoundFonts/*.sf2"),
    os.path.expanduser("~/Library/Application Support/MuseScore/MuseScore4/SoundFonts/*.sf2"),
]
SEMITONES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(key):
    """MIDI key -> scientific pitch name, so 65 is F4 and 60 is C4."""
    return f"{SEMITONES[key % 12]}{key // 12 - 1}"


def midi_hz(key):
    return 440.0 * 2 ** ((key - 69) / 12)


def riff_chunks(data, start, end):
    position = start
    while position + 8 <= end:
        chunk_id = data[position : position + 4]
        size = struct.unpack("<I", data[position + 4 : position + 8])[0]
        yield chunk_id, position + 8, position + 8 + size
        position += 8 + size + (size & 1)


def find_sub_chunk(data, want, list_type):
    for chunk_id, body, stop in riff_chunks(data, 12, len(data)):
        if chunk_id != b"LIST" or data[body : body + 4] != list_type:
            continue
        for sub_id, sub_body, sub_stop in riff_chunks(data, body + 4, stop):
            if sub_id == want:
                return sub_body, sub_stop
    return None, None


def read_soundfont(path):
    """Return (sample headers, audio pool) for one SoundFont."""
    with open(path, "rb") as handle:
        data = handle.read()
    header, header_end = find_sub_chunk(data, b"shdr", b"pdta")
    pool, _ = find_sub_chunk(data, b"smpl", b"sdta")
    if header is None or pool is None:
        return [], b""
    samples = []
    for offset in range(header, header_end, 46):
        name, start, end, loop_start, loop_end, rate, key, corr, _link, _kind = struct.unpack(
            "<20sIIIIIBbHH", data[offset : offset + 46]
        )
        samples.append(
            (name.rstrip(b"\0").decode("utf-8", "replace"), start, end, loop_start,
             loop_end, rate, key, corr)
        )
    return samples, data[pool:]


def pick_sample(samples, voice):
    """The requested instrument's note nearest the wanted pitch."""
    for fragment in voice["fragments"]:
        best = None
        for sample in samples:
            name, start, end, _loop_start, _loop_end, _rate, key, _corr = sample
            low = name.lower()
            if fragment not in low or any(bad in low for bad in voice["banned"]):
                continue
            if end - start < MIN_SAMPLE_BYTES:
                continue
            # prefer the wanted channel of a stereo pair, then the nearest pitch
            channel = 0 if voice.get("channel") and voice["channel"] in low else 1
            score = (channel, abs(key - voice["key"]), len(name))
            if best is None or score < best[0]:
                best = (score, sample)
        if best:
            return best[1]
    return None


def to_float_pcm(payload, rate):
    """Decode a sample's bytes: raw PCM in .sf2, Ogg Vorbis in .sf3."""
    if payload[:4] != b"OggS":
        values = array.array("h")
        values.frombytes(payload[: len(payload) // 2 * 2])
        return [value / 32768.0 for value in values], rate

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("this SoundFont stores samples as Vorbis; ffmpeg is needed to decode it")
    with tempfile.TemporaryDirectory() as work:
        source = os.path.join(work, "sample.ogg")
        target = os.path.join(work, "sample.wav")
        with open(source, "wb") as handle:
            handle.write(payload)
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", source, "-ac", "1", "-c:a", "pcm_s16le", target],
            check=True,
        )
        with wave.open(target, "rb") as handle:
            decoded = array.array("h")
            decoded.frombytes(handle.readframes(handle.getnframes()))
            rate = handle.getframerate()
    return [value / 32768.0 for value in decoded], rate


def goertzel(samples, freq, rate):
    omega = 2 * math.pi * freq / rate
    coeff = 2 * math.cos(omega)
    s1 = s2 = 0.0
    for value in samples:
        s0 = value + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def detected_key(samples, rate, declared):
    """The sample's real pitch, in case the bank labels it an octave off.

    A note has no energy below its own fundamental, so if the octave below the
    declared pitch carries real power, the declared pitch was that note's second
    harmonic and the sample is really the lower one. Only ever corrected
    downwards, and only on a large margin, so a quiet rumble can't move it.
    """
    probe = samples[: int(rate * 2)]
    column = lambda key: goertzel(probe, midi_hz(key), rate)
    key = declared
    power = column(key)
    while key - 12 >= 12 and column(key - 12) >= 0.25 * max(power, 1e-12):
        key -= 12
        power = column(key)
    return key


def varispeed(samples, ratio):
    """Shift pitch by resampling: ratio > 1 reads faster, so it sounds higher."""
    if abs(ratio - 1.0) < 1e-9:
        return samples
    length = int(len(samples) / ratio)
    out = []
    for index in range(length):
        source = index * ratio
        base = int(source)
        frac = source - base
        first = samples[base] if base < len(samples) else 0.0
        second = samples[base + 1] if base + 1 < len(samples) else first
        out.append(first + frac * (second - first))
    return out


def resample(samples, rate, target=44100):
    if rate == target:
        return samples, rate
    return varispeed(samples, rate / target), target


def trim(samples, rate, seconds, fade_seconds, decay_seconds=0.0):
    keep = min(len(samples), int(rate * seconds))
    out = list(samples[:keep])
    if decay_seconds > 0:
        # MuseScore's piano sample sits at full level for its first ~150 ms (a
        # compressed plateau), so a plain cut of it reads as a held blip rather
        # than a struck key. A struck string is loudest at the attack and only
        # loses energy after that, so fit that curve before trimming.
        for index in range(len(out)):
            out[index] *= math.exp(-(index / rate) / decay_seconds)
    fade = min(int(rate * fade_seconds), len(out))
    for index in range(fade):
        # 1 -> 0 across the tail, so the note ends in silence instead of being cut
        out[-fade + index] *= 0.5 + 0.5 * math.cos(math.pi * (index + 1) / fade)
    peak = max(abs(value) for value in out) or 1.0
    return [value * PEAK / peak for value in out]


def level_db(samples):
    rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
    return 20 * math.log10(rms) if rms > 0 else -120.0


def write_wav(path, samples, rate):
    pcm = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", choices=sorted(VOICES), default="piano")
    parser.add_argument("--seconds", type=float, help="override the voice's length in seconds")
    parser.add_argument("output", nargs="?", help="default: <voice>.wav beside this script")
    args = parser.parse_args()
    voice = VOICES[args.voice]
    seconds = args.seconds or voice.get("seconds", KEEP_SECONDS)
    # a short note can't spend most of its life fading, or the attack goes too
    fade_seconds = min(voice.get("fade", FADE_OUT), seconds / 2)
    decay_seconds = voice.get("decay", 0.0)
    output = args.output or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f"{args.voice}.wav"
    )

    fonts = [path for pattern in SOUNDFONTS for path in sorted(glob.glob(pattern))]
    if not fonts:
        print("no SoundFont found; install MuseScore or pass a bank in SOUNDFONTS", file=sys.stderr)
        return 1

    for font in fonts:
        samples, pool = read_soundfont(font)
        chosen = pick_sample(samples, voice)
        if not chosen:
            continue
        name, start, end, loop_start, loop_end, rate, key, corr = chosen
        note, rate = to_float_pcm(pool[start:end], rate)
        # A SoundFont loops the sustained part; the natural note is everything up
        # to the loop, so never keep material from inside it.
        if 0 < loop_start < loop_end < len(note):
            note = note[:loop_end]

        source = detected_key(note, rate, key + corr / 100)  # corrected for cents
        shift = voice["key"] - source
        note = varispeed(note, 2 ** (shift / 12))
        note, rate = resample(note, rate)
        chime = trim(note, rate, seconds, fade_seconds, decay_seconds)
        write_wav(output, chime, rate)

        print(f"wrote {output}")
        stereo = voice.get("channel") and voice["channel"] in name.lower()
        mic = " · left mic of the stereo pair" if stereo else ""
        print(f'  source: {os.path.basename(font)} · "{name}"{mic}')
        print(f"  declared {note_name(round(key))} (key {key}), measured "
              f"{note_name(round(source))} → shifted {shift:+.2f} semitones to {voice['name']}")
        print(f"  {len(chime) / rate:.2f}s kept from {len(note) / rate:.2f}s of recorded "
              f"{voice['blurb']}, {rate} Hz mono, peak {PEAK}")
        block = rate // 20  # 50 ms
        fade_samples = int(rate * fade_seconds)
        head = level_db(chime[:block])
        tail = level_db(chime[max(0, len(chime) - fade_samples - block) : len(chime) - fade_samples])
        print(f"  decays {head - tail:.1f} dB across the note")
        # Prove the tap is percussive: struck, then falling away, not a held
        # blip. Compare short windows, not prefixes — a prefix keeps its loud
        # attack in it and hides the fall.
        size = max(1, int(len(chime) * 0.1))

        def window_db(centre):
            start = min(max(0, centre - size // 2), len(chime) - size)
            return level_db(chime[start : start + size])

        attack = window_db(size // 2)
        falls = " / ".join(
            f"{window_db(int(len(chime) * part)) - attack:+.1f}" for part in (0.25, 0.5, 0.9)
        )
        print(f"  falls {falls} dB at a quarter / halfway / near the end of the note")
        power = goertzel(chime, voice["hz"], rate)
        neighbours = [goertzel(chime, voice["hz"] * 2 ** (step / 12), rate) for step in (-1, 1)]
        print(f"  pitch check: {voice['name']} {power:.0f} vs semitone below {neighbours[0]:.0f}, "
              f"semitone above {neighbours[1]:.0f}, 2nd harmonic "
              f"{goertzel(chime, 2 * voice['hz'], rate):.0f}")
        if power > max(neighbours):
            print(f"  -> fundamental confirmed at {voice['name']} ({voice['blurb']})")
            return 0
        print(f"  -> WARNING: pitch is not centred on {voice['name']}")
        return 1

    print(f"no {'/'.join(voice['fragments'])} sample found in: "
          f"{', '.join(os.path.basename(f) for f in fonts)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
