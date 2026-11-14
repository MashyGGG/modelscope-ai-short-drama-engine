"""Resolve upstream HF revisions and LFS content hashes without downloading weights."""

import json
import urllib.request

from core.common import ROOT, write_json

ITEMS = [
    (
        "sdxl",
        "stabilityai/stable-diffusion-xl-base-1.0",
        "sd_xl_base_1.0.safetensors",
        "checkpoints/sd_xl_base_1.0.safetensors",
        True,
    ),
    (
        "wan",
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
        "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
        True,
    ),
    (
        "umt5",
        "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        True,
    ),
    (
        "vae",
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "split_files/vae/wan2.2_vae.safetensors",
        "vae/wan2.2_vae.safetensors",
        True,
    ),
    (
        "faceid",
        "h94/IP-Adapter-FaceID",
        "ip-adapter-faceid-plusv2_sdxl.bin",
        "ipadapter/ip-adapter-faceid-plusv2_sdxl.bin",
        True,
    ),
    (
        "faceid_lora",
        "h94/IP-Adapter-FaceID",
        "ip-adapter-faceid-plusv2_sdxl_lora.safetensors",
        "loras/ip-adapter-faceid-plusv2_sdxl_lora.safetensors",
        True,
    ),
    (
        "clip_vision",
        "h94/IP-Adapter",
        "models/image_encoder/model.safetensors",
        "clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        True,
    ),
    (
        "appearance",
        "h94/IP-Adapter",
        "sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors",
        "ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors",
        False,
    ),
    (
        "openpose",
        "xinsir/controlnet-openpose-sdxl-1.0",
        "diffusion_pytorch_model.safetensors",
        "controlnet/openpose_sdxl.safetensors",
        False,
    ),
]


def main():
    repos, models = {}, []
    for key, repo, filename, destination, required in ITEMS:
        if repo not in repos:
            request = urllib.request.Request(
                f"https://huggingface.co/api/models/{repo}?blobs=true", headers={"User-Agent": "short-drama-engine"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                repos[repo] = json.load(response)
        data = repos[repo]
        sibling = next(x for x in data["siblings"] if x["rfilename"] == filename)
        lfs = sibling["lfs"]
        models.append(
            {
                "key": key,
                "repo": repo,
                "revision": data["sha"],
                "file": filename,
                "destination": destination,
                "size": lfs["size"],
                "sha256": lfs["sha256"],
                "required": required,
            }
        )
        print(key, data["sha"], flush=True)
    write_json(ROOT / "config/models-lock.json", {"models": models})


if __name__ == "__main__":
    main()
