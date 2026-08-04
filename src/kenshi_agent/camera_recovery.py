"""Deterministic frame scoring and telemetry checks for camera recovery."""

from __future__ import annotations

import math
from collections import Counter

from PIL import Image, ImageStat

from .core.evidence import CameraFrameScore
from .core.observation import Observation
from .core.telemetry import normalize_control_label

WORLD_ROI = (0.12, 0.05, 0.90, 0.72)
SCORE_SIZE = (160, 90)
USABLE_STRUCTURE_SCORE_FLOOR = 0.60
USABLE_EDGE_DENSITY_FLOOR = 0.40
USABLE_CONTRAST_FLOOR = 0.50
USABLE_NONFLAT_FRACTION_FLOOR = 0.50
USABLE_INVERSE_DOMINANT_COLOR_FLOOR = 0.60


def score_camera_observation(
    observation: Observation,
    *,
    candidate: str,
    floor: int,
    clear_score_threshold: float,
    anchor_max_distance: float,
) -> CameraFrameScore:
    """Score one retained frame without model judgment.

    The metric rewards visible structure, contrast, and color variation in the
    central world viewport. It penalizes the large flat/dominant-color regions
    produced when the camera is inside a wall or roof. A frame is accepted when
    it either clears the high composite threshold or has balanced minimum
    structure across independent dimensions. Both paths also require telemetry
    to show the selected character's world label and keep the camera anchored
    near that character.
    """

    if observation.screenshot_path is None or observation.screenshot_sha256 is None:
        raise RuntimeError("Camera recovery scoring requires a retained screenshot.")
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        raise RuntimeError("Camera recovery scoring requires fresh telemetry.")
    if observation.world_revision.frame_sequence is None:
        raise RuntimeError("Camera recovery scoring requires a frame sequence.")

    with Image.open(observation.screenshot_path) as source:
        image = source.convert("RGB")
        width, height = image.size
        left, top, right, bottom = WORLD_ROI
        crop = image.crop(
            (
                round(width * left),
                round(height * top),
                round(width * right),
                round(height * bottom),
            )
        ).resize(SCORE_SIZE, Image.Resampling.BILINEAR)

    rgb_bytes = crop.tobytes()
    pixels = list(
        zip(
            rgb_bytes[0::3],
            rgb_bytes[1::3],
            rgb_bytes[2::3],
            strict=True,
        )
    )
    grayscale = crop.convert("L")
    gray_pixels = list(grayscale.tobytes())
    gray_width, gray_height = grayscale.size

    edge_hits = 0
    edge_comparisons = 0
    for y in range(gray_height):
        row = y * gray_width
        for x in range(gray_width):
            index = row + x
            value = gray_pixels[index]
            if x + 1 < gray_width:
                edge_hits += abs(value - gray_pixels[index + 1]) >= 12
                edge_comparisons += 1
            if y + 1 < gray_height:
                edge_hits += abs(value - gray_pixels[index + gray_width]) >= 12
                edge_comparisons += 1
    raw_edge_density = edge_hits / max(1, edge_comparisons)
    edge_density = min(1.0, raw_edge_density / 0.30)

    raw_contrast = ImageStat.Stat(grayscale).stddev[0] / 255.0
    contrast = min(1.0, raw_contrast / 0.15)

    coarse_colors = Counter((red // 32, green // 32, blue // 32) for red, green, blue in pixels)
    color_diversity = min(1.0, len(coarse_colors) / 64.0)
    dominant_fraction = max(coarse_colors.values(), default=0) / max(1, len(pixels))
    inverse_dominant_color = 1.0 - dominant_fraction

    flat_blocks = 0
    total_blocks = 0
    block_width = 10
    block_height = 10
    for top_px in range(0, gray_height, block_height):
        for left_px in range(0, gray_width, block_width):
            values: list[int] = []
            for y in range(top_px, min(top_px + block_height, gray_height)):
                start = y * gray_width + left_px
                values.extend(
                    gray_pixels[start : start + min(block_width, gray_width - left_px)]
                )
            if values:
                mean = sum(values) / len(values)
                variance = sum((value - mean) ** 2 for value in values) / len(values)
                flat_blocks += variance < 80.0
                total_blocks += 1
    nonflat_fraction = 1.0 - (flat_blocks / max(1, total_blocks))

    score = (
        0.25 * edge_density
        + 0.25 * contrast
        + 0.20 * color_diversity
        + 0.15 * nonflat_fraction
        + 0.15 * inverse_dominant_color
    )
    score = min(1.0, max(0.0, score))

    selected = [character for character in telemetry.squad if character.selected]
    selected_character = selected[0] if len(selected) == 1 else None
    expected_label = (
        normalize_control_label(f"[{selected_character.name}]")
        if selected_character is not None
        else ""
    )
    selected_world_label_visible = any(
        control.role == "text"
        and normalize_control_label(control.label) == expected_label
        for control in (telemetry.ui.visible_controls or [])
    )

    anchor_distance: float | None = None
    if (
        selected_character is not None
        and selected_character.position is not None
        and telemetry.camera.center is not None
    ):
        delta_x = telemetry.camera.center.x - selected_character.position.x
        delta_z = telemetry.camera.center.z - selected_character.position.z
        anchor_distance = math.hypot(delta_x, delta_z)

    anchored = (
        selected_world_label_visible
        and anchor_distance is not None
        and anchor_distance <= anchor_max_distance
    )
    balanced_world_structure = (
        score >= USABLE_STRUCTURE_SCORE_FLOOR
        and edge_density >= USABLE_EDGE_DENSITY_FLOOR
        and contrast >= USABLE_CONTRAST_FLOOR
        and nonflat_fraction >= USABLE_NONFLAT_FRACTION_FLOOR
        and inverse_dominant_color >= USABLE_INVERSE_DOMINANT_COLOR_FLOOR
    )
    clear = anchored and (
        score >= clear_score_threshold or balanced_world_structure
    )
    return CameraFrameScore(
        candidate=candidate,
        screenshot_path=observation.screenshot_path,
        screenshot_sha256=observation.screenshot_sha256,
        telemetry_sequence=telemetry.sequence,
        frame_sequence=observation.world_revision.frame_sequence,
        floor=floor,
        score=score,
        edge_density=edge_density,
        contrast=contrast,
        color_diversity=color_diversity,
        nonflat_fraction=nonflat_fraction,
        inverse_dominant_color=inverse_dominant_color,
        selected_world_label_visible=selected_world_label_visible,
        anchor_distance=anchor_distance,
        clear=clear,
    )
