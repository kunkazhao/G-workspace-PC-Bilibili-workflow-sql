from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

ENGINE_DIR = Path(__file__).resolve().parents[1] / "scripts" / "jianying_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

fake_torch = types.ModuleType("torch")
fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
fake_torch.bfloat16 = object()
sys.modules.setdefault("torch", fake_torch)

fake_qwen_asr = types.ModuleType("qwen_asr")
fake_qwen_asr.Qwen3ForcedAligner = None
sys.modules.setdefault("qwen_asr", fake_qwen_asr)

import scripts.jianying_engine.generate_jianying_draft as engine


class FakeClipSettings:
    def __init__(
        self,
        *,
        scale_x: float | None = None,
        scale_y: float | None = None,
        transform_x: float | None = None,
        transform_y: float | None = None,
    ) -> None:
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.transform_x = transform_x
        self.transform_y = transform_y


def test_display_video_panel_pixel_mode_uses_jianying_transform_coordinates(monkeypatch):
    monkeypatch.setattr(engine, "draft", SimpleNamespace(ClipSettings=FakeClipSettings))
    monkeypatch.setattr(engine, "probe_video_size", lambda _path: (1936, 1080))

    settings = engine.build_display_video_clip_settings(
        Path("roll-b.mp4"),
        {
            "x": -830,
            "y": -77,
            "width": 970,
            "height": 590,
            "coordinate_mode": "clip_transform_pixels",
        },
        1920,
        1080,
    )

    assert settings.transform_x == -830 / 1920
    assert settings.transform_y == -77 / 1080
    assert settings.scale_x == 970 / 1936
    assert settings.scale_y == 590 / 1080


def test_display_video_panel_pixel_mode_can_use_calibrated_fixed_scale(monkeypatch):
    monkeypatch.setattr(engine, "draft", SimpleNamespace(ClipSettings=FakeClipSettings))
    monkeypatch.setattr(engine, "probe_video_size", lambda _path: (1280, 720))

    settings = engine.build_display_video_clip_settings(
        Path("roll-b-720p.mp4"),
        {
            "x": -830,
            "y": -77,
            "width": 970,
            "height": 590,
            "coordinate_mode": "clip_transform_pixels",
            "scale_x": 970 / 1936,
            "scale_y": 590 / 1080,
        },
        1920,
        1080,
    )

    assert settings.transform_x == -830 / 1920
    assert settings.transform_y == -77 / 1080
    assert settings.scale_x == 970 / 1936
    assert settings.scale_y == 590 / 1080
