"""Pick the best available compute device for local models."""


def get_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"  # Apple Silicon
    return "cpu"


def use_fp16(device: str) -> bool:
    """Half precision is faster on NVIDIA GPUs; on CPU it is slow or unsupported."""
    return device == "cuda"
