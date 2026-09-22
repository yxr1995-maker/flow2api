import os
import io
import time
import base64
import torch
import numpy as np
from PIL import Image
import urllib.request
import urllib.error
from urllib.parse import urlparse
import folder_paths
from typing import Dict, Any, List, Optional

from .client import call_flow2api_stream, get_connection_config

# Try importing comfy native Video support
HAVE_COMFY_VIDEO = False
try:
    from comfy_api.latest import InputImpl, Types
    HAVE_COMFY_VIDEO = True
except Exception:
    HAVE_COMFY_VIDEO = False

IMAGE_MODELS = [
    "gemini-3.0-pro-image-four-three",
    "gemini-3.0-pro-image-four-three-2k",
    "gemini-3.0-pro-image-four-three-4k",
    "gemini-3.0-pro-image-landscape",
    "gemini-3.0-pro-image-landscape-2k",
    "gemini-3.0-pro-image-landscape-4k",
    "gemini-3.0-pro-image-portrait",
    "gemini-3.0-pro-image-portrait-2k",
    "gemini-3.0-pro-image-portrait-4k",
    "gemini-3.0-pro-image-square",
    "gemini-3.0-pro-image-square-2k",
    "gemini-3.0-pro-image-square-4k",
    "gemini-3.0-pro-image-three-four",
    "gemini-3.0-pro-image-three-four-2k",
    "gemini-3.0-pro-image-three-four-4k",
    "gemini-3.1-flash-image-four-three",
    "gemini-3.1-flash-image-four-three-2k",
    "gemini-3.1-flash-image-four-three-4k",
    "gemini-3.1-flash-image-landscape",
    "gemini-3.1-flash-image-landscape-2k",
    "gemini-3.1-flash-image-landscape-4k",
    "gemini-3.1-flash-image-portrait",
    "gemini-3.1-flash-image-portrait-2k",
    "gemini-3.1-flash-image-portrait-4k",
    "gemini-3.1-flash-image-square",
    "gemini-3.1-flash-image-square-2k",
    "gemini-3.1-flash-image-square-4k",
    "gemini-3.1-flash-image-three-four",
    "gemini-3.1-flash-image-three-four-2k",
    "gemini-3.1-flash-image-three-four-4k",
    "imagen-4.0-generate-preview-landscape",
    "imagen-4.0-generate-preview-portrait",
]

VIDEO_MODELS = [
    # Omni Flash (abra_t2v_8s / abra_r2v_8s, max 3 ref images)
    "omni",
    "omni_portrait",
    # Veo 3.1 T2V
    "veo_3_1_t2v_fast_landscape",
    "veo_3_1_t2v_fast_portrait",
    "veo_3_1_t2v_landscape",
    "veo_3_1_t2v_portrait",
    "veo_3_1_t2v_fast_landscape_4s",
    "veo_3_1_t2v_fast_portrait_4s",
    "veo_3_1_t2v_fast_landscape_6s",
    "veo_3_1_t2v_fast_portrait_6s",
    "veo_3_1_t2v_lite_landscape",
    "veo_3_1_t2v_lite_portrait",
    # Veo 3.1 I2V (1-2 images: first frame or first+last frame)
    "veo_3_1_i2v_s_fast_fl",
    "veo_3_1_i2v_s_fast_portrait_fl",
    "veo_3_1_i2v_s_landscape",
    "veo_3_1_i2v_s_portrait",
    "veo_3_1_i2v_s_fast_landscape_4s_fl",
    "veo_3_1_i2v_s_fast_portrait_4s_fl",
    "veo_3_1_i2v_s_fast_landscape_6s_fl",
    "veo_3_1_i2v_s_fast_portrait_6s_fl",
    "veo_3_1_i2v_lite_landscape",
    "veo_3_1_i2v_lite_portrait",
    "veo_3_1_interpolation_lite_landscape",
    "veo_3_1_interpolation_lite_portrait",
    # Veo 3.1 R2V (Reference images, 1-3 images)
    "veo_3_1_r2v_fast_landscape",
    "veo_3_1_r2v_fast_portrait",
]

def tensor_to_base64(image_tensor: torch.Tensor) -> str:
    """Converts a single image tensor [H, W, C] to base64 data URL."""
    arr = 255.0 * image_tensor.cpu().numpy()
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    pil_img = Image.fromarray(arr)
    buffer = io.BytesIO()
    pil_img.save(buffer, format="JPEG", quality=95)
    b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_str}"

def download_bytes(url: str) -> bytes:
    """Downloads bytes from url or decodes data url safely without Authorization header."""
    if url.startswith("data:"):
        header, encoded = url.split(",", 1)
        return base64.b64decode(encoded)

    parsed = urlparse(url)
    # If URL is relative path like /tmp/... prepend base_url
    if not parsed.scheme:
        base_url, _ = get_connection_config()
        url = f"{base_url.rstrip('/')}/{url.lstrip('/')}"
        parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Prohibited URL scheme '{parsed.scheme}'. Only http(s) and data URIs are allowed.")

    # Never attach Authorization header to media file download requests
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        raise RuntimeError("Failed to retrieve media asset from upstream.")

def bytes_to_tensor(img_bytes: bytes) -> torch.Tensor:
    """Decodes image bytes to [1, H, W, 3] float32 tensor."""
    pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    arr = np.array(pil_img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


class Flow2API_ImageNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "A beautiful cinematic landscape"}),
                "model": (IMAGE_MODELS, {"default": "gemini-3.1-flash-image-landscape"}),
                "rerun_nonce": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "tooltip": "Change value to force re-execution without cache"}),
            },
            "optional": {
                "reference_image": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "image_path")
    FUNCTION = "generate"
    CATEGORY = "flow2api"

    def generate(self, prompt: str, model: str, rerun_nonce: int = 0, reference_image: Optional[torch.Tensor] = None):
        content = [{"type": "text", "text": prompt}]

        if reference_image is not None:
            if reference_image.shape[0] > 1:
                raise ValueError(f"reference_image batch size is {reference_image.shape[0]}. Flow2API_ImageNode only accepts a single image (batch size 1).")
            ref_tensor = reference_image[0]
            b64_url = tensor_to_base64(ref_tensor)
            content.append({
                "type": "image_url",
                "image_url": {"url": b64_url}
            })

        messages = [{"role": "user", "content": content}]
        res = call_flow2api_stream(model=model, messages=messages, timeout=300)

        media_url = res.get("media_url")
        if not media_url:
            raise RuntimeError("Generation completed without returning an image asset.")

        img_bytes = download_bytes(media_url)
        img_tensor = bytes_to_tensor(img_bytes)

        # Save to ComfyUI output directory
        output_dir = folder_paths.get_output_directory()
        prefix = "flow2api_img"
        full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            prefix, output_dir, img_tensor.shape[2], img_tensor.shape[1]
        )
        file_name = f"{filename}_{counter:05}_.png"
        file_path = os.path.join(full_output_folder, file_name)
        Image.fromarray((img_tensor[0].cpu().numpy() * 255).astype(np.uint8)).save(file_path)

        ui_preview = {
            "images": [{
                "filename": file_name,
                "subfolder": subfolder,
                "type": "output"
            }]
        }

        return {"ui": ui_preview, "result": (img_tensor, file_path)}


class Flow2API_VideoNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "Cinematic camera drone flying through valley"}),
                "model": (VIDEO_MODELS, {"default": "omni"}),
                "rerun_nonce": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "tooltip": "Change value to force re-execution without cache"}),
            },
            "optional": {
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
                "ref_images": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING") if HAVE_COMFY_VIDEO else ("STRING",)
    RETURN_NAMES = ("video", "video_path") if HAVE_COMFY_VIDEO else ("video_path",)
    OUTPUT_NODE = True
    FUNCTION = "generate"
    CATEGORY = "flow2api"

    def generate(self, prompt: str, model: str, rerun_nonce: int = 0,
                 first_frame: Optional[torch.Tensor] = None,
                 last_frame: Optional[torch.Tensor] = None,
                 ref_images: Optional[torch.Tensor] = None):

        content = [{"type": "text", "text": prompt}]

        # Validate and map image inputs according to model specifications
        is_omni = model.startswith("omni")
        is_t2v = "t2v" in model and not is_omni
        is_i2v = "i2v" in model or "interpolation" in model
        is_r2v = "r2v" in model

        # Check for multi-frame batch on single-frame slots
        if first_frame is not None and first_frame.shape[0] > 1:
            raise ValueError(f"first_frame input has batch size {first_frame.shape[0]}. Expected single image (batch size 1).")
        if last_frame is not None and last_frame.shape[0] > 1:
            raise ValueError(f"last_frame input has batch size {last_frame.shape[0]}. Expected single image (batch size 1).")

        images_to_attach: List[str] = []

        if is_t2v:
            if first_frame is not None or last_frame is not None or ref_images is not None:
                raise ValueError(f"Model '{model}' is pure Text-to-Video (T2V) and does not support image inputs.")
        elif is_i2v:
            if ref_images is not None:
                raise ValueError(f"Model '{model}' is I2V (First/Last frame) mode. Use 'first_frame' and 'last_frame', not 'ref_images'.")
            if first_frame is None:
                raise ValueError(f"Model '{model}' requires 'first_frame'.")

            # Check interpolation model requires strictly 2 frames
            if "interpolation" in model:
                if last_frame is None:
                    raise ValueError(f"Model '{model}' is an interpolation model and requires strictly 2 frames (both 'first_frame' and 'last_frame').")
                images_to_attach.append(tensor_to_base64(first_frame[0]))
                images_to_attach.append(tensor_to_base64(last_frame[0]))
            elif "lite" in model:
                if last_frame is not None:
                    raise ValueError(f"Model '{model}' is lite I2V and only supports 1 frame ('first_frame').")
                images_to_attach.append(tensor_to_base64(first_frame[0]))
            else:
                images_to_attach.append(tensor_to_base64(first_frame[0]))
                if last_frame is not None:
                    images_to_attach.append(tensor_to_base64(last_frame[0]))
        elif is_r2v or is_omni:
            if first_frame is not None:
                images_to_attach.append(tensor_to_base64(first_frame[0]))
            if last_frame is not None:
                images_to_attach.append(tensor_to_base64(last_frame[0]))
            if ref_images is not None:
                for i in range(ref_images.shape[0]):
                    images_to_attach.append(tensor_to_base64(ref_images[i]))

            if is_r2v and len(images_to_attach) < 1:
                raise ValueError(f"Model '{model}' is R2V (Reference-to-Video) and requires at least 1 reference image.")

            if len(images_to_attach) > 3:
                raise ValueError(f"Model '{model}' supports at most 3 reference images, but {len(images_to_attach)} were provided.")

        for b64 in images_to_attach:
            content.append({
                "type": "image_url",
                "image_url": {"url": b64}
            })

        messages = [{"role": "user", "content": content}]
        # Video generation timeout is 1500s matching upstream
        res = call_flow2api_stream(model=model, messages=messages, timeout=1500)

        media_url = res.get("media_url")
        if not media_url:
            raise RuntimeError("Generation completed without returning a video asset.")

        video_bytes = download_bytes(media_url)

        # Save to ComfyUI output directory
        output_dir = folder_paths.get_output_directory()
        prefix = "flow2api_vid"
        full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            prefix, output_dir, 1920, 1080
        )
        file_name = f"{filename}_{counter:05}_.mp4"
        file_path = os.path.join(full_output_folder, file_name)

        with open(file_path, "wb") as f:
            f.write(video_bytes)

        # Native preview shape: PreviewVideo serializes to {"images": [...], "animated": (True,)}
        # so the frontend renders a <video> tag on the canvas.
        ui_preview = {
            "images": [{
                "filename": file_name,
                "subfolder": subfolder,
                "type": "output",
            }],
            "animated": (True,),
        }

        if HAVE_COMFY_VIDEO:
            video_ref = InputImpl.VideoFromFile(file_path)
            return {"ui": ui_preview, "result": (video_ref, file_path)}
        else:
            return {"ui": ui_preview, "result": (file_path,)}

