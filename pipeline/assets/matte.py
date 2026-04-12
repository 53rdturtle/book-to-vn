"""Background removal for anime character images using ToonOut (BiRefNet fine-tune)."""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image


@lru_cache(maxsize=1)
def _load_model():
    import torch
    from huggingface_hub import hf_hub_download
    from transformers import AutoModelForImageSegmentation

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet", trust_remote_code=True
    )

    weights_path = hf_hub_download("joelseytre/toonout", "birefnet_finetuned_toonout.pth")
    state_dict = torch.load(weights_path, map_location="cpu", weights_only=True)
    clean = {}
    for k, v in state_dict.items():
        if k.startswith("module._orig_mod."):
            clean[k[len("module._orig_mod."):]] = v
        elif k.startswith("module."):
            clean[k[len("module."):]] = v
        else:
            clean[k] = v
    model.load_state_dict(clean)
    model.float()

    model.to(device).eval()
    return model, device


def remove_background(img: Image.Image) -> Image.Image:
    """Remove background from a PIL Image, returning RGBA with transparent BG."""
    import torch
    from torchvision import transforms

    model, device = _load_model()

    rgb = img.convert("RGB")
    orig_size = rgb.size

    transform = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    tensor = transform(rgb).unsqueeze(0).to(device)
    with torch.no_grad():
        preds = model(tensor)[-1].sigmoid().cpu()

    mask = transforms.ToPILImage()(preds[0].squeeze())
    mask = mask.resize(orig_size, Image.LANCZOS)

    result = rgb.copy()
    result.putalpha(mask)
    return result


def remove_background_bytes(img_bytes: bytes) -> Image.Image:
    """Convenience: accept raw bytes, return RGBA PIL Image."""
    img = Image.open(io.BytesIO(img_bytes))
    return remove_background(img)
