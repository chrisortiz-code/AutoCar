"""Shared model directory — downloaded models are cached here to avoid re-downloading."""

import os

MODELS_DIR = os.path.dirname(os.path.abspath(__file__))


def model_path(filename: str) -> str:
    """Return the full path to a model file in the models/ directory."""
    return os.path.join(MODELS_DIR, filename)
