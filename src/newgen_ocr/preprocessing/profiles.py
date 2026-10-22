"""Preprocessing profiles: named, versionable parameter sets.

Aggressive filters are never applied universally. In the default ``standard`` profile,
enhancement steps run only when the quality assessment flags the matching problem
(``auto`` mode). Additional profiles can be supplied via ``NGOCR_CUSTOM_PREPROCESSING_PROFILES``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from newgen_ocr.imaging.filters import DenoiseMethod


class StepMode(StrEnum):
    NEVER = "never"
    AUTO = "auto"  # only when the quality assessment flags the problem
    ALWAYS = "always"


class ProfileConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    description: str = ""
    orientation: bool = True  # rotate pages whose text lines run vertically
    deskew: bool = True
    deskew_min_angle: float = Field(default=0.3, ge=0.0, le=15.0)
    illumination: StepMode = StepMode.NEVER  # auto: uneven_background
    denoise: StepMode = StepMode.AUTO  # auto: noisy
    denoise_method: DenoiseMethod = DenoiseMethod.MEDIAN
    denoise_strength: int = Field(default=3, ge=1, le=15)
    contrast: StepMode = StepMode.AUTO  # auto: poor_contrast
    clahe_clip_limit: float = Field(default=2.0, gt=0.0, le=10.0)
    clahe_tile_grid: int = Field(default=8, ge=1, le=32)
    adaptive_threshold: bool = False
    threshold_block_inches: float = Field(default=0.1, gt=0.0, le=1.0)
    threshold_c: int = Field(default=10, ge=0, le=50)


BUILTIN_PROFILES: dict[str, ProfileConfig] = {
    p.name: p
    for p in (
        ProfileConfig(
            name="none",
            description="No preprocessing; OCR uses the source page image.",
            orientation=False,
            deskew=False,
            denoise=StepMode.NEVER,
            contrast=StepMode.NEVER,
        ),
        ProfileConfig(
            name="standard",
            description=(
                "Orientation and deskew; denoise/contrast only when quality flags call for it."
            ),
        ),
        ProfileConfig(
            name="degraded_scan",
            description="Old or poor scans: always denoise (median) and enhance contrast.",
            denoise=StepMode.ALWAYS,
            contrast=StepMode.ALWAYS,
            clahe_clip_limit=3.0,
        ),
        ProfileConfig(
            name="photo",
            description="Phone/camera captures: flatten illumination, then enhance contrast.",
            illumination=StepMode.ALWAYS,
            denoise=StepMode.AUTO,
            denoise_method=DenoiseMethod.BILATERAL,
            denoise_strength=7,
            contrast=StepMode.ALWAYS,
        ),
        ProfileConfig(
            name="binarize",
            description="Standard plus adaptive thresholding to black and white.",
            adaptive_threshold=True,
        ),
    )
}


def merge_profiles(custom: tuple[ProfileConfig, ...]) -> dict[str, ProfileConfig]:
    profiles = dict(BUILTIN_PROFILES)
    for profile in custom:
        if profile.name in profiles:
            raise ValueError(f"preprocessing profile {profile.name!r} is already defined")
        profiles[profile.name] = profile
    return profiles
