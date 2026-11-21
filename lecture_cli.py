r"""lecture_cli.py — 离线讲座转写（DSH 插件 dsh-plugin-lecture 的内核）。

所有大件（whisper 模型 / 依赖 wheel 解压目录 / ffmpeg）集中放在 LECTURE_HOME，
默认 %USERPROFILE%\.dsh\lecture，可用环境变量 LECTURE_HOME 覆盖。

用法：
  python lecture_cli.py probe  <media>
  python lecture_cli.py run    <media> --out DIR [--name X] [--lang zh] [--workers 4] [--threads 5]
  python lecture_cli.py status <out_dir>
  python lecture_cli.py worker <wav> <seg_dir> <ids...>   # 内部使用
"""
import os
import sys
import json
import time
import wave
import argparse
import subprocess

SEG = 600  # 每段秒数，10 分钟


def home():
    h = os.environ.get("LECTURE_HOME")
    if not h:
        h = os.path.join(os.path.expanduser("~"), ".dsh", "lecture")
    return h


def find_ffmpeg():
    h = home()
    cand = [os.path.join(h, "ffmpeg", "ffmpeg.exe")]
    b = os.path.join(h, "pylibs", "imageio_ffmpeg", "binaries")
    if os.path.isdir(b):
        for fn in sorted(os.listdir(b)):
            if fn.lower().startswith("ffmpeg") and fn.lower().endswith(".exe"):
                cand.append(os.path.join(b, fn))
    for c in cand:
        if os.path.exists(c):
            return c
    raise SystemExit("ffmpeg not found under " + h)


FF = find_ffmpeg()


def ensure_pylibs():
    p = os.path.join(home(), "pylibs")
    if p not in sys.path:
        sys.path.insert(0, p)


def run_hidden(args, log=None):
    f = open(log, "ab") if log else subprocess.DEVNULL
    try:
        return subprocess.run(args, stdout=f, stderr=f, stdin=subprocess.DEVNULL).returncode
    finally:
        if log:
            f.close()


def probe(path):
    """返回 {duration_s, has_video, has_audio, raw}（ffmpeg 无 ffprobe 时的替代方案）。"""
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"_probe_{os.getpid()}.txt")
    with open(tmp, "wb") as f:
        subprocess.run([FF, "-hide_banner", "-i", path], stdout=f, stderr=f,
                       stdin=subprocess.DEVNULL)
    txt = open(tmp, "rb").read().decode("utf-8", "ignore")
    os.remove(tmp)
    m = None
    for line in txt.splitlines():
        if "Duration:" in line:
            m = line
            break
    dur = 0
    if m:
        import re
        g = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", m)
        if g:
            dur = int(g.group(1)) * 3600 + int(g.group(2)) * 60 + int(g.group(3))
    return {
        "duration_s": dur,
        "has_video": "Video:" in txt,
        "has_audio": "Audio:" in txt,
        "raw": txt.strip().splitlines()[:12],
    }


def to_wav(src, out_dir, name):
    get = os.path.splitext(os.path.basename(src))[1].lower()
    if get == ".wav":
        return src
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, name + ".wav")
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    rc = run_hidden([FF, "-hide_banner", "-loglevel", "error", "-i", src, "-vn",
                     "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", dst])
    if rc != 0:
        raise SystemExit("ffmpeg audio extraction failed (rc=%d)" % rc)
    return dst


def load_wav(path, start=0.0, length=None):
    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getframerate() != 16000:
            raise SystemExit("need 16k mono wav")
        w.setpos(int(start * 16000))
        n = int(length * 16000) if length else w.getnframes() - int(start * 16000)
        buf = w.readframes(n)
    return np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0


def write_progress(out_dir, **kw):
    os.makedirs(out_dir, exist_ok=True)
    kw["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(out_dir, "progress.json"), "w", encoding="utf-8") as f:
        json.dump(kw, f, ensure_ascii=False)


def do_worker(wav, seg_dir, ids, lang, threads, model_dir):
    ensure_pylibs()
    from faster_whisper import WhisperModel
    total = probe(wav)["duration_s"] or 0
    model = WhisperModel(model_dir, device="cpu", compute_type="int8",
                         cpu_threads=threads)
    prompt = "以下是普通话授课内容，请用简体中文转写。" if lang == "zh" else None
    for i in ids:
        part = os.path.join(seg_dir, "%03d.jsonl" % i)
        if os.path.exists(part):
            continue
        start = i * SEG
        length = min(SEG, total - start)
        segs, _ = model.transcribe(load_wav(wav, start, length), language=lang,
                                   beam_size=5, initial_prompt=prompt,
                                   vad_filter=True,
                                   condition_on_previous_text=False)
        with open(part, "w", encoding="utf-8") as f:
            for s in segs:
                f.write(json.dumps({"start": s.start + start, "end": s.end + start,
                                    "text": s.text.strip()}, ensure_ascii=False) + "\n")
        print("[worker %d] seg %d ok" % (os.getpid(), i), flush=True)


def srt_ts(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return "%02d:%02d:%02d,%03d" % (h, m, int(s), int((s - int(s)) * 1000))


def combine(seg_dir, name, out_dir):
    rows = []
    if os.path.isdir(seg_dir):
        for fn in sorted(os.listdir(seg_dir)):
            if fn.endswith(".jsonl"):
                with open(os.path.join(seg_dir, fn), encoding="utf-8") as f:
                    rows += [json.loads(l) for l in f if l.strip()]
    rows.sort(key=lambda r: r["start"])
    txt_path = os.path.join(out_dir, name + ".txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        prev = -999
        for r in rows:
            if r["start"] - prev > 8:
                f.write("\n")
            f.write(r["text"])
            prev = r["end"]
    with open(os.path.join(out_dir, name + ".srt"), "w", encoding="utf-8") as f:
        for k, r in enumerate(rows, 1):
            f.write("%d\n%s --> %s\n%s\n\n" %
                    (k, srt_ts(r["start"]), srt_ts(r["end"]), r["text"]))
    return txt_path, len(rows)


def cmd_run(a):
    name = a.name or os.path.splitext(os.path.basename(a.media))[0]
    out_dir = os.path.abspath(a.out)
    seg_dir = os.path.join(out_dir, "segs")
    os.makedirs(seg_dir, exist_ok=True)
    t0 = time.time()

    write_progress(out_dir, stage="probe", name=name)
    info = probe(a.media)
    total = info["duration_s"]
    if not total:
        raise SystemExit("cannot read duration: " + a.media)

    write_progress(out_dir, stage="audio", name=name, duration_s=total)
    wav = to_wav(a.media, out_dir, name)

    nseg = int((total + SEG - 1) // SEG)
    model_dir = a.model or os.path.join(home(), "models", "small")
    if not os.path.isdir(model_dir):
        raise SystemExit("model dir missing: " + model_dir)

    # 分段切成 workers 份，各自起进程（已验证的并行方式）
    buckets = [[] for _ in range(a.workers)]
    for i in range(nseg):
        buckets[i % a.workers].append(i)
    procs = []
    for b in buckets:
        if not b:
            continue
        wlog = open(os.path.join(out_dir, "worker-%d.log" % len(procs)), "ab")
        procs.append(subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "worker", wav,
             "--out", seg_dir, "--lang", a.lang, "--threads", str(a.threads),
             "--model", model_dir, "--ids"] + [str(i) for i in b],
            stdout=wlog, stderr=wlog, stdin=subprocess.DEVNULL))

    deadline = t0 + a.timeout if a.timeout else None
    while any(p.poll() is None for p in procs):
        done = len([f for f in os.listdir(seg_dir) if f.endswith(".jsonl")])
        write_progress(out_dir, stage="transcribe", name=name, duration_s=total,
                       segments_done=done, segments_total=nseg,
                       pct=round(done * 100.0 / nseg, 1) if nseg else 0,
                       elapsed_s=round(time.time() - t0, 1))
        if deadline and time.time() > deadline:
            for p in procs:
                p.kill()
            write_progress(out_dir, stage="timeout", name=name,
                           segments_done=done, segments_total=nseg)
            print(json.dumps({"ok": False, "reason": "timeout", "segments_done": done,
                              "segments_total": nseg, "out_dir": out_dir},
                             ensure_ascii=False))
            return 3
        time.sleep(2)

    bad = [p.returncode for p in procs if p.returncode not in (0, None)]
    txt, nrows = combine(seg_dir, name, out_dir)
    chars = len(open(txt, encoding="utf-8").read().replace("\n", ""))
    res = {"ok": not bad, "transcript": txt,
           "srt": os.path.join(out_dir, name + ".srt"),
           "segments": nrows, "chars": chars, "duration_s": total,
           "elapsed_s": round(time.time() - t0, 1), "workers": a.workers,
           "failed_workers": bad}
    write_progress(out_dir, stage="done", name=name, **{k: res[k] for k in
                   ("segments", "chars", "elapsed_s")})
    print(json.dumps(res, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("media", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--name")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--model")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--threads", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=0)
    ap.add_argument("--ids", nargs="*", type=int)
    a = ap.parse_args()

    if a.cmd == "probe":
        print(json.dumps(probe(a.media), ensure_ascii=False))
    elif a.cmd == "status":
        p = os.path.join(a.media, "progress.json")
        print(open(p, encoding="utf-8").read() if os.path.exists(p) else "{}")
    elif a.cmd == "run":
        sys.exit(cmd_run(a))
    elif a.cmd == "worker":
        wav, seg_dir = a.media, a.out
        do_worker(wav, seg_dir, a.ids or [], a.lang, a.threads,
                  a.model or os.path.join(home(), "models", "small"))
    else:
        raise SystemExit("unknown cmd " + a.cmd)


if __name__ == "__main__":
    main()
