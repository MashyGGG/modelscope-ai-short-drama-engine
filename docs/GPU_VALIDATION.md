# 魔搭 A10 GPU 验证流程

本文对应本仓库当前 CLI 和安装脚本，用于在 **Linux / NVIDIA A10 24GB / 约 32GB 系统内存** 的魔搭 Notebook 中完成第一集实测。所有命令在 Notebook 的 **Bash 终端**运行；不是在本地 Windows，也不是在 Notebook 的 Python 单元格直接粘贴 Bash。

**这是待执行的操作流程，不是已经通过的 GPU 测试报告。** 当前已完成的离线验证见 [已有验收记录](../reports/ACCEPTANCE.md)。本流程使用内置剧本，不需要配置 LLM API。

## 1. 验证目标与通过标准

内置故事《离婚当天，我继承了千亿集团》，三个成年虚构角色、12 个镜头、8 句对白、45 秒成片。按下面顺序执行，每个阶段通过后再继续：

| 阶段 | 本阶段通过条件 | 还不能证明的事情 |
|---|---|---|
| 安装与预检 | 三个环境可用、权重校验正确、实际 CUDA 运算成功、受管 ComfyUI 可访问 | 模型能够完成真实推理 |
| 角色与分镜 | A10 生成 3 张定妆图、12 张分镜图，全部人工批准 | 动态画面、配音和成片可用 |
| 视频试跑 | 384×640 / 33 帧和 704×1280 / 49 帧各生成一个真实镜头 | 整集视频全部完成 |
| 正式视频 | 12 个正式镜头生成、完整解码并人工批准 | 配音、字幕及最终合成可用 |
| 配音与后期 | 8 条真实对白、逐句时间轴、SRT/ASS、1080×1920 / 24 FPS / H.264 / AAC / 45 秒 MP4 | 已完成最终人工审核 |
| 最终验收 | 成片人工批准，`--gpu-acceptance` 返回 `GPU E2E PASSED`，且 `acceptance_errors` 为空 | 所有其他驱动、模型或硬件组合也通过 |

下载结束、服务启动、`preflight.passed=true` 或离线测试通过，都不能代替 GPU E2E。

## 2. 实例、文件和授权语音准备

在魔搭创建或连接 GPU Notebook，进入开发界面的终端。确认实际分配的设备是 A10；资源规格、使用时限和界面入口以你的账号页面为准。官方操作和持久化目录说明可参考 [魔搭 Notebook 指南](https://github.com/modelscope/ms-cookbook/blob/main/content/chapters/chapter-04.md)。

开始长任务前准备好：

- Python 3.11 或 3.12，Git、FFmpeg、ffprobe，FFmpeg 包含 libass。
- 建议空闲磁盘 80–100 GiB。必需图像/视频权重约 27.45 GiB，TTS 权重约 1.03 GiB，还需要三个 Python 环境、下载缓存及输出空间。
- 三段有使用授权的角色参考声音，每段 3–10 秒，准确的参考文本和授权说明。分别对应林辰 `VOICE_001`、苏雨 `VOICE_002`、王总 `VOICE_003`。
- 能够访问 GitHub、PyTorch wheel 源、模型下载源。未提供可直接使用的真人音色，不能用测试音调或静音代替真实配音验收。

先检查实际环境：

```bash
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv
python3 --version
free -h
df -h /mnt/workspace
command -v git ffmpeg ffprobe
ffmpeg -hide_banner -filters | rg ' ass '
fc-match 'Noto Sans CJK SC'
```

如果没有 `rg`，最后的滤镜检查可改成 `ffmpeg -hide_banner -filters | grep ' ass '`。应看到 A10、约 24GB 显存、可用中文字体和 `ass` 滤镜。`fc-match` 返回其他字体表示发生了字体回退，需要检查是否支持中文。

Ubuntu/Debian 镜像缺少系统工具时，可由有系统安装权限的用户执行以下命令；root 用户去掉 `sudo`。其他发行版使用对应包管理器。

```bash
sudo apt-get update
sudo apt-get install -y git ffmpeg fontconfig fonts-noto-cjk \
  build-essential python3-dev python3-venv libgl1 libglib2.0-0 libgomp1
```

开发头文件和 venv 支持需要匹配实际选择的 Python。不要在 Notebook 内自行升级 NVIDIA 驱动来绕过兼容问题，先检查实例镜像和 PyTorch 构建。`nvidia-smi` 标注的 CUDA 版本不是已安装 PyTorch 的 CUDA runtime；兼容关系参考 [NVIDIA 官方说明](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html)，最后仍以真实 CUDA 运算和推理结果为准。

## 3. 获取源码并建立本轮证据目录

以下以 `/mnt/workspace` 为示例。先确认该目录确实是实例的持久存储；同名目录不保证不同实例共享文件。

```bash
cd /mnt/workspace
git -c http.version=HTTP/1.1 clone https://github.com/MashyGGG/modelscope-ai-short-drama-engine.git
cd modelscope-ai-short-drama-engine

export ENGINE_ROOT="$PWD"
export DRAMA_RUNTIME=/mnt/workspace/short-drama-runtime
export PYTHON_BIN=python3
export PYTHONUTF8=1
export CUDA_VISIBLE_DEVICES=0
export DRAMA_VALIDATION_REL="reports/gpu-validation/$(date -u +%Y%m%dT%H%M%SZ)"
export DRAMA_VALIDATION_DIR="$ENGINE_ROOT/$DRAMA_VALIDATION_REL"
mkdir -p "$DRAMA_VALIDATION_DIR"
set -o pipefail

git rev-parse HEAD > "$DRAMA_VALIDATION_DIR/source-commit.txt"
nvidia-smi > "$DRAMA_VALIDATION_DIR/nvidia-smi-before.txt"
free -h > "$DRAMA_VALIDATION_DIR/memory-before.txt"
df -h /mnt/workspace > "$DRAMA_VALIDATION_DIR/disk-before.txt"
```

若 `python3` 不是 3.11–3.12，将 `PYTHON_BIN` 改为镜像中已安装的对应解释器，例如 `python3.11`。已 clone 的仓库先检查本地修改，再按需要 `git pull --ff-only`，不要重复 clone 到非空目录。私有仓库使用你自己的正常 Git 认证，不将 token 写进日志或克隆 URL。

后续命令沿用这个终端及这些变量。日志目录名使用 UTC。`pipefail` 保证 `命令 | tee 日志` 保留原命令失败状态；**命令非零退出时，处理错误后再继续下一阶段**。

## 4. 安装环境与模型，暂不启动服务

在同一个 GPU 实例先使用准备模式，方便在启动 ComfyUI 前单独验证两套 CUDA 环境：

```bash
bash oneclick.sh --prepare-only 2>&1 | tee "$DRAMA_VALIDATION_DIR/01-install.log"
source "$DRAMA_RUNTIME/app-venv/bin/activate"
python -m scripts.preflight --models 2>&1 | tee "$DRAMA_VALIDATION_DIR/02-model-preflight.log"
```

三套环境分别为：

| 路径 | 用途 |
|---|---|
| `$DRAMA_RUNTIME/app-venv` | 业务 CLI、SQLite、HTTP 客户端与后期调度 |
| `$DRAMA_RUNTIME/comfy-venv` | ComfyUI、SDXL、IPAdapter、Wan 推理 |
| `$DRAMA_RUNTIME/tts-venv` | GPT-SoVITS |

`--prepare-only` 完成后打印的 `CPU PREPARATION COMPLETE` 只表示准备阶段完成，即使运行机器有 GPU，也没有完成本流程的生成验证。其安装日志在 `logs/install.log`，实际依赖在 `logs/app-pip-freeze.txt`、`logs/comfy-pip-freeze.txt`、`logs/tts-pip-freeze.txt`。

默认 PyTorch 源是 `cu128`。如需选择与实例匹配的其他构建，应在安装前设置并记录，例如 `export TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126`；这只是选项，不代表该组合已经在本项目通过。安装器不会自动修正已装好的另一种 CUDA 构建，修复或重建相应独立环境后须重新执行下一节检查。选择依据参考 [PyTorch 官方安装页](https://docs.pytorch.org/get-started/locally/)。

网络不通时可在安装前设置 `export DRAMA_MODEL_SOURCE=modelscope`，它仅影响 ComfyUI 权重下载；镜像缺失或 SHA256 不一致会报错。TTS 仍使用 Hugging Face，可配置可信的 `HF_ENDPOINT`。完整权重会复用校验通过的文件；下载中断支持 `.part` 续传。不要运行 `scripts.lock_models` 或 `scripts.vendor_upstream` 来绕过下载失败，这两个开发维护入口会更新验证基线。

## 5. 分别验证 ComfyUI 和 TTS 的真实 CUDA 运算

业务环境不安装 torch，必须使用对应环境的解释器。两个子进程顺序执行，退出后释放其测试占用：

```bash
(
for env_name in comfy-venv tts-venv; do
  echo "=== $env_name ==="
  "$DRAMA_RUNTIME/$env_name/bin/python" - <<'PY'
import torch
print("torch:", torch.__version__, "runtime CUDA:", torch.version.cuda)
assert torch.cuda.is_available(), "CUDA is unavailable"
print("device:", torch.cuda.get_device_name(0))
x = torch.randn((512, 512), device="cuda", dtype=torch.float16)
y = x @ x
torch.cuda.synchronize()
assert torch.isfinite(y).all().item(), "CUDA matmul returned non-finite results"
print("CUDA FP16 matmul PASSED")
PY
  probe_status=$?
  if [ "$probe_status" -ne 0 ]; then
    echo "CUDA probe failed; stop validation here."
    exit "$probe_status"
  fi
done
) 2>&1 | tee "$DRAMA_VALIDATION_DIR/03-cuda-probes.log"
```

日志必须同时出现两个环境的设备信息和两次 `CUDA FP16 matmul PASSED`。只出现一次、出现异常，或只有 `is_available=True`，都不算这一关通过。这个小运算通过也不保证 Wan 正式参数不会 OOM。

## 6. 启动受管 ComfyUI，执行在线预检

```bash
bash scripts/start_services.sh --lowvram 2>&1 | tee "$DRAMA_VALIDATION_DIR/04-start-comfy.log"
python -m scripts.preflight --live --models 2>&1 | tee "$DRAMA_VALIDATION_DIR/05-live-preflight.log"
cp reports/preflight.json "$DRAMA_VALIDATION_DIR/preflight-live.json"
curl -fsS http://127.0.0.1:8188/queue
```

这里选择原生 `--lowvram` 作为首次 A10 验证的保守启动方式；正式生成仍按指定尺寸运行。队列应没有其他运行中或待执行任务，预检应 `passed: true`、`errors: []`，设备信息应是实际 NVIDIA GPU。

服务只监听本机 8188。引擎会核对 PID、监听端口、工作目录、固定提交及运行时节点。不要另开一个 ComfyUI 或手工启动 GPT-SoVITS 占用 GPU；TTS 9880 端口应保持空闲，音频阶段会自动管理它。

在线预检主要检查服务、节点存在性及权重完整性；每个实际生成任务提交前还会校验 `/object_info` 的输入字段和模型选项。它们均不代替真实生成。阶段准入还要求空闲显存至少 14000 MiB、系统可用内存至少 12000 MiB；卸载后观测显存占用须回落至配置阈值以内。这些是 `config/hardware.yaml` 的当前值，不是对模型峰值的保证。

## 7. 创建示例，生成并审核角色

准备脚本已经导入过同一示例，可再次执行确认。内置计划重复导入不会重新生成资产：

```bash
python generate_episode.py --plan examples/episode_45s.json
python generate_episode.py --episode EP001 --stage characters --character CHAR_001 \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/06-character-smoke.log"
python engine.py review-list --episode EP001
```

从输出的真实 `ASSET_…` 和 `path` 找到林辰的定妆 PNG。使用 Notebook 文件区打开或下载原图，检查成年人脸、正面身份、写实风格、服装和肢体。确认后，将以下占位 ID 替换为实际值：

```bash
python engine.py review --asset ASSET_实际林辰定妆ID --decision approved \
  --reviewer "实际审核人" --notes "已检查身份、写实风格、肢体与服装"
python generate_episode.py --episode EP001 --stage characters \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/07-characters.log"
python engine.py review-list --episode EP001
```

再逐个检查并批准苏雨、王总。**三个 identity 均须人工批准**，角色才会锁定并可进入图片阶段。不要直接改数据库、把整个列表批量自动批准，或用上传的替代图片来证明本轮 A10 定妆生成通过。

定妆不满意时先拒绝，再生成该角色的新候选：

```bash
python engine.py review --asset ASSET_不合格ID --decision rejected --reviewer "实际审核人" --notes "具体问题"
python generate_episode.py --episode EP001 --stage characters --character CHAR_001 --regenerate
```

批准新的身份图会让相关下游分镜/视频失效，因此角色应先定好，再开始整集生产。

## 8. 生成并审核静态分镜

先验证包含角色的 `EP001_SH002`，再生成其余镜头；成功且输入未变的试跑任务会被复用：

```bash
python generate_episode.py --episode EP001 --stage images --shot EP001_SH002 \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/08-image-smoke.log"
python engine.py review-list --episode EP001
python generate_episode.py --episode EP001 --stage images \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/09-images.log"
python engine.py review-list --episode EP001 | tee "$DRAMA_VALIDATION_DIR/images-review-list.json"
```

逐张查看 12 个 image 类型的 PNG，检查原始尺寸 704×1280、无黑图、人物与定妆一致、同角色服装一致、镜头类型及场景正确、无明显多余人物/肢体错误。图片语义不会由技术检测器自动保证。

```bash
# 每个镜头分别替换为实际资产 ID 执行，直到 12 个镜头都批准。
python engine.py review --asset ASSET_实际分镜ID --decision approved --reviewer "实际审核人"
```

需要返工时用 `--stage images --shot EP001_SH002 --regenerate` 生成新候选；需要复现可追加 `--seed 123456`。批准新的图片会使该镜头的视频选定结果失效，应在视频生成前完成图片审核。

## 9. Wan 视频：先小尺寸，再正式尺寸，再整集

### 9.1 小尺寸链路试跑

```bash
python generate_episode.py --episode EP001 --stage videos --shot EP001_SH002 \
  --low-resolution --frames 33 2>&1 | tee "$DRAMA_VALIDATION_DIR/10-video-low.log"
python engine.py review-list --episode EP001
```

此步骤应使用已批准的真实分镜 PNG 作为 I2V 输入，产出 **384×640、16 FPS、33 帧、约 2.06 秒**的 MP4。打开播放，检查不是空文件、能完整播放、有人物动态和相对稳定的面部；同时检查作业目录的 `submitted.json`、`history.json`、`quality.json`。

该素材只用于链路验证。可以将此 video 候选标记为 rejected，注明“低分辨率试跑，不进入正式成片”；这不代表技术链路失败。不要批准它作为本轮正式镜头。

### 9.2 正式尺寸单镜头

```bash
python generate_episode.py --episode EP001 --stage videos --shot EP001_SH002 \
  --frames 49 2>&1 | tee "$DRAMA_VALIDATION_DIR/11-video-production-smoke.log"
python engine.py review-list --episode EP001
```

低分辨率试跑与本次输入参数不同，会创建不同任务；**第一次正式生成不需要 `--regenerate`**。应得到 **704×1280、16 FPS、49 帧、约 3.06 秒** MP4。确认 `quality.errors` 为空，并人工检查画面、动作、身份及稳定性，再批准这份正式候选。

### 9.3 正式整集

```bash
python generate_episode.py --episode EP001 --stage videos --frames 49 \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/12-videos.log"
python engine.py review-list --episode EP001 | tee "$DRAMA_VALIDATION_DIR/videos-review-list.json"
# 对每个镜头检查对应正式候选后逐个执行：
python engine.py review --asset ASSET_实际正式视频ID --decision approved --reviewer "实际审核人"
```

全部 12 个镜头必须选中正式视频。列表里可能同时有低分辨率试跑或旧候选，用 `quality` 的尺寸/帧数识别；同一镜头最后批准的候选成为选定素材。

**45 秒时间轴说明：**示例每镜头 3.75 秒，49/16≈3.06 秒。默认后期用约 0.69 秒末帧停留补足每个镜头，不是 45 秒全程都由 Wan 生成运动。执行命令会记录停留时间。若要求更长连续运动，可以先试 `--frames 61`，再整集统一使用 61 帧并重新选定；这会增加推理资源需求，不能假设 A10 必定成功。

33 帧只有约 2.06 秒，补到 3.75 秒超过默认 1 秒停留上限，渲染会拒绝。OOM 的处理方式和基准标记见第 13 节。

## 10. 注册授权音色，逐句配音并审核

将三段有授权的实际参考音频上传到 Notebook 持久目录，例如 `/mnt/workspace/authorized-voices/`。复制模板：

```bash
mkdir -p "$DRAMA_RUNTIME/voice-profiles"
cp examples/voice_profile.json "$DRAMA_RUNTIME/voice-profiles/linchen.json"
cp examples/voice_profile.json "$DRAMA_RUNTIME/voice-profiles/suyu.json"
cp examples/voice_profile.json "$DRAMA_RUNTIME/voice-profiles/wang.json"
```

用编辑器修改三份 JSON：

| 文件 | voice_id | 需要填写 |
|---|---|---|
| `linchen.json` | `VOICE_001` | 林辰的实际参考音频绝对路径、准确文本、授权说明 |
| `suyu.json` | `VOICE_002` | 苏雨的实际参考音频绝对路径、准确文本、授权说明 |
| `wang.json` | `VOICE_003` | 王总的实际参考音频绝对路径、准确文本、授权说明 |

逐份确认有相应声音授权后，将模板的 `authorized: false` 改为 `true`，填写真实 `license_note`。模板路径和参考文本不能原样保留。先使用默认官方预训练 v2 权重，不额外填写自定义权重。

```bash
python engine.py voice-import --json "$DRAMA_RUNTIME/voice-profiles/linchen.json"
python engine.py voice-import --json "$DRAMA_RUNTIME/voice-profiles/suyu.json"
python engine.py voice-import --json "$DRAMA_RUNTIME/voice-profiles/wang.json"

python generate_episode.py --episode EP001 --stage audio --shot EP001_SH002 \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/13-audio-smoke.log"
python engine.py review-list --episode EP001
```

音频阶段要求整集视频已审核。它先请求 ComfyUI 卸载并观测显存回落，再启动独立 GPT-SoVITS，逐句生成 PCM16 WAV，阶段返回前终止托管 TTS 并等待进程退出。不要提前手工启动 `api_v2.py`。

试听单句，检查普通话内容、音色、噪声、削波和实际语音是否合理，然后生成剩余对白：

```bash
python generate_episode.py --episode EP001 --stage audio \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/14-audio.log"
python engine.py review-list --episode EP001 | tee "$DRAMA_VALIDATION_DIR/audio-review-list.json"
# 试听每条实际对白后，逐条批准 8 个 audio 资产：
python engine.py review --asset ASSET_实际音频ID --decision approved --reviewer "实际审核人"
```

如需在 Jupyter Python 单元格试听，可使用 `IPython.display.Audio(filename="实际WAV绝对路径")`；图片可用 `IPython.display.Image`，MP4 可在文件区下载后播放。使用实际原始文件进行检查，不以终端显示“成功”代替试听/观看。

## 11. 时间轴预检、渲染与成片人工审核

先检查真实对白时长是否能放进各自镜头，不需要启动 GPU：

```bash
python - <<'PY'
import os
from pathlib import Path
from core.common import write_json
from core.state_manager import StateManager
from production.subtitle_generator import SubtitleGenerator
state = StateManager()
try:
    timeline = SubtitleGenerator().timeline(state, "EP001")
    for line in timeline:
        print(line["shot_id"], line["dialogue_id"], line["start_time"], line["end_time"], line["text"])
    assert len(timeline) == 8
    write_json(Path(os.environ["DRAMA_VALIDATION_DIR"]) / "timeline-precheck.json", timeline)
finally:
    state.close()
PY

python generate_episode.py --episode EP001 --stage render \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/15-render.log"
python engine.py review-list --episode EP001 | tee "$DRAMA_VALIDATION_DIR/final-review-list.json"
```

如果对白超出镜头时长，会给出镜头/对白 ID 并停止，不会截断语音。处理方法见第 13 节。

从 final 类型资产中选择本次输出，填写实际 ID，解析其预览路径：

```bash
export FINAL_ASSET_ID=ASSET_实际成片ID
export FINAL_MP4="$(python - <<'PY'
import os
from core.state_manager import StateManager
state = StateManager()
try:
    asset = state.asset(os.environ["FINAL_ASSET_ID"])
    assert asset["kind"] == "final" and asset["owner_id"] == "EP001"
    print(asset["path"])
finally:
    state.close()
PY
)"

ffprobe -v error -count_frames -show_format -show_streams -of json "$FINAL_MP4" \
  | tee "$DRAMA_VALIDATION_DIR/final-ffprobe.json"
ffmpeg -v error -xerror -i "$FINAL_MP4" -map 0 -f null - \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/final-full-decode.log"
```

应为 1080×1920、9:16、24 FPS、H.264 视频和 AAC 音频，时长约 45 秒；容器时间戳存在小数误差，渲染质量检查使用约 0.05 秒容差。完整解码命令退出码应为 0，通常没有错误输出。

完整播放成片，确认三个角色一致、12 个镜头齐全、对白没有截断或错位、中文字幕无缺字且不超出画面、没有异常黑屏或长时间冻结，再批准：

```bash
python engine.py review --asset "$FINAL_ASSET_ID" --decision approved \
  --reviewer "实际审核人" --notes "已完整播放，检查角色、12镜头、对白时序、中文字幕及画面质量"
```

渲染成功只创建 `review_required` 预览，未批准的成片不能通过最终 GPU 验收。

## 12. 执行 GPU 验收，保存真实证据

仍在实际 A10 实例上执行：

```bash
python engine.py report --episode EP001 --gpu-acceptance \
  2>&1 | tee "$DRAMA_VALIDATION_DIR/16-gpu-acceptance.log"
cp reports/EP001.json "$DRAMA_VALIDATION_DIR/EP001-gpu-acceptance.json"
nvidia-smi > "$DRAMA_VALIDATION_DIR/nvidia-smi-after.txt"
```

只有退出码为 0、JSON 的 `gpu_e2e_status` 为 `GPU E2E PASSED`、`acceptance_errors` 为空，才能记录通过。命令会检查当前 A10、四个 GPU 阶段实测记录、受管服务证据、选定资产及成功作业、ComfyUI 历史、文件摘要、最终技术质量和人工批准状态。导入素材、离线合成媒体或只有服务启动记录不能充当这些证据。

普通 `python engine.py report` 会写入 `NOT_RUN` 的普通报告，不能代替验收；它还会覆盖 `reports/EP001.json`，因此通过后保留上面那份专用副本。

需要留存的证据：

| 位置 | 内容 |
|---|---|
| `$DRAMA_VALIDATION_DIR/` | 本轮命令输出、设备与磁盘信息、源码提交、在线预检、时间轴、解码结果、GPU 验收副本 |
| `projects/EP001/jobs/JOB_…/` | 实际输入工作流、prompt_id 对应历史、PNG/MP4/WAV、逐项质量报告、字幕、FFmpeg 命令及成片 |
| `projects/EP001/runs/` | 阶段实测硬件、运行耗时、显存/系统内存采样峰值、错误及服务身份 |
| `logs/` | 安装日志、业务日志、ComfyUI/TTS 日志、三个环境的 pip freeze |
| `database/engine.sqlite` | 全部作业、选定资产及审核记录；备份时使用下面的 SQLite backup |
| `assets/characters/`、`assets/voices/` | 实际复用的角色/声音参考资产 |

显存峰值是约 1 秒采样结果，可能遗漏瞬时峰值；报告的各阶段耗时、失败镜头、重试/重生成次数、成片时长及文件大小都应按本轮实测填写。失败的旧候选可以留作审计，最终选定素材必须成功且已批准。

复制 [验证记录模板](../reports/templates/A10_VALIDATION_RECORD.md) 到本轮目录填写。所有生产命令结束后，备份数据库并打包（目标归档放在项目目录外，避免包含自身）：

```bash
cp reports/templates/A10_VALIDATION_RECORD.md "$DRAMA_VALIDATION_DIR/VALIDATION_RECORD.md"
python - <<'PY'
import os, sqlite3
from pathlib import Path
target = Path(os.environ["DRAMA_VALIDATION_DIR"]) / "engine.sqlite"
with sqlite3.connect("database/engine.sqlite") as source, sqlite3.connect(target) as backup:
    source.backup(backup)
print(target)
PY
tar -czf "/mnt/workspace/drama-EP001-validation-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" \
  projects/EP001 assets/characters assets/voices config workflows logs "$DRAMA_VALIDATION_REL"
```

归档含角色及声音素材，应保存在授权范围内。运行数据库和资产包含绝对路径，恢复时保持原项目及资产路径；此归档不包含数十 GB 模型和 Python 环境，不保证换一个目录/解释器版本即可直接恢复推理。

## 13. 中断、OOM 与常见问题

### 重新打开终端或实例重启

恢复同一代码目录、`DRAMA_RUNTIME` 和原验证目录变量，激活业务环境，重新 `set -o pipefail`。若实例重启，先 `bash scripts/start_services.sh --lowvram`，再做在线预检。数据库、资产及 `projects/EP001` 必须保留；不要删除数据库来解决失败任务。

```bash
cd /mnt/workspace/modelscope-ai-short-drama-engine
export ENGINE_ROOT="$PWD"
export DRAMA_RUNTIME=/mnt/workspace/short-drama-runtime
source "$DRAMA_RUNTIME/app-venv/bin/activate"
set -o pipefail
python engine.py status --episode EP001
python generate_episode.py --episode EP001 --stage videos --shot EP001_SH003 --retry
```

同参数失败/中断用 `--retry`；`--resume` 会从所有阶段依次检查，遇到人工审核关卡会停止。仍存在的 prompt 会重新挂接，已成功且输入未变的任务会复用。服务重启且历史清空后，该失败/中断任务可能需要重新推理。

### OOM：先确认任务结束，再改变参数

`--retry` 会沿用记录的 seed、尺寸、帧数和候选标识，**不能用它同时改变失败任务的参数**。改参数应新建候选：

```bash
# 仅验证较轻的链路，不足以填满示例镜头时间轴：
python generate_episode.py --episode EP001 --stage videos --shot EP001_SH003 \
  --regenerate --low-resolution --frames 33
# 若需要在低分辨率下保留约 3.06 秒时长，可另试 49 帧：
python generate_episode.py --episode EP001 --stage videos --shot EP001_SH003 \
  --regenerate --low-resolution --frames 49
```

低分辨率回退完成时记录实际参数，不能将其写成“704×1280 基准已通过”。不满足基准的结果也不要为了验收而伪改 `quality.json`。先检查其他 GPU 进程和内存，再考虑更小输入、原生卸载或合适的实例；不保证调整后必然成功。

### 需要重启本项目 ComfyUI 或更改 `--lowvram`

`start_services.sh` 对已启动服务只会复用，不会改变启动参数。以下仅在确认队列空闲时停止本项目受管进程，然后重新启动；没有验证身份的外部服务应由其所有者处理。

```bash
python - <<'PY'
import psutil
from core.common import config
from core.comfy_client import ComfyClient
from core.service_identity import managed_comfy_identity
url = config("production")["comfy_url"]
ComfyClient(url).require_idle()
identity = managed_comfy_identity(url)
process = psutil.Process(identity["pid"])
process.terminate()
process.wait(timeout=30)
PY
bash scripts/start_services.sh --lowvram
python -m scripts.preflight --live --models
```

### 对白超出镜头时长

先试听并查看 `timeline-precheck` 报错。可在该声音配置的对应 `emotion_profiles` 中调整受支持的 `speed_factor`，重新导入该音色、生成并重审受影响对白。导入声音配置会使该音色的已选音频失效，因此不要无目的重复导入。若仍不合适，修改剧本/分镜时间轴并使用新的 episode/shot/dialogue ID；首版不支持原地修改已导入计划，也不会自动截掉对白。

### 其他排查

| 现象 | 排查与处理 |
|---|---|
| `No NVIDIA GPU` / CUDA 运算失败 | 确认连接 GPU 运行时，并在两个推理环境各自测试；不要用业务环境或系统 torch 的结果替代 |
| `Insufficient free VRAM/RAM` / 卸载未回落 | 查看 `nvidia-smi`、`free -h`、`logs/comfyui.log`；停止你自己的竞争任务，等待原生卸载，不直接降低检查阈值冒充成功 |
| 8188 / 9880 占用 | 检查占用服务；只使用本项目受管 ComfyUI，TTS 由音频阶段启动/停止 |
| `Missing runtime node` / 模型选项不可用 | 查看 ComfyUI 自定义节点启动错误，核对独立环境、固定源码和模型路径；不要猜节点或手改为其他模型名 |
| 模型缺失 / 校验失败 | 重跑 `python -m scripts.download_models` 或 `python -m scripts.download_tts_models`，保留已校验的权重；不要重写锁清单绕过 SHA256 |
| GPT-SoVITS 启动或合成失败 | 查看 `logs/tts.log`，检查其 CUDA 运算、预训练模型、3–10 秒参考声音、准确参考文本与路径 |
| `approved … required` | 用 `review-list` 找到该身份/镜头/音频的真实候选并人工审核，不直接改数据库状态 |
| 图片/音频审核后再次要求审核 | 上游参考或声音配置变更会使下游失效；重新生成并审核对应资产 |
| 字幕缺字或被遮挡 | 检查中文字体、ASS、手机竖屏实际预览；修复后重新渲染并批准新的 final |
| Git 提示受管 checkout 有修改 | 保留修改用于排查，核对锁定提交；不要直接覆盖或删除现场来宣称使用了固定基线 |

若某一关未通过，记录失败阶段、镜头/任务 ID、实际参数、错误及日志路径，将结论保留为未通过/未完成，后续继续恢复验证。
