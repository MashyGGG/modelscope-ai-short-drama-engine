# ModelScope AI Short Drama Engine

在魔搭 NVIDIA A10 24GB Notebook 上运行的真人短剧生产引擎。Python 管理剧本、共享角色资产、分镜、SQLite 任务、逐级人工审核和 FFmpeg 后期；ComfyUI 执行 SDXL / FaceID / Wan2.2 推理，GPT-SoVITS 使用独立环境。

**当前交付为可执行的首版工程。已完成的验证范围见 [验收报告](reports/ACCEPTANCE.md)。本机没有 NVIDIA GPU，尚未完成魔搭 A10 真人素材及最终成片验收，不能标记 GPU E2E PASSED。**

在魔搭实际验证时，请按 [A10 GPU 完整验证流程](docs/GPU_VALIDATION.md) 执行：安装与 CUDA 检查 → 定妆审核 → 分镜审核 → 小尺寸/正式视频 → 授权配音 → 合成预览 → GPU 验收与证据归档。实测结果填写 [验证记录模板](reports/templates/A10_VALIDATION_RECORD.md)。

## 快速开始

在 Linux GPU Notebook 项目根目录执行。需要 Python 3.11–3.12、Git、FFmpeg（含 libass）、ffprobe；建议 A10 24GB、32GB RAM、至少 80–100 GiB 空闲磁盘。依赖编译可能需要系统已安装 gcc/g++、Python 头文件、libGL、libgomp；脚本不修改系统驱动或系统 Python。

```bash
bash oneclick.sh
source .runtime/app-venv/bin/activate
python generate_episode.py --stage characters
python engine.py review-list --episode EP001
```

安装会下载约 27.45 GiB 必需图像/视频权重、约 1.03 GiB 语音权重，以及三个独立环境的依赖。默认仅启动 ComfyUI，保留 TTS 关闭。首次安装耗时取决于下载和编译。可先在 CPU 实例执行 `bash oneclick.sh --prepare-only`，但环境和模型必须位于 GPU 实例也能访问的存储中；该命令不会进行 GPU 生成验收。

为持久存储指定运行目录：

```bash
export DRAMA_RUNTIME=/mnt/workspace/short-drama-runtime
bash oneclick.sh
source "$DRAMA_RUNTIME/app-venv/bin/activate"
```

这个变量只移动依赖、模型和服务；SQLite、角色资产、剧集、日志仍保存在本项目目录。跨实例迁移时需要一起保留这些数据。ComfyUI、TTS 和业务 Python 环境不能互换。

国内下载可使用 `DRAMA_MODEL_SOURCE=modelscope bash oneclick.sh`；魔搭镜像仍须匹配原权重的固定 SHA256，镜像不存在或内容不同会报错，不会接受不同权重。TTS 默认使用 Hugging Face，可设置可信的 `HF_ENDPOINT`。联网条件和镜像可用性需要在实际 Notebook 验证。

## 第一集：45 秒示例

内置《离婚当天，我继承了千亿集团》，林辰、苏雨、王总三个成年虚构角色、一个场景、12 个镜头、8 句对白，总时长 45 秒。完整剧本及分镜在 [examples/episode_45s.json](examples/episode_45s.json)。

```bash
python generate_episode.py --plan examples/episode_45s.json
# 等价的内置标题入口，只规划，不自动消耗 GPU：
python generate_episode.py --title "离婚当天，我继承了千亿集团" --duration 45 --characters 3 --aspect-ratio 9:16
```

1. 生成定妆图，打开 `review-list` 给出的真实文件路径，检查脸、肢体、风格及服装；逐个审核。

```bash
python generate_episode.py --stage characters
python engine.py review-list --episode EP001
python engine.py review --asset ASSET_实际ID --decision approved --reviewer "你的名字" --notes "已检查角色身份、肢体与服装"
```

2. 角色身份批准后自动锁定，所有镜头引用该角色的同一身份资产。生成分镜并逐个审核。

```bash
python generate_episode.py --stage images
python engine.py review-list --episode EP001
python engine.py review --asset ASSET_实际ID --decision approved --reviewer "你的名字"
```

3. 先验证一个较小的动态镜头，再验证正式尺寸；视频生成后也需人工审核。

```bash
python generate_episode.py --stage videos --shot EP001_SH002 --low-resolution --frames 33
# 低分辨率结果只用于验证；不同参数会创建正式任务，无需 --regenerate：
python generate_episode.py --stage videos --shot EP001_SH002 --frames 49
python generate_episode.py --stage videos --frames 49
python engine.py review-list --episode EP001
python engine.py review --asset ASSET_实际视频ID --decision approved --reviewer "你的名字"
```

默认视频为 704×1280、16 FPS、49 帧、batch=1、FP16 模型。49/16≈3.0625 秒，不等于示例每镜头 3.75 秒；后期允许最多 1 秒的末帧停留并将停留时间写入执行命令。示例每镜头约停留 0.69 秒。如希望全部时间都有生成运动，可使用 `--frames 61`（约 3.81 秒）生成候选，后期裁切到 3.75 秒。低分辨率 33 帧的验通素材不足以完成此时间轴，渲染会拦截，不会悄悄拉伸。

OOM 会记录镜头 ID、任务状态及降低尺寸/帧数、CPU 文本编码器、`--lowvram` 的建议。首次服务可通过 `bash scripts/start_services.sh --lowvram` 启动；已启动的服务需要由使用者先停止。引擎不会为了重试而自行中断外部任务。

4. 准备每个角色的授权参考语音，注册后逐句配音。

复制 [examples/voice_profile.json](examples/voice_profile.json)，分别填写 `VOICE_001`、`VOICE_002`、`VOICE_003` 的 `reference_path`、准确文本和授权说明。该模板默认 `authorized: false`；确认有授权后才填写 `true`。系统不附带任何真人克隆音色。

```bash
python engine.py voice-import --json /mnt/workspace/voice_linchen.json
python engine.py voice-import --json /mnt/workspace/voice_suyu.json
python engine.py voice-import --json /mnt/workspace/voice_wang.json
python generate_episode.py --stage audio
python engine.py review-list --episode EP001
python engine.py review --asset ASSET_实际音频ID --decision approved --reviewer "你的名字"
```

参考语音导入后复制到共享文件系统，转换为单声道 PCM16 WAV，要求 3–10 秒、非静音、无明显削波。GPT-SoVITS `api_v2` 没有参考音频上传接口，API 使用服务端文件路径，因此首版要求本机托管服务和共享文件系统。

音色默认使用固定的官方 v2 GPT/SoVITS 权重。自定义权重必须同时提供 `gpt_weights` 与 `sovits_weights`。每句记录实际音频时长、模型及参考音频摘要。`emotion_profiles` 映射情绪到官方支持的 `speed_factor`、`temperature`、`top_p`、`top_k`，不虚构 `emotion` API 参数；真实情感表现仍需试听。`CosyVoiceAdapter` 保留可替换接口，尚未实现其网络传输。

5. 合成、预览、审核最终成片，再运行 A10 验收。

```bash
python generate_episode.py --stage render
python engine.py review-list --episode EP001
# 打开 final 类型的 MP4，检查中文字幕、对白时序与全部镜头后批准：
python engine.py review --asset ASSET_实际成片ID --decision approved --reviewer "你的名字"
python engine.py report --episode EP001 --gpu-acceptance
```

最终默认输出 1080×1920、24 FPS、H.264/AAC MP4；16→24 FPS 使用普通帧率转换。SRT、ASS、逐句时间轴、实际 FFmpeg 命令、质量报告和预览视频都保存在 `projects/EP001/jobs/JOB_…/`。对白按 WAV 的实际时长定位，超过镜头边界会拦截，需缩短文本或重新规划时间轴。字幕使用 Noto Sans CJK SC，请在 Notebook 安装对应中文字体并人工检查；缺字不能由技术解码测试发现。

背景音乐通过剧集 `music_path` 指定；环境音效使用 `effect_tracks: [{"path": "绝对音频路径", "start_time": 2.0, "volume": 0.3}]`。旁白采用对白项的 `mode: "narration"`，同样使用已注册角色的声音。音轨不会凭音效建议文本自动生成。

## 其他故事与角色资产

配置任意兼容 chat/completions 协议的文本服务，输入故事大纲。未配置时会明确报错，不会把套用的示例冒充新剧本。

```bash
export DRAMA_LLM_URL=https://你的服务/v1/chat/completions
export DRAMA_LLM_MODEL=你的模型名
export DRAMA_LLM_KEY=你的密钥
python generate_episode.py --title "雨夜归人" --outline "写入你的故事大纲" --duration 45 --characters 3
```

导演返回的 JSON 必须通过关系、时长、角色数、镜头类型及对白校验。直接导入自己的计划也可使用 `--plan 文件.json`。不同剧集使用不同 `episode_id`、镜头 ID、对白 ID；相同 Character ID 会复用角色及已审核身份图。已导入剧集的内容保持不可变，修改剧本请另建剧集 ID，保留审核历史。

```bash
python engine.py character-create --json /path/character.json
python engine.py character-edit --character CHAR_001 --json /path/changes.json
python engine.py character-import --character CHAR_001 --file /path/face.png
python engine.py character-import --character CHAR_001 --kind wardrobe_default --file /path/suit.png
python engine.py character-import --character CHAR_001 --kind expression_smile --file /path/smile.png
python generate_episode.py --stage characters --character CHAR_001 --angle profile
python generate_episode.py --stage characters --character CHAR_001 --angle full_body
python engine.py character-shots --character CHAR_001
python engine.py scene-import --episode EP001 --scene SCENE_001 --file /path/lobby.png
```

导入图像、衣服、场景及多角度参考同样先审核。真实人物在角色定义中设置 `fictional: false`，必须记录 `likeness_authorized: true`，导入时再提供 `--authorized`。编辑身份描述会解锁身份并使相关下游资产失效。更换已批准的衣服/场景/身份图也会使受影响镜头失效。

可选衣服/场景图片条件和 OpenPose：先运行 `python -m scripts.download_models --optional` 下载独立的 SDXL PLUS 适配器和 SDXL OpenPose ControlNet。`shot.pose_reference` 必须是预先制作的 OpenPose 控制图，首版不内置人体姿态预处理器。`shot.reference_images` 可传入其他构图/场景参考图。

多人物同框使用 `shot.identity_masks: {"CHAR_001": "/path/a.png", "CHAR_002": "/path/b.png"}`；每张遮罩为与分镜相同尺寸的 RGBA PNG，角色区域 alpha=0、其余区域 alpha=255（符合 `LoadImage` 返回的反向 alpha MASK）。不同角色的可见区域需要独立指定。示例使用单个可辨识人物、正反打和匿名虚化肩膀，避免默认将两张人脸融合。

## 恢复、候选与任务审计

```bash
python generate_episode.py --resume
python generate_episode.py --stage videos --shot EP001_SH003 --retry
python generate_episode.py --stage images --shot EP001_SH003 --regenerate
python engine.py review --asset ASSET_实际ID --decision rejected --reviewer "你的名字" --notes "脸部不稳定"
python engine.py status --episode EP001
python engine.py plan-export --episode EP001 --output /path/plan.json
python engine.py report --episode EP001
```

每次默认生成一个候选。`--regenerate` 必须指定单个镜头/角色，创建独立候选和文件路径；人工批准一个候选后成为该槽位的选定资产。旧候选保留。未经批准的图片不能进入视频，未经批准的视频不能进入音频/合成，未经批准的音频不能进入最终时间轴。

初次生成使用计划中的固定 seed；重新生成默认选择并记录新 seed。需要复现指定候选时追加 `--seed 123456`，仍需指定单个 `--shot` 或 `--character`。

任务键由输入参数、依赖资产摘要、工作流代码/模板、配置和模型版本共同确定。成功且文件摘要未变的任务直接复用；失败任务需 `--retry` 或 `--resume`。执行前持久化客户端 UUID `prompt_id`，中断后先检查 ComfyUI 历史/队列并重新挂接，避免重复提交。若服务重启或历史被清空，允许恢复时重新执行该任务。OS 文件锁在进程退出后自动释放，各生成阶段仍共享同一 GPU 锁，不启动后台并发 worker。

SQLite 位于 `database/engine.sqlite`，包含文档要求的 11 张业务表及审核日志。SQL 源码在 [database/schema.sql](database/schema.sql)。每个作业目录包含 `submitted.json`、`history.json`、`quality.json` 和真实下载输出。`/free` 是异步请求，切换阶段会等待实测可用显存回落；未回落即报错。外部 TTS 占用端口时会阻止图像/视频阶段，托管 TTS 退出后才结束音频阶段。

生成阶段只连接本项目脚本启动的本机 ComfyUI，检查记录的 PID、监听端口、工作目录和固定提交，避免把另一套同名模型的服务误当作已校验的运行环境。8188 端口被其他服务占用时需要先处理该服务；本项目不会接管它。

报告记录各阶段耗时、显存/系统内存采样峰值、失败镜头、重试及重新生成次数、输出时长和大小。峰值是 1 秒采样值，可能遗漏瞬时峰值。普通 `report` 永远不授予 GPU 验收通过；只有 `--gpu-acceptance` 在 A10 上核对真实执行记录、全部已审核资产和完整解码后才可能输出 `GPU E2E PASSED`。

## 开发验证

无 GPU 的 Windows/Linux 均可运行业务层与测试；安装生成环境的 Bash 脚本面向魔搭 Linux。

```bash
python -m venv .venv-dev
# Linux: source .venv-dev/bin/activate
# Windows PowerShell: .\.venv-dev\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m scripts.preflight
python -m scripts.validate_upstream
python -m ruff check .
python -m pytest -q --junitxml=reports/pytest.xml
# 一次执行并保存日志、JUnit 和离线媒体测试证据：
python -m scripts.validate_offline
```

测试运行本地 Mock HTTP ComfyUI、合成测试信号和 FFmpeg，覆盖图片真实上传/下载、I2V 参数、OOM、超时重新挂接、任务去重、文件篡改、审核、GPU 锁、逐句音频及 45 秒后期时间轴。离线合成图案/音调不是真人短剧，也不是 GPU 推理通过的证明。身份一致性、肢体、场景语义、中文发音和字幕可读性由人工审核；当前未实现视觉语义检测模型或 InsightFace 相似度评分。

## 上游核对与使用范围

固定的提交、原始 URL、SHA256 见 [config/upstream-lock.json](config/upstream-lock.json)，原始 UI 工作流及相关源码保留在 `docs/upstream/`。工作流先导入官方 UI 并检查连接，再导出业务 API/UI 版本；静态源码契约通过不代表实际 GPU 节点加载成功，每次提交仍用运行实例 `/object_info` 校验。

- [ComfyUI 官方 Wan2.2 工作流](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_5B_ti2v.json)
- [IPAdapter 官方模型配对说明](https://github.com/cubiq/ComfyUI_IPAdapter_plus)
- [GPT-SoVITS 官方 API](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)
- [FaceID 模型卡的非商业用途限制](https://huggingface.co/h94/IP-Adapter-FaceID#non-commercial-use)

默认 FaceID/InsightFace 方案应按研究、非商业用途处理，不能因 SDXL checkpoint 可商用就认定整条链路可商用。商业生产需要取得相应模型授权或替换身份控制方案。自定义角色/声音还需相应肖像及声音授权。

业务依赖固定在 requirements 文件；ComfyUI/GPT-SoVITS 源码固定提交，GPU 框架/上游间接依赖在目标 Notebook 安装并记录 `logs/*-pip-freeze.txt`，尚未声称这些间接依赖和驱动组合已验证。`TORCH_INDEX_URL` 可调整 CUDA wheel 源；默认 cu128，需要目标驱动支持。项目源码采用 GPL-3.0，随附上游源码的许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
