from __future__ import annotations

import math
import os
import re
import json
import logging
import subprocess
import wave
from pathlib import Path
from typing import Any

from .utils import safe_text

logger = logging.getLogger(__name__)


DEFAULT_TTS_FIELDS = {
    "use_emo_text": False,
    "use_random": False,
    "interval_silence": 100,
    "max_text_tokens_per_sentence": 100,
    "do_sample": True,
    "top_p": 0.8,
    "top_k": 10,
    "temperature": 0.5,
    "length_penalty": 0.0,
    "num_beams": 1,
    "repetition_penalty": 6.0,
    "max_mel_tokens": 1800,
    "verbose": False,
}
VOICE_PROVIDER_INDEXTTS = "indextts"
VOICE_PROVIDER_MINIMAX = "minimax"
VOICE_PROVIDER_LABELS = {
    VOICE_PROVIDER_INDEXTTS: "IndexTTS 本地服务",
    VOICE_PROVIDER_MINIMAX: "MiniMax API",
}
MINIMAX_API_BASE_URL = "https://api.minimaxi.com"
MINIMAX_T2A_URL = f"{MINIMAX_API_BASE_URL}/v1/t2a_v2"
MINIMAX_VOICE_LIST_URL = f"{MINIMAX_API_BASE_URL}/v1/get_voice"
MINIMAX_T2A_MODEL = "speech-2.8-hd"
MINIMAX_VOICE_ALIASES = {
    "知了": "bilibili-zhiliao",
    "蓉蓉": "rongrong-v2",
    "荣荣": "rongrong-v2",
    "小博": "xiaobo-v2",
    "小燃": "xiaoran-v2",
    "小歪": "xiaowai-v6",
}
MINIMAX_KNOWN_LOCAL_VOICE_IDS = set(MINIMAX_VOICE_ALIASES.values())
MINIMAX_SKILL_ENV_PATH = Path(r"C:\Users\zhaoer\.codex\skills\minimax-tts\.env")
DEFAULT_SILENCE_THRESHOLD_DB = -45.0
DEFAULT_MIN_SILENCE_MS = 300
DEFAULT_KEEP_SILENCE_MS = 220
DEFAULT_LONG_SILENCE_MS = 800
DEFAULT_LONG_SILENCE_KEEP_MS = 350
DEFAULT_SILENCE_CHUNK_MS = 10
DEFAULT_LEADING_SILENCE_MS = 100
DEFAULT_MAX_LEADING_SILENCE_MS = 120
DEFAULT_TRAILING_SILENCE_LIMIT_MS = 500
DEFAULT_TRAILING_SILENCE_KEEP_MS = 200
DEFAULT_LOUDNORM_I = -11.0
DEFAULT_LOUDNORM_TP = -1.0
DEFAULT_LOUDNORM_LRA = 11.0


def normalize_audio_loudness(
    audio_path: Path,
    *,
    target_i: float = DEFAULT_LOUDNORM_I,
    target_tp: float = DEFAULT_LOUDNORM_TP,
    target_lra: float = DEFAULT_LOUDNORM_LRA,
) -> dict[str, Any]:
    if not audio_path.exists():
        return {"enabled": True, "changed": False, "reason": "missing file"}
    if audio_path.stat().st_size <= 0:
        return {"enabled": True, "changed": False, "reason": "empty file"}

    try:
        measured = _measure_loudness(audio_path, target_i=target_i, target_tp=target_tp, target_lra=target_lra)
        result = _apply_loudnorm(
            audio_path,
            target_i=target_i,
            target_tp=target_tp,
            target_lra=target_lra,
            measured=measured,
        )
        result["measured"] = measured
        result["two_pass"] = True
        return result
    except Exception as exc:
        logger.warning("两遍 loudnorm 失败，回退单遍处理：%s (%s)", audio_path, exc)
        try:
            return _apply_loudnorm(
                audio_path,
                target_i=target_i,
                target_tp=target_tp,
                target_lra=target_lra,
                measured=None,
            )
        except Exception as fallback_exc:
            logger.warning("音频响度归一化失败，保留原文件：%s (%s)", audio_path, fallback_exc)
            return {
                "enabled": True,
                "changed": False,
                "target_i_lufs": target_i,
                "target_tp_db": target_tp,
                "target_lra": target_lra,
                "reason": str(fallback_exc),
            }


def _measure_loudness(
    audio_path: Path,
    *,
    target_i: float,
    target_tp: float,
    target_lra: float,
) -> dict[str, str]:
    audio_filter = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
        "print_format=json"
    )
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path), "-af", audio_filter, "-f", "null", os.devnull],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffmpeg loudness measure failed: {result.returncode}")
    input_marker = result.stderr.rfind('"input_i"')
    if input_marker >= 0:
        json_start = result.stderr.rfind("{", 0, input_marker)
        json_end = result.stderr.find("}", input_marker)
        if json_start >= 0 and json_end > json_start:
            raw = result.stderr[json_start : json_end + 1]
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
            data = json.loads(cleaned)
            if "input_i" in data and "target_offset" in data:
                return {str(key): str(value) for key, value in data.items()}
    raise RuntimeError("ffmpeg loudnorm did not return JSON measurement")


def _apply_loudnorm(
    audio_path: Path,
    *,
    target_i: float,
    target_tp: float,
    target_lra: float,
    measured: dict[str, str] | None,
) -> dict[str, Any]:
    audio_filter = f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
    if measured:
        audio_filter += (
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            ":linear=true:print_format=summary"
        )

    temp_path = audio_path.with_name(f"{audio_path.stem}.loudnorm.tmp{audio_path.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio_path),
        "-af",
        audio_filter,
        *_audio_codec_args(audio_path),
        str(temp_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"ffmpeg loudnorm apply failed: {result.returncode}")
        temp_path.replace(audio_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "enabled": True,
        "changed": True,
        "target_i_lufs": target_i,
        "target_tp_db": target_tp,
        "target_lra": target_lra,
    }


def _audio_codec_args(audio_path: Path) -> list[str]:
    suffix = audio_path.suffix.casefold()
    if suffix == ".wav":
        return ["-c:a", "pcm_s16le"]
    if suffix == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    if suffix in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "192k"]
    if suffix == ".flac":
        return ["-c:a", "flac"]
    return ["-c:a", "aac", "-b:a", "192k"]


def seconds_to_frames(seconds: float, frame_rate: int) -> int:
    return max(0, int(round(seconds * frame_rate)))


def normalize_voice_provider(value: str) -> str:
    text = safe_text(value).casefold()
    if text in {"minimax", "minimax api", "minimax-api", "minimax_api", "api", "MiniMax API".casefold()}:
        return VOICE_PROVIDER_MINIMAX
    return VOICE_PROVIDER_INDEXTTS


def voice_provider_label(provider: str) -> str:
    return VOICE_PROVIDER_LABELS.get(normalize_voice_provider(provider), VOICE_PROVIDER_LABELS[VOICE_PROVIDER_INDEXTTS])


def resolve_minimax_voice_id(value: str) -> str:
    text = safe_text(value)
    return MINIMAX_VOICE_ALIASES.get(text, text)


def account_voice_id_for_provider(account: dict[str, Any], provider: str) -> str:
    normalized = normalize_voice_provider(provider)
    if normalized == VOICE_PROVIDER_MINIMAX:
        explicit = safe_text(account.get("minimax_voice_id"))
        if explicit:
            return resolve_minimax_voice_id(explicit)
        for key in ("label", "voice_name", "voice_id", "account_id"):
            resolved = resolve_minimax_voice_id(safe_text(account.get(key)))
            if resolved in MINIMAX_KNOWN_LOCAL_VOICE_IDS:
                return resolved
        return ""
    return safe_text(account.get("voice_id") or account.get("account_id"))


def _load_env_file_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        return value.strip().strip('"').strip("'")
    return ""


def load_minimax_api_key() -> str:
    env_value = os.environ.get("MINIMAX_API_KEY", "").strip()
    if env_value:
        return env_value
    for candidate in (MINIMAX_SKILL_ENV_PATH, Path.cwd() / ".env"):
        value = _load_env_file_value(candidate, "MINIMAX_API_KEY")
        if value:
            return value
    raise ValueError(f"找不到 MINIMAX_API_KEY。请配置系统环境变量，或写入：{MINIMAX_SKILL_ENV_PATH}")


def dbfs_for_chunk(chunk: bytes, sample_width: int) -> float:
    if not chunk:
        return -math.inf
    sample_count = len(chunk) // sample_width
    if sample_count <= 0:
        return -math.inf
    total_square = 0
    for offset in range(0, sample_count * sample_width, sample_width):
        sample = chunk[offset : offset + sample_width]
        if sample_width == 1:
            value = sample[0] - 128
        else:
            value = int.from_bytes(sample, byteorder="little", signed=True)
        total_square += value * value
    rms = math.sqrt(total_square / sample_count)
    if rms <= 0:
        return -math.inf
    peak = float(128 if sample_width == 1 else (1 << (8 * sample_width - 1)) - 1)
    if peak <= 0:
        return -math.inf
    return 20.0 * math.log10(rms / peak)


def silence_ranges_for_audio(
    raw_audio: bytes,
    *,
    frame_count: int,
    frame_rate: int,
    bytes_per_frame: int,
    sample_width: int,
    threshold_db: float = DEFAULT_SILENCE_THRESHOLD_DB,
    chunk_ms: int = DEFAULT_SILENCE_CHUNK_MS,
) -> list[tuple[int, int, bool]]:
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be greater than 0")

    chunk_frames = max(1, seconds_to_frames(chunk_ms / 1000.0, frame_rate))
    chunks: list[tuple[int, int, bool]] = []
    start_frame = 0
    while start_frame < frame_count:
        end_frame = min(frame_count, start_frame + chunk_frames)
        start_byte = start_frame * bytes_per_frame
        end_byte = end_frame * bytes_per_frame
        chunk = raw_audio[start_byte:end_byte]
        chunks.append((start_frame, end_frame, dbfs_for_chunk(chunk, sample_width) <= threshold_db))
        start_frame = end_frame

    if not chunks:
        return []

    ranges: list[tuple[int, int, bool]] = []
    current_start, current_end, current_silence = chunks[0]
    for chunk_start, chunk_end, is_silence in chunks[1:]:
        if is_silence == current_silence:
            current_end = chunk_end
            continue
        ranges.append((current_start, current_end, current_silence))
        current_start, current_end, current_silence = chunk_start, chunk_end, is_silence
    ranges.append((current_start, current_end, current_silence))
    return ranges


def compress_internal_silence(
    audio_path: Path,
    *,
    threshold_db: float = DEFAULT_SILENCE_THRESHOLD_DB,
    min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
    keep_silence_ms: int = DEFAULT_KEEP_SILENCE_MS,
    long_silence_ms: int = DEFAULT_LONG_SILENCE_MS,
    long_keep_silence_ms: int = DEFAULT_LONG_SILENCE_KEEP_MS,
    chunk_ms: int = DEFAULT_SILENCE_CHUNK_MS,
) -> dict[str, Any]:
    if min_silence_ms < keep_silence_ms:
        return {"enabled": True, "changed": False, "reason": "min_silence_ms < keep_silence_ms"}
    if long_silence_ms < min_silence_ms or long_keep_silence_ms < keep_silence_ms:
        return {"enabled": True, "changed": False, "reason": "invalid long silence thresholds"}
    if chunk_ms <= 0:
        raise ValueError("静音修音参数错误：chunk_ms 必须大于 0。")

    with wave.open(str(audio_path), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        frame_count = reader.getnframes()
        channel_count = reader.getnchannels()
        sample_width = reader.getsampwidth()
        raw_audio = reader.readframes(frame_count)

    if frame_rate <= 0 or frame_count <= 0:
        return {"enabled": True, "changed": False, "reason": "empty audio"}
    if sample_width not in {1, 2, 3, 4}:
        return {"enabled": True, "changed": False, "reason": f"unsupported sample width {sample_width}"}

    bytes_per_frame = channel_count * sample_width
    min_silence_frames = seconds_to_frames(min_silence_ms / 1000.0, frame_rate)
    keep_silence_frames = seconds_to_frames(keep_silence_ms / 1000.0, frame_rate)
    long_silence_frames = seconds_to_frames(long_silence_ms / 1000.0, frame_rate)
    long_keep_silence_frames = seconds_to_frames(long_keep_silence_ms / 1000.0, frame_rate)
    ranges = silence_ranges_for_audio(
        raw_audio,
        frame_count=frame_count,
        frame_rate=frame_rate,
        bytes_per_frame=bytes_per_frame,
        sample_width=sample_width,
        threshold_db=threshold_db,
        chunk_ms=chunk_ms,
    )

    if not ranges:
        return {"enabled": True, "changed": False, "reason": "no chunks"}

    output_parts: list[bytes] = []
    compressed: list[dict[str, Any]] = []
    last_index = len(ranges) - 1
    for index, (start, end, is_silence) in enumerate(ranges):
        duration_frames = end - start
        keep_frames = duration_frames
        if is_silence and 0 < index < last_index and duration_frames > min_silence_frames:
            target_frames = long_keep_silence_frames if duration_frames > long_silence_frames else keep_silence_frames
            keep_frames = min(duration_frames, target_frames)
            compressed.append(
                {
                    "start_seconds": round(start / frame_rate, 3),
                    "original_ms": round(duration_frames * 1000 / frame_rate),
                    "kept_ms": round(keep_frames * 1000 / frame_rate),
                }
            )
        start_byte = start * bytes_per_frame
        end_byte = (start + keep_frames) * bytes_per_frame
        output_parts.append(raw_audio[start_byte:end_byte])

    if not compressed:
        return {
            "enabled": True,
            "changed": False,
            "threshold_db": threshold_db,
            "min_silence_ms": min_silence_ms,
            "keep_silence_ms": keep_silence_ms,
            "long_silence_ms": long_silence_ms,
            "long_keep_silence_ms": long_keep_silence_ms,
            "compressed_count": 0,
        }

    fixed_audio = b"".join(output_parts)
    temp_path = audio_path.with_name(f"{audio_path.stem}.silencefix.tmp{audio_path.suffix}")
    try:
        with wave.open(str(temp_path), "wb") as writer:
            writer.setparams(params)
            writer.writeframes(fixed_audio)
        temp_path.replace(audio_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    original_ms = round(frame_count * 1000 / frame_rate)
    fixed_frames = len(fixed_audio) // bytes_per_frame
    fixed_ms = round(fixed_frames * 1000 / frame_rate)
    return {
        "enabled": True,
        "changed": True,
        "threshold_db": threshold_db,
        "min_silence_ms": min_silence_ms,
        "keep_silence_ms": keep_silence_ms,
        "long_silence_ms": long_silence_ms,
        "long_keep_silence_ms": long_keep_silence_ms,
        "compressed_count": len(compressed),
        "compressed": compressed,
        "original_ms": original_ms,
        "fixed_ms": fixed_ms,
        "removed_ms": original_ms - fixed_ms,
    }


def prepend_silence(audio_path: Path, *, silence_ms: int = DEFAULT_LEADING_SILENCE_MS) -> dict[str, Any]:
    if silence_ms <= 0:
        return {"enabled": True, "changed": False, "reason": "silence_ms <= 0"}

    with wave.open(str(audio_path), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        frame_count = reader.getnframes()
        channel_count = reader.getnchannels()
        sample_width = reader.getsampwidth()
        raw_audio = reader.readframes(frame_count)

    if frame_rate <= 0 or channel_count <= 0 or sample_width <= 0:
        return {"enabled": True, "changed": False, "reason": "invalid wav params"}

    silence_frames = seconds_to_frames(silence_ms / 1000.0, frame_rate)
    if silence_frames <= 0:
        return {"enabled": True, "changed": False, "reason": "silence too short"}

    silent_prefix = b"\x00" * silence_frames * channel_count * sample_width
    temp_path = audio_path.with_name(f"{audio_path.stem}.leadingsilence.tmp{audio_path.suffix}")
    try:
        with wave.open(str(temp_path), "wb") as writer:
            writer.setparams(params)
            writer.writeframes(silent_prefix + raw_audio)
        temp_path.replace(audio_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "enabled": True,
        "changed": True,
        "silence_ms": round(silence_frames * 1000 / frame_rate),
    }


def normalize_generated_voice_silence(
    audio_path: Path,
    *,
    threshold_db: float = DEFAULT_SILENCE_THRESHOLD_DB,
    chunk_ms: int = DEFAULT_SILENCE_CHUNK_MS,
    leading_min_ms: int = DEFAULT_LEADING_SILENCE_MS,
    leading_trim_limit_ms: int = 300,
    leading_keep_ms: int = DEFAULT_MAX_LEADING_SILENCE_MS,
    trailing_trim_limit_ms: int = DEFAULT_TRAILING_SILENCE_LIMIT_MS,
    trailing_keep_ms: int = DEFAULT_TRAILING_SILENCE_KEEP_MS,
    internal_trim_limit_ms: int = DEFAULT_MIN_SILENCE_MS,
    internal_keep_ms: int = DEFAULT_KEEP_SILENCE_MS,
    internal_long_limit_ms: int = DEFAULT_LONG_SILENCE_MS,
    internal_long_keep_ms: int = DEFAULT_LONG_SILENCE_KEEP_MS,
) -> dict[str, Any]:
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be greater than 0")

    with wave.open(str(audio_path), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        frame_count = reader.getnframes()
        channel_count = reader.getnchannels()
        sample_width = reader.getsampwidth()
        raw_audio = reader.readframes(frame_count)

    if frame_rate <= 0 or frame_count <= 0:
        return {"enabled": True, "changed": False, "reason": "empty audio"}
    if channel_count <= 0 or sample_width not in {1, 2, 3, 4}:
        return {"enabled": True, "changed": False, "reason": "unsupported wav params"}

    bytes_per_frame = channel_count * sample_width
    ranges = silence_ranges_for_audio(
        raw_audio,
        frame_count=frame_count,
        frame_rate=frame_rate,
        bytes_per_frame=bytes_per_frame,
        sample_width=sample_width,
        threshold_db=threshold_db,
        chunk_ms=chunk_ms,
    )
    if not ranges:
        return {"enabled": True, "changed": False, "reason": "no chunks"}

    leading_min_frames = seconds_to_frames(leading_min_ms / 1000.0, frame_rate)
    leading_trim_limit_frames = seconds_to_frames(leading_trim_limit_ms / 1000.0, frame_rate)
    leading_keep_frames = seconds_to_frames(leading_keep_ms / 1000.0, frame_rate)
    trailing_trim_limit_frames = seconds_to_frames(trailing_trim_limit_ms / 1000.0, frame_rate)
    trailing_keep_frames = seconds_to_frames(trailing_keep_ms / 1000.0, frame_rate)
    internal_trim_limit_frames = seconds_to_frames(internal_trim_limit_ms / 1000.0, frame_rate)
    internal_keep_frames = seconds_to_frames(internal_keep_ms / 1000.0, frame_rate)
    internal_long_limit_frames = seconds_to_frames(internal_long_limit_ms / 1000.0, frame_rate)
    internal_long_keep_frames = seconds_to_frames(internal_long_keep_ms / 1000.0, frame_rate)

    output_parts: list[bytes] = []
    changes: list[dict[str, Any]] = []
    last_index = len(ranges) - 1
    leading_kept_frames = 0
    for index, (start, end, is_silence) in enumerate(ranges):
        duration_frames = end - start
        keep_frames = duration_frames
        reason = ""
        if is_silence and index == 0:
            leading_kept_frames = duration_frames
            if duration_frames > leading_trim_limit_frames:
                keep_frames = min(duration_frames, leading_keep_frames)
                leading_kept_frames = keep_frames
                reason = "leading"
        elif is_silence and index == last_index:
            if duration_frames > trailing_trim_limit_frames:
                keep_frames = min(duration_frames, trailing_keep_frames)
                reason = "trailing"
        elif is_silence and duration_frames > internal_trim_limit_frames:
            target_frames = internal_long_keep_frames if duration_frames > internal_long_limit_frames else internal_keep_frames
            keep_frames = min(duration_frames, target_frames)
            reason = "internal_long" if duration_frames > internal_long_limit_frames else "internal"

        if reason and keep_frames < duration_frames:
            changes.append(
                {
                    "type": reason,
                    "start_seconds": round(start / frame_rate, 3),
                    "original_ms": round(duration_frames * 1000 / frame_rate),
                    "kept_ms": round(keep_frames * 1000 / frame_rate),
                }
            )
        start_byte = start * bytes_per_frame
        end_byte = (start + keep_frames) * bytes_per_frame
        output_parts.append(raw_audio[start_byte:end_byte])

    prefix_frames = 0
    if leading_kept_frames < leading_min_frames:
        prefix_frames = leading_min_frames - leading_kept_frames
        output_parts.insert(0, b"\x00" * prefix_frames * bytes_per_frame)

    if not changes and prefix_frames <= 0:
        return {
            "enabled": True,
            "changed": False,
            "threshold_db": threshold_db,
            "changed_count": 0,
        }

    fixed_audio = b"".join(output_parts)
    temp_path = audio_path.with_name(f"{audio_path.stem}.voicefix.tmp{audio_path.suffix}")
    try:
        with wave.open(str(temp_path), "wb") as writer:
            writer.setparams(params)
            writer.writeframes(fixed_audio)
        temp_path.replace(audio_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    original_ms = round(frame_count * 1000 / frame_rate)
    fixed_frames = len(fixed_audio) // bytes_per_frame
    fixed_ms = round(fixed_frames * 1000 / frame_rate)
    return {
        "enabled": True,
        "changed": True,
        "threshold_db": threshold_db,
        "changed_count": len(changes) + (1 if prefix_frames > 0 else 0),
        "changes": changes,
        "prepended_ms": round(prefix_frames * 1000 / frame_rate),
        "original_ms": original_ms,
        "fixed_ms": fixed_ms,
        "removed_ms": original_ms - fixed_ms,
    }


def markdown_to_voice_text(markdown: str) -> str:
    text = safe_text(markdown)
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n?", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s{0,3}>\s?", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+[.)、]\s+", "", line)
        line = re.sub(r"[*_~]{1,3}", "", line)
        line = line.strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def markdown_file_to_voice_text(path: str | Path) -> str:
    md_path = Path(path)
    if md_path.suffix.casefold() != ".md":
        raise ValueError("只支持选择 MD 文档。")
    if not md_path.exists():
        raise ValueError(f"MD 文档不存在：{md_path}")
    if not md_path.is_file():
        raise ValueError(f"MD 路径不是文件：{md_path}")
    return markdown_to_voice_text(md_path.read_text(encoding="utf-8-sig"))
