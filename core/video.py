"""Read local video metadata and sample display-oriented JPEGs through PyAV.

Only derived images are encoded. Input media is opened read-only, with no CLI
processes. Native imports are lazy so photo-only use does not require PyAV.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import math
import random
import re
import struct

from PIL import Image

from strings import T
from .timestamps import parse_dt


_GPS = re.compile(
    r'^\s*([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)'
    r'(?:[+-]\d+(?:\.\d+)?)?/?\s*$')
_TIME_KEYS = ('creation_time', 'com.apple.quicktime.creationdate', 'date')
_GPS_KEYS = ('location', 'location-eng', 'com.apple.quicktime.location.iso6709')


def _av_module():
    try:
        import av
    except ImportError as error:
        raise RuntimeError(T('无法加载 PyAV，请安装 requirements-media.txt 中的依赖。')) from error
    return av


@contextmanager
def _open(path):
    # A Python file object also keeps URLs from being interpreted as inputs.
    av = _av_module()
    with open(path, 'rb') as source, av.open(source, mode='r') as container:
        yield container


def _video_stream(container):
    videos = [s for s in container.streams.video
              if not s.disposition & s.disposition.attached_pic]
    return max(videos, key=lambda s: (s.width * s.height,
               bool(s.disposition & s.disposition.default), -s.index), default=None)


def _duration(container, stream):
    av = _av_module()
    values = [container.duration / av.time_base if container.duration is not None else 0]
    if stream is not None and stream.duration is not None and stream.time_base is not None:
        values.append(float(stream.duration * stream.time_base))
    return next((float(v) for v in values if math.isfinite(v) and v > 0), 0.0)


def _metadata(container, stream):
    sources = [container.metadata]
    if stream is not None:
        sources.append(stream.metadata)
    creation, gps = None, None
    for source in sources:
        tags = {key.lower(): str(value) for key, value in source.items()}
        if creation is None:
            for key in _TIME_KEYS:
                for value in tags.get(key, '').split(';'):
                    if parse_dt(value.strip()) is not None:
                        creation = value.strip()
                        break
                if creation is not None:
                    break
        if gps is None:
            for key in _GPS_KEYS:
                match = _GPS.fullmatch(tags.get(key, ''))
                if match:
                    lat, lon = map(float, match.groups())
                    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
                        gps = lat, lon
                        break
    return creation, gps


def probe(path: str) -> dict:
    """Container duration in seconds, capture-time string and decimal GPS pair.

    Container tags take priority over the selected video stream. Invalid media
    raises; callers can retain their existing filename/manual-time fallback.
    """
    with _open(path) as container:
        stream = _video_stream(container)
        creation, gps = _metadata(container, stream)
        return dict(dur=_duration(container, stream), creation=creation, gps=gps)


def _origin(container, stream):
    if stream.start_time is not None and stream.time_base is not None:
        return float(stream.start_time * stream.time_base)
    if container.start_time is not None:
        return container.start_time / _av_module().time_base
    return None


def _orientation(frame, stream):
    """Pillow transpose for the eight orthogonal display orientations, or angle."""
    matrix = frame.side_data.get('DISPLAYMATRIX')
    if matrix is not None:
        values = struct.unpack('=9i', bytes(matrix))
        a, b, c, d = (values[i] / 65536 for i in (0, 1, 3, 4))
        key = tuple(round(v) for v in (a, b, c, d))
        if all(abs(value - integer) < .001 for value, integer in zip((a, b, c, d), key)):
            operations = {
                (1, 0, 0, 1): None,
                (0, -1, 1, 0): Image.Transpose.ROTATE_90,
                (-1, 0, 0, -1): Image.Transpose.ROTATE_180,
                (0, 1, -1, 0): Image.Transpose.ROTATE_270,
                (-1, 0, 0, 1): Image.Transpose.FLIP_LEFT_RIGHT,
                (1, 0, 0, -1): Image.Transpose.FLIP_TOP_BOTTOM,
                (0, 1, 1, 0): Image.Transpose.TRANSPOSE,
                (0, -1, -1, 0): Image.Transpose.TRANSVERSE,
            }
            if key in operations:
                return operations[key], 0
        return None, frame.rotation
    try:
        return None, float(stream.metadata.get('rotate', 0))
    except (TypeError, ValueError):
        return None, 0


def _jpeg(frame, stream, width):
    operation, angle = _orientation(frame, stream)
    sar = float(stream.sample_aspect_ratio or 1)
    if not math.isfinite(sar) or sar <= 0:
        sar = 1.0
    w, h = frame.width * sar, frame.height
    swaps_axes = operation in (Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_270,
                              Image.Transpose.TRANSPOSE, Image.Transpose.TRANSVERSE)
    radians = math.radians(angle)
    display_width = h if swaps_axes else abs(w * math.cos(radians)) + abs(h * math.sin(radians))
    scale = min(1.0, width / max(1, display_width))
    image = frame.to_image(width=max(1, round(w * scale)), height=max(1, round(h * scale)))
    if operation is not None:
        image = image.transpose(operation)
    elif angle:
        image = image.rotate(angle, expand=True)
    if image.width > width:
        image = image.resize((width, max(1, round(image.height * width / image.width))), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=90)
    return buffer.getvalue()


def extract_keyframes(path, count, width, dur):
    """Decode only keyframes and select candidates across the whole timeline.

    Retain O(count) JPEGs, regardless of video length. With unknown duration,
    deterministic reservoir sampling covers the stream without storing all frames.
    Duplicate candidates are left for pick_frames to remove as before.
    """
    if count <= 0 or width <= 0:
        return []
    av = _av_module()
    samples = []
    try:
        with _open(path) as container:
            stream = _video_stream(container)
            if stream is None:
                return []
            origin = _origin(container, stream)
            stream.codec_context.skip_frame = 'NONKEY'
            duration = dur if dur and math.isfinite(dur) and dur > 0 else _duration(container, stream)
            targets = [duration * i / max(1, count - 1) for i in range(count)]
            slots, distances = [None] * count, [math.inf] * count
            rng = random.Random(0)
            for index, frame in enumerate(container.decode(stream)):
                if origin is None and frame.time is not None:
                    origin = float(frame.time)
                timestamp = float(frame.time) - origin if frame.time is not None else None
                if duration > 0 and timestamp is not None:
                    updates = [i for i, target in enumerate(targets) if abs(timestamp - target) < distances[i]]
                    if updates:
                        data = _jpeg(frame, stream, width)
                        for i in updates:
                            slots[i], distances[i] = (timestamp, data), abs(timestamp - targets[i])
                else:
                    slot = index if index < count else rng.randrange(index + 1)
                    if slot < count:
                        value = (index, _jpeg(frame, stream, width))
                        if len(samples) < count:
                            samples.append(value)
                        else:
                            samples[slot] = value
            samples = [slot for slot in slots if slot is not None] or samples
    except (OSError, ValueError, av.FFmpegError):
        # Opening/decoding failures trigger the existing seek fallback.
        return []
    return [data for _, data in sorted(samples, key=lambda sample: sample[0])]


def extract_seek(path, positions, width):
    """Seek backwards to a keyframe, then decode to each requested relative time.

    Reuse one container, preserve requested order and account for nonzero stream
    start timestamps. Positions beyond EOF produce no frame. Failed positions do
    not prevent sampling the remaining positions.
    """
    if width <= 0:
        return []
    av = _av_module()
    out = []
    try:
        with _open(path) as container:
            stream = _video_stream(container)
            if stream is None:
                return []
            origin = _origin(container, stream)
            if origin is None:
                first = next(container.decode(stream), None)
                origin = float(first.time) if first is not None and first.time is not None else 0.0
            for sec in positions:
                if not math.isfinite(sec):
                    continue
                target = origin + max(0.0, sec)
                try:
                    container.seek(int(target / stream.time_base), stream=stream, backward=True)
                    for frame in container.decode(stream):
                        if frame.time is None or float(frame.time) + 1e-6 >= target:
                            out.append(_jpeg(frame, stream, width))
                            break
                except (OSError, ValueError, av.FFmpegError):
                    continue
    except (OSError, ValueError, av.FFmpegError):
        pass
    return out
