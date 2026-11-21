"""12 线程 Range 分块下载 model.bin（hf-mirror 单连接只有 1-2MB/s）。

每一块先落 <model.bin>.pNN，全部到齐后顺序拼成 model.bin；中断后重跑只补缺块。

用法：
  python setup/dl_parallel.py              # 默认 small
  python setup/dl_parallel.py medium 16    # 换模型 / 换并发
"""
import os
import sys
import time
import threading
import urllib.request

HOME = os.environ.get("LECTURE_HOME") or os.path.join(
    os.path.expanduser("~"), ".dsh", "lecture")
NAME = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LECTURE_MODEL", "small")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12
DST = os.path.join(HOME, "models", NAME, "model.bin")
URL = "https://hf-mirror.com/Systran/faster-whisper-%s/resolve/main/model.bin" % NAME
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

os.makedirs(os.path.dirname(DST), exist_ok=True)


def size_of(url):
    req = urllib.request.Request(url, headers=H, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers["Content-Length"])


def part_size():
    return os.path.getsize(DST) if os.path.exists(DST) else 0


if part_size() > 400 * 1000 * 1000 and not os.environ.get("FORCE"):
    print("already", part_size())
    sys.exit(0)

total = size_of(URL)
print("size", total, flush=True)
step = total // N
errs = [None] * N
done = [0] * N


def part(i):
    s = i * step
    e = total - 1 if i == N - 1 else s + step - 1
    p = "%s.p%02d" % (DST, i)
    if os.path.exists(p) and os.path.getsize(p) == e - s + 1:
        done[i] = 1
        return
    for _ in range(4):
        try:
            req = urllib.request.Request(URL, headers={**H, "Range": "bytes=%d-%d" % (s, e)})
            with urllib.request.urlopen(req, timeout=60) as r, open(p, "wb") as f:
                while True:
                    c = r.read(1 << 20)
                    if not c:
                        break
                    f.write(c)
            if os.path.getsize(p) == e - s + 1:
                done[i] = 1
                return
        except Exception as ex:
            errs[i] = str(ex)[:60]
            time.sleep(2)
    done[i] = -1


threads = [threading.Thread(target=part, args=(i,)) for i in range(N)]
t0 = time.time()
for t in threads:
    t.start()
last = -1
while any(t.is_alive() for t in threads):
    time.sleep(5)
    got = sum(os.path.getsize("%s.p%02d" % (DST, i)) for i in range(N)
              if os.path.exists("%s.p%02d" % (DST, i)))
    if got // (20 << 20) != last:
        last = got // (20 << 20)
        print("%.0fMB %.2fMB/s" % (got / (1 << 20), got / 1e6 / (time.time() - t0)), flush=True)
for t in threads:
    t.join()

if all(d == 1 for d in done):
    with open(DST, "wb") as out:
        for i in range(N):
            with open("%s.p%02d" % (DST, i), "rb") as f:
                while True:
                    c = f.read(1 << 20)
                    if not c:
                        break
                    out.write(c)
    for i in range(N):
        os.remove("%s.p%02d" % (DST, i))
    print("OK", os.path.getsize(DST), round(time.time() - t0, 1), flush=True)
else:
    print("FAIL", errs, flush=True)
    sys.exit(1)
