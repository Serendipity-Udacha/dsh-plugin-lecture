"""下载 faster-whisper 的 CTranslate2 模型到 LECTURE_HOME/models/<name>。

hf-mirror / ModelScope 都必须带浏览器 UA，否则 403；国内直连 huggingface.co 基本超时。
小文件走这里，`model.bin`（约 460-480MB）单连接只有 ~1-2MB/s，建议用 dl_parallel.py。

用法：
  python setup/fetch_model.py                 # 默认 small
  python setup/fetch_model.py tiny            # 更小更快，准确率下降
"""
import os
import sys
import urllib.request

HOME = os.environ.get("LECTURE_HOME") or os.path.join(
    os.path.expanduser("~"), ".dsh", "lecture")
NAME = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LECTURE_MODEL", "small")
MODEL = os.path.join(HOME, "models", NAME)
os.makedirs(MODEL, exist_ok=True)

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

BASES = [
    "https://hf-mirror.com/Systran/faster-whisper-%s/resolve/main/" % NAME,
    "https://huggingface.co/Systran/faster-whisper-%s/resolve/main/" % NAME,
]

FILES = [("config.json", 100), ("tokenizer.json", 1000),
         ("vocabulary.txt", 100), ("model.bin", 300 * 1000 * 1000)]


def get(url, dst, expect_min=0):
    if os.path.exists(dst) and os.path.getsize(dst) >= expect_min > 0:
        return True, os.path.getsize(dst)
    tmp = dst + ".part"
    try:
        req = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if expect_min and got < expect_min:
            os.remove(tmp)
            return False, "short %d" % got
        os.replace(tmp, dst)
        return True, got
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False, "%s %s" % (type(e).__name__, str(e)[:40])


ok_base = None
for base in BASES:
    r, info = get(base + "config.json", os.path.join(MODEL, "config.json"), 100)
    print(("HIT  " if r else "MISS ") + base[:60], info)
    if r:
        ok_base = base
        break

if not ok_base:
    print("NO_BASE")
    sys.exit(1)

for fn, mn in FILES:
    dst = os.path.join(MODEL, fn)
    if os.path.exists(dst) and os.path.getsize(dst) >= mn:
        print("skip", fn, os.path.getsize(dst))
        continue
    if fn == "model.bin" and not os.environ.get("FORCE_SLOW"):
        print("skip model.bin -> 用 setup/dl_parallel.py 下（快 4-8 倍）")
        continue
    r, info = get(ok_base + fn, dst, mn)
    print("get", fn, "->", info if not r else os.path.getsize(dst))
    if not r:
        print("FAIL", fn)
        sys.exit(2)

print("MODEL_DIR", MODEL)
