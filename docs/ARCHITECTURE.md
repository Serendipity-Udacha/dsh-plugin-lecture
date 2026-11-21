# 设计取舍与踩坑记录

这份文档记录的是"为什么这么写"，不是"怎么用"。用法见 [README](../README.md)。

## 1. 整条链路

```
lecture_probe(media)
   └─ ffmpeg -i 取 Duration / Stream      # ffprobe 不单独装，复用同一个 exe
lecture_transcribe(media)
   ├─ ffmpeg -vn -ac 1 -ar 16000 -c:a pcm_s16le → <out>/<name>.wav
   ├─ nseg = ceil(duration / 600)
   ├─ 把 0..nseg-1 轮转分给 W 个 worker 进程
   │     worker: wave 读 [start, start+600) → float32 → WhisperModel.transcribe
   │             写 segs/NNN.jsonl（一行一句）
   ├─ 主进程每 2 秒扫 jsonl 数量 → progress.json
   └─ combine: 排序 + 按 8 秒静默分段 → transcript.txt / transcript.srt
```

## 2. 为什么分段 600 秒

- 段太短：模型每次都要重新「进入语境」，`initial_prompt` 反复生效，句间会被切断；
- 段太长：中断后重跑代价大，进度也不好玩；
- 600 秒 ≈ 10 分钟，一段大约 1-2 分钟转完（4 段并行时），且正好对上「讲完一个话题」的粒度。

分段结果**先落盘再合并**，所以 Ctrl+C、断电、工具超时都不会浪费已经算完的部分——
`lecture_transcribe` 第二次调用时 worker 看到 `NNN.jsonl` 存在就直接跳过。

## 3. 为什么用 `wave` 读音频而不是 `av`/`ffmpeg-python`

faster-whisper 1.2.x 在内部用 `av.open(path, metadata_errors="ignore")`。
PyAV 19 已经没有 `metadata_errors` 这个参数，于是直接抛：

```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

把 PyAV 降级会连带撞 ctranslate2 的 ABI；而我们要的只是「16k 单声道 float32 数组」这一件事，
`wave` 标准库 + numpy 三行就能做到，还顺带把分块读取做得更可控（`w.setpos()`）。

## 4. 为什么并行用进程而不是线程

CTranslate2 释放 GIL 并不彻底，多线程共享一个 `WhisperModel` 收益有限；而 `int8` 模型本身占
内存不大（small ≈ 240MB 常驻），起 4 个进程各持一份模型最直白，也最容易做「一个 worker 崩了
不拖垮其他」。代价是每进程多 240MB 常驻内存。

worker 通过 `subprocess.Popen` 起，命令行参数用 `--out` 传目录——早期版本把目录当位置参数传，
argparse 直接 `rc=2` 退出，表现为「0 段、2 秒就返回 ok」，这个 bug 在 `smoke.mjs` 里能复现。

## 5. `duration()` 的临时文件坑

ffmpeg 没有 `--print-duration`，只能读 stderr。最初写死 `work/_dur.txt`，多进程同时 probe 时：

```
PermissionError: [WinError 32] 另一个程序正在使用此文件
```

改成 `_dur_<pid>.txt`，用完立刻删。**任何"固定临时文件名"在多进程里都是雷。**

## 6. 网络：hf-mirror / ModelScope 的 UA 门槛

- `huggingface.co`、`github.com` 在国内常常直接超时；
- `hf-mirror.com` 不带 UA 返回 403，带 Chrome UA 正常；
- 单连接 `model.bin`（460MB）只有 1–2MB/s，12 线程 Range 分块能到 6–8MB/s，随后会降到 ~2MB/s
  （镜像限速），总耗时约 10 分钟。

`dl_parallel.py` 每块先写 `.pNN`，全部到齐再顺序拼接，因此中断重启只补缺块。

## 7. 为什么模型放 `LECTURE_HOME` 而不是包内

460MB 的 `model.bin` 不该进 git，也不该跟着某个项目目录走。集中到 `~/.dsh/lecture` 后：
换项目不用重下、升级插件不重下、多个 DSH profile 共享同一份。插件打包体积因此只有几十 KB。

## 8. 插件形态：为什么要 `dsh.bundle.patch`

DSH 的 profile 是「多个 bundle 层叠成的一棵树」。一个第三方包要贡献插件行，最稳的方式是声明
自己的 patch 文件（`package.json` 里 `dsh.bundle.patch`），由 loader 在 base 层之上 `insert` 一行：

```yaml
- insert:
    - id: lecture-asr
      name: 'dsh-plugin-lecture'
```

同时 profile 的 `node_modules` 下需要能解析到包名——开发期用目录链接（junction）即可，
和 wallpaper-engine 等第三方 bundle 的 dev-link 形态一致。

## 9. 未做的事

- **说话人分离**：需要 pyannote 一类的模型，体积和依赖都翻倍，暂不做；
- **GPU**：`compute_type` 写死 `int8`，没有 `float16` + CUDA 分支；
- **VAD 参数暴露**：`vad_filter=True` 是固定值，噪声大的会场可能需要调 `min_silence_duration_ms`；
- **自动标点**：whisper 自带的标点其实有，但中文讲座里常被 VAD 切断，后续可以接 punctuation 模型。
