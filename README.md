# dsh-plugin-lecture

> 给 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的**离线讲座转写**插件：任意音视频 → 逐字稿 `.txt` + 字幕 `.srt`。纯 CPU、断点续跑、不发一个字节到云端。

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![runtime](https://img.shields.io/badge/runtime-CPU%20only-lightgrey)

---

## 为什么有这个

录播课、组会、讲座动辄一两个小时。要写笔记、要检索，第一步永远是**把声音变成文字**。
云服务可以，但一份内部讲座上传到第三方，很多人是不愿意的；装了 `whisper` 命令行又常常撞上
`ffmpeg not found`、`cuDNN 版本不对`、`模型下载 403` 这些坑。

这个插件把这条链路固定下来：本地 faster-whisper（int8）、固定 10 分钟分段、分段落盘可续跑，
模型和依赖集中放在一个目录里跨项目复用，坏掉就按 `setup/` 里的脚本重建。

## 三个工具

| 工具 | 输入 | 输出 |
|---|---|---|
| `lecture_probe` | `media` | 时长、是否有音轨/画面（秒回，先判断值不值得转） |
| `lecture_transcribe` | `media`，可选 `out_dir / lang / workers / threads / timeout_s / force` | `.txt` 逐字稿 + `.srt` 字幕 + 字数/句数/耗时 |
| `lecture_status` | `out_dir` | `progress.json` 里的阶段、已完成段数、百分比 |

在 DSH 里直接说「把这个讲座转成逐字稿」即可，模型会自己调 `lecture_probe` 判断长度、再调
`lecture_transcribe`；超时返回的是「已完成 3/6 段」，同样参数再调一次就从断点续跑。

## 安装

### 作为 DSH 插件

```powershell
# 1. 拿到本包（git clone 或直接放）到任意目录，例如 D:\Desktop\dsh-plugin-lecture
# 2. 在 profile 的 node_modules 下建一个目录链接（Windows 用 junction）
$a = "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-plugin-lecture"
New-Item -ItemType Junction -Path $a -Target "D:\Desktop\dsh-plugin-lecture"

# 3. 把包名加进 profile 的 dsh.profile.bundles（package.json）
#    "dsh-plugin-lecture" 追加到 dsh.profile.bundles 数组
# 4. 重启 DSH
```

包内的 `cordis.patch.yml` 会插入一行插件：

```yaml
- insert:
    - id: lecture-asr
      name: 'dsh-plugin-lecture'
```

不想用了：把 `dsh.profile.bundles` 里那行删掉重启即可，不会影响 DSH 启动（行级失败是静默的）。

### 只当命令行工具

```powershell
python lecture_cli.py probe  "D:\videos\lecture.mp4"
python lecture_cli.py run    "D:\videos\lecture.mp4" --out D:\out\lecture --workers 4 --threads 5
python lecture_cli.py status D:\out\lecture
```

`run` 最后一行输出一个 JSON（`ok/transcript/chars/segments/elapsed_s`），方便被别的脚本消费。

## 首次准备

模型和依赖不进仓库，也不进插件包，统一放在 `LECTURE_HOME`（默认 `%USERPROFILE%\.dsh\lecture`）：

```powershell
python setup\install.py            # 从 PyPI 取 wheel 解压到 pylibs（不需要 pip）
python setup\fetch_model.py        # config.json / tokenizer.json / vocabulary.txt
python setup\dl_parallel.py        # model.bin：12 线程分块，比单连接快 4-8 倍
```

目录长这样：

```
%USERPROFILE%\.dsh\lecture\
├── models\small\        model.bin(≈460MB) + tokenizer/config/vocabulary
├── pylibs\              faster-whisper / ctranslate2 / numpy / onnxruntime / av …
│   └── imageio_ffmpeg\binaries\ffmpeg-win-x86_64-*.exe
└── downloads\           wheel 缓存
```

> 为什么不用 `pip install`：部分受管环境里 pip 写 `*.whl.metadata` 会被直接拒绝
> （`Permission denied: '...whl.metadata'`，改 TMP 也没用）。`setup/install.py` 只做
> 「取 wheel → 解压 → 读 METADATA 递归装依赖」，绕开 pip 的临时文件。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LECTURE_HOME` | `%USERPROFILE%\.dsh\lecture` | 模型/依赖/ffmpeg 的根目录 |
| `LECTURE_PYTHON` | 自动探测 | 指定解释器（需 3.10+，依赖来自 `pylibs`） |
| `LECTURE_MODEL` | `small` | `setup/*.py` 下载哪个模型；运行时的模型目录是 `models/small` |
| `FORCE_SLOW` | 未设置 | 设了才允许 `fetch_model.py` 单连接下 `model.bin` |

## 性能（实测）

| 场景 | 耗时 |
|---|---|
| 60 秒音频，1 进程 × 6 线程 | 22–24 秒（≈2.6× 实时） |
| 102 分钟录音，4 进程 × 5 线程 | ≈5 分钟（≈20× 实时） |
| 单进程 × 5 线程（参考） | ≈10× 实时 |

24 逻辑核 Windows / CPU int8 / `small` 模型。想更准换 `medium`（`setup/dl_parallel.py medium 16`，
并把 `lecture_cli.py` 的模型目录指到 `models/medium`），代价是 3-5 倍耗时。

## 已知限制

- **同音错字**：机器转写会把人名地名写错（风物→风雾、朱瑾→朱锦），专名务必对着 PPT 或原文复核。
- 无标点、按句切分；需要断句可参考 `.srt` 的时间轴。
- 不做说话人分离（diarization），多人讨论会糊成一段。
- 只支持 CPU 路径（`compute_type=int8`），没有 CUDA 分支。

## 工作原理

```
probe(duration) ──► ffmpeg -vn -ac 1 -ar 16k ──► wave 分块读 (10min/段)
                                                     │
             ┌───────────────────────────────────────┘
             ▼
   N 个 worker 进程（每个 int8 WhisperModel）──► segs/NNN.jsonl（落盘即断点）
             │
             ▼
   combine：按 start 排序 → transcript.txt + transcript.srt
```

更细的设计取舍（为什么用 `wave` 不用 `av`、为什么每段 600 秒、为什么并行靠进程而不是线程）
写在 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 开发

```powershell
node smoke.mjs "D:\videos\lecture.mp4"          # 桩掉 defineTool，加载插件并跑一次 probe
node smoke.mjs "$env:TEMP\clip.wav" transcribe  # 连转写一起验
```

```
.
├── lib/index.js          # Cordis 插件：defineTool 注册三个工具
├── lecture_cli.py        # 内核 CLI：probe / run / status / worker
├── cordis.patch.yml      # 注入 lecture-asr 行
├── setup/                # 首次准备：install / fetch_model / dl_parallel
├── smoke.mjs             # 无 DSH 也能跑的自测
└── docs/ARCHITECTURE.md
```

## License

MIT。仓库地址已填好（`https://github.com/xjj524/dsh-plugin-lecture`），换成你自己的 fork 时记得同步
改 `package.json` 里的三处 URL。
