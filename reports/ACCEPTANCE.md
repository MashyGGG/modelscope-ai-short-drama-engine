# 首版工程验收记录

日期：2026-11-14。项目：`modelscope-ai-short-drama-engine`。

**状态：OFFLINE PASSED；GPU E2E NOT RUN。尚无真人 AI 短剧成片，不得将离线合成测试视频作为真人生成结果。**

## 已交付

- Python 业务引擎、独立 Director、角色/服装/表情/声音/场景资产管理、9 种分镜类型、CLI 审核、可复用 Character ID。
- SQLite 业务表、独立阶段作业、内容指纹去重、失败重试、固定 UUID prompt_id、超时重新挂接、单镜头候选重生成、审核失效传播。
- ComfyUI HTTP 上传、提交、历史轮询、实际输出下载；SDXL FaceID 和 Wan2.2 I2V API/UI 工作流。
- 模型大小和 SHA256 校验、显存及系统内存检查、阶段串行 GPU 锁、异步卸载后的观测确认、OOM 状态及建议。
- 独立进程 GPT-SoVITS api_v2 适配、授权参考声音导入、逐句 WAV、实际时长时间轴、SRT/ASS、FFmpeg 视频/旁白/音乐/环境声/字幕合成。
- 四层技术质量检查及需要人工判断的语义检查项；中文 README、安装/下载/预检脚本、固定上游源码和模型清单、45 秒示例计划。

## 已执行验证

| 检查 | 结果与范围 |
|---|---|
| Python 依赖一致性 | `pip check`；业务开发环境无冲突 |
| 静态检查 | Ruff、Python 编译检查 |
| 上游契约 | 固定提交的源码 AST 与导入的官方工作流连接检查；3 份 API 工作流及 UI 往返转换 |
| 自动测试 | 32 项通过，详见 [pytest.xml](pytest.xml) |
| Mock ComfyUI | 本机 HTTP 服务，真实 multipart PNG 上传、动态 I2V 绑定、UUID 提交、历史查询、PNG/MP4 下载 |
| 故障与恢复 | OOM、超时挂接、失败候选保留 seed/标识、成功资产不重复生成、文件改变拦截、工作进程退出后的资产恢复 |
| 审核与资源 | 未审核身份/图片/视频的阶段拦截，身份更换下游失效、跨集引用、GPU 文件锁、等待卸载完成、外部 TTS 拦截、无 GPU 拦截 |
| 配音协议 | GPT-SoVITS 请求字段与受支持的情绪控制映射；音频使用合成音调，不是 AI 配音 |
| 后期合成 | 使用真实 FFmpeg 合成 12 个合成图案镜头和 8 段测试音调，验证 45 秒、9:16、24 FPS、H.264/AAC、字幕和完整解码；为加速测试输出 216×384 |
| 安装脚本 | Bash 语法检查；没有在本机执行 Linux GPU 依赖安装 |

完整命令、退出码和测试输出见 [offline-validation.json](offline-validation.json)、[运行日志](../logs/offline-validation.log)。上游原始源码校验记录见 [upstream-contracts.json](upstream-contracts.json)，本机设备预检见 [preflight.json](preflight.json)。

离线后期测试素材保存在 `reports/offline_media/SYNTHETIC_45s_TEST_ONLY.mp4`，同目录的 NOTICE 明确其用途。它不包含真人、不包含生成语音，也未使用 GPU。

## 尚未验证或尚未实现的范围

1. 本机未检测到 NVIDIA GPU；没有魔搭 Notebook 的连接或执行环境。因此未执行 SDXL 人物定妆、FaceID 跨镜头一致性、Wan2.2 动态镜头、GPT-SoVITS 真人对白及 1080×1920 真人最终成片的 A10 实测。
2. 704×1280 / 49 帧、所选 CUDA/PyTorch/驱动组合、ComfyUI/IPAdapter/GPT-SoVITS 的实际安装启动和显存峰值，需要在目标 A10 实例完成验证。安装器记录实际 pip freeze；不宣称所有间接依赖已锁定或兼容验证通过。
3. 身份、肢体、多余人物、场景语义、情绪、中文发音、字幕可读性仍依赖明确的人工审核。没有实现额外视觉语义模型或自动 InsightFace 相似度评分。
4. CosyVoice 只交付替换接口；口型同步、视频超分、插帧和 Depth ControlNet 没有纳入首版主流程。OpenPose 接受预先制作的控制图。
5. 未附带授权真人声音或商用许可。默认 FaceID/InsightFace 路线有独立用途限制，需要遵循上游模型卡，不能据此宣称整套系统可以商用。

## A10 验收入口

详细操作、每一步通过条件、低分辨率试跑、恢复及日志归档见 [魔搭 A10 GPU 验证流程](../docs/GPU_VALIDATION.md)，本轮实测结果使用 [验证记录模板](templates/A10_VALIDATION_RECORD.md) 记录。本文件现有的离线结果不会因此变成 GPU 通过结果。

按 README 在 GPU Notebook 安装，依次完成角色、图片、视频、配音及成片的人工审核后执行：

```bash
python engine.py report --episode EP001 --gpu-acceptance
```

该命令要求当前真实 A10、受管 ComfyUI 进程及提交证据、各 GPU 阶段监控记录、已成功生成且摘要未变的全部已审核资产，以及 1080×1920 / 24 FPS / 45 秒最终文件的完整解码。缺少任何条件会返回失败并列出原因。普通报告和离线验证不会给出 GPU E2E PASSED。
