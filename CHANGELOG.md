# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-21

首个可用版本，从一个真实作业（两场北大讲座 102 分钟，要写 ≥500 字听课笔记）里长出来的。

### Added

- `lecture_probe` / `lecture_transcribe` / `lecture_status` 三个工具，`name: lecture-asr`
- `lecture_cli.py`：probe / run / status / worker 四个子命令，可脱离 DSH 独立使用
- 10 分钟分段 + `segs/NNN.jsonl` 落盘 → 超时、崩溃、断电都能续跑
- 分块并行：N 个 worker 进程各持一份 int8 模型，24 核机器上 102 分钟音频约 5 分钟
- `progress.json` 进度文件，供 `lecture_status` 轮询
- `setup/install.py`：不依赖 pip，直接取 PyPI wheel 解压并递归装依赖
- `setup/fetch_model.py` + `setup/dl_parallel.py`：hf-mirror / ModelScope 带 UA 下载，model.bin 12 线程分块
- `cordis.patch.yml` bundle 注入，一行启用/停用
- `smoke.mjs`：桩掉 `defineTool` 后加载插件，无需 DSH 即可跑通 probe 与 transcribe

### Fixed

- worker 的 `seg_dir` 改走 `--out`，修掉 argparse `rc=2` 导致「0 段、2 秒返回 ok」的假成功
- `duration()` 临时文件改为 `_dur_<pid>.txt`，修掉多进程 `WinError 32: 另一个程序正在使用此文件`
- 绕开 faster-whisper 1.2.x 调 `av.open(metadata_errors=...)` 在 PyAV 19 上的 `TypeError`

### Known issues

- 中文专名同音错字（风物→风雾、朱瑾→朱锦），需要人工复核
- 无说话人分离；无 GPU 分支
