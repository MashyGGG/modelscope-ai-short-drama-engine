from copy import deepcopy

from core.common import ROOT, GateError, config, digest, read_json, write_json

WIDGETS = {
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CLIPLoader": ["clip_name", "type", "device"],
    "VAELoader": ["vae_name"],
    "CreateVideo": ["fps"],
    "SaveVideo": ["filename_prefix", "format", "format.codec", "format.codec.encoding"],
    "Wan22ImageToVideoLatent": ["width", "height", "length", "batch_size"],
    "LoadImage": ["image", "_upload"],
    "CLIPTextEncode": ["text"],
    "KSampler": ["seed", "_control", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "ModelSamplingSD3": ["shift"],
    "VAEDecode": [],
    "SaveImage": ["filename_prefix"],
    "CheckpointLoaderSimple": ["ckpt_name"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "IPAdapterUnifiedLoaderFaceID": ["preset", "lora_strength", "provider"],
    "IPAdapterFaceID": [
        "weight",
        "weight_faceidv2",
        "weight_type",
        "combine_embeds",
        "start_at",
        "end_at",
        "embeds_scaling",
    ],
    "IPAdapterUnifiedLoader": ["preset"],
    "IPAdapterAdvanced": ["weight", "weight_type", "combine_embeds", "start_at", "end_at", "embeds_scaling"],
    "ControlNetLoader": ["control_net_name"],
    "ControlNetApplyAdvanced": ["strength", "start_percent", "end_percent"],
}
OUTPUTS = {
    "UNETLoader": ["MODEL"],
    "CLIPLoader": ["CLIP"],
    "VAELoader": ["VAE"],
    "CreateVideo": ["VIDEO"],
    "SaveVideo": ["VIDEO"],
    "Wan22ImageToVideoLatent": ["LATENT"],
    "LoadImage": ["IMAGE", "MASK"],
    "CLIPTextEncode": ["CONDITIONING"],
    "KSampler": ["LATENT"],
    "ModelSamplingSD3": ["MODEL"],
    "VAEDecode": ["IMAGE"],
    "SaveImage": [],
    "CheckpointLoaderSimple": ["MODEL", "CLIP", "VAE"],
    "EmptyLatentImage": ["LATENT"],
    "IPAdapterUnifiedLoaderFaceID": ["MODEL", "IPADAPTER"],
    "IPAdapterFaceID": ["MODEL", "IMAGE"],
    "IPAdapterUnifiedLoader": ["MODEL", "IPADAPTER"],
    "IPAdapterAdvanced": ["MODEL"],
    "ControlNetLoader": ["CONTROL_NET"],
    "ControlNetApplyAdvanced": ["CONDITIONING", "CONDITIONING"],
}


def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def import_ui(workflow):
    """Convert the checked-in upstream UI format with explicit widget mappings; fail on unknown nodes."""
    links = {link[0]: link for link in workflow["links"]}
    graph = {}
    for item in workflow["nodes"]:
        kind = item["type"]
        if kind in {"MarkdownNote", "Note"}:
            continue
        if kind not in WIDGETS:
            raise ValueError(f"No audited widget mapping for {kind}")
        inputs = {}
        for entry in item.get("inputs", []):
            if entry.get("link") is not None:
                link = links[entry["link"]]
                if link[3] != item["id"]:
                    raise ValueError("UI link destination mismatch")
                inputs[entry["name"]] = [str(link[1]), link[2]]
        for name, value in zip(WIDGETS[kind], item.get("widgets_values") or []):
            if not name.startswith("_"):
                inputs[name] = value
        graph[str(item["id"])] = node(kind, **inputs)
    validate(graph)
    return graph


def validate(graph, info=None):
    visiting, visited = set(), set()
    if info is not None:
        missing = {item["class_type"] for item in graph.values()} - set(info)
        if missing:
            raise GateError(f"Missing ComfyUI node(s): {', '.join(sorted(missing))}; inspect startup logs")

    def visit(key):
        if key in visiting:
            raise ValueError("Workflow cycle")
        if key in visited:
            return
        visiting.add(key)
        item = graph[key]
        kind, inputs = item["class_type"], item["inputs"]
        if kind not in OUTPUTS:
            raise ValueError(f"Unaudited node {kind}")
        schema = None
        if info is not None:
            if kind not in info:
                raise GateError(f"Missing ComfyUI node {kind}; inspect custom-node startup logs")
            schema = info[kind]["input"]
            required, allowed = expand_inputs(schema, inputs)
            if set(required) - set(inputs):
                raise ValueError(f"{kind} missing inputs {set(required) - set(inputs)}")
            if set(inputs) - set(allowed):
                raise ValueError(f"{kind} unknown inputs {set(inputs) - set(allowed)}")
        for name, value in inputs.items():
            if isinstance(value, list):
                if len(value) != 2 or str(value[0]) not in graph or type(value[1]) is not int or value[1] < 0:
                    raise ValueError(f"Broken workflow link {key}.{name}")
                source = graph[str(value[0])]["class_type"]
                outputs = info[source]["output"] if info else OUTPUTS[source]
                if value[1] >= len(outputs):
                    raise ValueError("Invalid output slot")
                if schema and isinstance(allowed[name][0], str) and allowed[name][0] != outputs[value[1]]:
                    raise ValueError(f"Type mismatch {key}.{name}")
                visit(str(value[0]))
            elif schema:
                spec = allowed[name]
                if isinstance(spec[0], list) and value not in spec[0]:
                    raise GateError(f"Unavailable model/option {kind}.{name}={value}")
                options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if isinstance(value, (int, float)) and (
                    value < options.get("min", value) or value > options.get("max", value)
                ):
                    raise ValueError(f"Out-of-range input {kind}.{name}")
        visiting.remove(key)
        visited.add(key)

    for key in graph:
        visit(key)


def expand_inputs(schema, values, prefix=""):
    """Expand the pinned V3 DynamicCombo schema using dot-separated prompt keys."""
    required, allowed = {}, {}
    for group in ("required", "optional"):
        for name, spec in schema.get(group, {}).items():
            key = prefix + name
            if spec[0] == "COMFY_DYNAMICCOMBO_V3":
                choices = spec[1]["options"]
                option = next((o for o in choices if o["key"] == values.get(key)), None)
                if key in values and option is None:
                    raise ValueError(f"Invalid dynamic choice {key}={values[key]}")
                allowed[key] = [[o["key"] for o in choices]]
                if option:
                    nested_required, nested_allowed = expand_inputs(option["inputs"], values, key + ".")
                    required.update(nested_required)
                    allowed.update(nested_allowed)
            else:
                allowed[key] = spec
            if group == "required":
                required[key] = allowed[key]
    return required, allowed


def to_ui(graph):
    nodes, links = [], []
    consumers = {key: [[] for _ in OUTPUTS[item["class_type"]]] for key, item in graph.items()}
    for index, (key, item) in enumerate(graph.items()):
        sockets = []
        for name, value in item["inputs"].items():
            if not isinstance(value, list):
                continue
            source, slot = str(value[0]), value[1]
            link_id = len(links) + 1
            dtype = OUTPUTS[graph[source]["class_type"]][slot]
            links.append([link_id, int(source), slot, int(key), len(sockets), dtype])
            sockets.append({"name": name, "type": dtype, "link": link_id})
            consumers[source][slot].append(link_id)
        widgets = []
        for name in WIDGETS[item["class_type"]]:
            if name == "_control":
                widgets.append("fixed")
            elif name == "_upload":
                widgets.append("image")
            elif name in item["inputs"]:
                widgets.append(item["inputs"][name])
        nodes.append(
            {
                "id": int(key),
                "type": item["class_type"],
                "pos": [(index % 4) * 380, (index // 4) * 330],
                "size": [340, 280],
                "flags": {},
                "order": index,
                "mode": 0,
                "inputs": sockets,
                "outputs": [],
                "properties": {"Node name for S&R": item["class_type"]},
                "widgets_values": widgets,
            }
        )
    for item in nodes:
        item["outputs"] = [
            {"name": dtype, "type": dtype, "links": consumers[str(item["id"])][slot]}
            for slot, dtype in enumerate(OUTPUTS[item["type"]])
        ]
    return {
        "last_node_id": max(map(int, graph)),
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }


class WorkflowManager:
    def __init__(self):
        self.settings = config("production")

    def version(self, graph):
        return digest({"graph": graph, "upstream": read_json(ROOT / "config/upstream-lock.json")})

    def character(self, prompt, negative, seed, prefix):
        graph = read_json(ROOT / "workflows/api/realistic_txt2img.json")
        graph["6"]["inputs"]["text"] = prompt
        graph["7"]["inputs"]["text"] = negative
        graph["3"]["inputs"]["seed"] = seed
        graph["9"]["inputs"]["filename_prefix"] = prefix
        graph["5"]["inputs"].update(width=self.settings["image"]["width"], height=self.settings["image"]["height"])
        return graph, "9"

    def image(self, prompt, negative, seed, prefix, references, appearance_refs=(), pose=None):
        graph, output = self.character(prompt, negative, seed, prefix)
        if references:
            graph["20"] = node(
                "IPAdapterUnifiedLoaderFaceID",
                model=["4", 0],
                preset="FACEID PLUS V2",
                lora_strength=0.6,
                provider="CPU",
            )
            previous = ["20", 0]
            for index, reference in enumerate(references):
                key = str(30 + index * 3)
                graph[key] = node("LoadImage", image=reference["image"])
                args = {
                    "model": previous,
                    "ipadapter": ["20", 1],
                    "image": [key, 0],
                    "weight": 0.8,
                    "weight_faceidv2": 1.0,
                    "weight_type": "linear",
                    "combine_embeds": "concat",
                    "start_at": 0.0,
                    "end_at": 1.0,
                    "embeds_scaling": "V only",
                }
                if len(references) > 1:
                    if not reference.get("mask"):
                        raise GateError(
                            "Multiple visible identities require per-character alpha masks (identity_masks); prefer separate reverse shots"
                        )
                    graph[str(int(key) + 1)] = node("LoadImage", image=reference["mask"])
                    args["attn_mask"] = [str(int(key) + 1), 1]
                graph[str(int(key) + 2)] = node("IPAdapterFaceID", **args)
                previous = [str(int(key) + 2), 0]
            graph["3"]["inputs"]["model"] = previous
        if appearance_refs:
            graph["70"] = node(
                "IPAdapterUnifiedLoader", model=graph["3"]["inputs"]["model"], preset="PLUS (high strength)"
            )
            previous = ["70", 0]
            for index, image in enumerate(appearance_refs):
                key = str(80 + index * 2)
                graph[key] = node("LoadImage", image=image)
                graph[str(int(key) + 1)] = node(
                    "IPAdapterAdvanced",
                    model=previous,
                    ipadapter=["70", 1],
                    image=[key, 0],
                    weight=0.3,
                    weight_type="style transfer",
                    combine_embeds="concat",
                    start_at=0.0,
                    end_at=0.7,
                    embeds_scaling="V only",
                )
                previous = [str(int(key) + 1), 0]
            graph["3"]["inputs"]["model"] = previous
        if pose:
            graph["100"] = node("ControlNetLoader", control_net_name="openpose_sdxl.safetensors")
            graph["101"] = node("LoadImage", image=pose)
            graph["102"] = node(
                "ControlNetApplyAdvanced",
                positive=["6", 0],
                negative=["7", 0],
                control_net=["100", 0],
                image=["101", 0],
                strength=0.7,
                start_percent=0.0,
                end_percent=0.8,
                vae=["4", 2],
            )
            graph["3"]["inputs"].update(positive=["102", 0], negative=["102", 1])
        validate(graph)
        return graph, output

    def video(self, image, prompt, negative, seed, prefix, low_resolution=False, frames=None):
        graph = read_json(ROOT / "workflows/api/wan22_i2v.json")
        settings = self.settings["video"]
        width, height = settings["low_resolution"] if low_resolution else (settings["width"], settings["height"])
        length = frames if frames is not None else settings["frames"]
        if length < 5 or (length - 1) % 4:
            raise ValueError("Wan frames must be 4n+1 and >=5")
        graph["56"]["inputs"]["image"] = image
        graph["6"]["inputs"]["text"] = prompt
        graph["7"]["inputs"]["text"] = negative
        graph["55"]["inputs"].update(width=width, height=height, length=length, batch_size=1)
        graph["3"]["inputs"].update(seed=seed, steps=settings["steps"], cfg=settings["cfg"])
        graph["57"]["inputs"]["fps"] = settings["fps"]
        graph["58"]["inputs"].update(
            {"filename_prefix": prefix, "format": "mp4", "format.codec": "h264", "format.codec.encoding": "auto"}
        )
        validate(graph)
        return graph, "58"


def prepare():
    # First import upstream UI files and validate links. Only then derive production graphs.
    upstream = ROOT / "docs/upstream"
    video = import_ui(read_json(upstream / "templates/templates/video_wan2_2_5B_ti2v.json"))
    face = import_ui(read_json(upstream / "ipadapter/examples/ipadapter_faceid.json"))
    face["4"]["inputs"]["ckpt_name"] = "sd_xl_base_1.0.safetensors"
    face["5"]["inputs"].update(width=704, height=1280, batch_size=1)
    face["3"]["inputs"].update(sampler_name="dpmpp_2m", scheduler="karras", steps=30, cfg=5.5)
    face["18"]["inputs"]["embeds_scaling"] = "V only"
    face["12"]["inputs"]["image"] = "approved_identity.png"
    portrait = deepcopy(face)
    portrait["3"]["inputs"]["model"] = ["4", 0]
    for key in ("12", "20", "18"):
        del portrait[key]
    video["56"]["inputs"]["image"] = "approved_storyboard.png"
    video["38"]["inputs"]["device"] = "cpu"
    video["55"]["inputs"].update(width=704, height=1280, length=49, batch_size=1)
    video["57"]["inputs"]["fps"] = 16
    video["58"]["inputs"].update({"format": "mp4", "format.codec": "h264", "format.codec.encoding": "auto"})
    for name, graph in (("realistic_txt2img", portrait), ("sdxl_faceid", face), ("wan22_i2v", video)):
        validate(graph)
        write_json(ROOT / f"workflows/api/{name}.json", graph)
        write_json(ROOT / f"workflows/ui/{name}.json", to_ui(graph))


if __name__ == "__main__":
    prepare()
