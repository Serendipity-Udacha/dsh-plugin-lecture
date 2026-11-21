"""从 PyPI 直接下载 wheel 并解压到 LECTURE_HOME/pylibs —— 不需要 pip。

为什么不用 pip：某些受管环境里 pip 写 `*.whl.metadata` 会被拒
（`Permission denied: '...whl.metadata'`），换 TMP 也不行。这里只用到
「拿 wheel → 解压 → 读 METADATA 取依赖」这三步，自己实现更稳。

用法：
  python setup/install.py faster-whisper numpy
  LECTURE_HOME=D:\\asr python setup/install.py faster-whisper
"""
import json
import os
import re
import sys
import time
import zipfile
import urllib.request
import shutil

HOME = os.environ.get("LECTURE_HOME") or os.path.join(
    os.path.expanduser("~"), ".dsh", "lecture")
LIB = os.path.join(HOME, "pylibs")
DL = os.path.join(HOME, "downloads")
os.makedirs(LIB, exist_ok=True)
os.makedirs(DL, exist_ok=True)
# 按当前解释器挑 cpXY wheel；pure-python 的 -py3-none-any 也放行
PYTAG = "cp%d%d" % sys.version_info[:2]
sys.path.insert(0, LIB)


def jget(url):
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception:
            time.sleep(2)
    return None


def ok_tag(fn):
    if not fn.endswith(".whl"):
        return False
    if "win_amd64" not in fn and "none-any" not in fn:
        return False
    return (PYTAG in fn) or ("abi3" in fn) or ("py3-none-any" in fn)


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


installed = {}


def install(req_name):
    key = norm(req_name)
    if key in installed:
        return True
    data = jget("https://pypi.org/pypi/%s/json" % req_name)
    if not data:
        print("NOPKG", req_name)
        return False
    ver = data["info"]["version"]
    files = [f for f in data["urls"] if ok_tag(f["filename"])]
    if not files:
        print("NOWHEEL", req_name, ver)
        return False
    f = files[-1]
    fn, url = f["filename"], f["url"]
    path = os.path.join(DL, fn)
    if not os.path.exists(path):
        try:
            with urllib.request.urlopen(url, timeout=600) as r, open(path, "wb") as fh:
                shutil.copyfileobj(r, fh)
        except Exception as e:
            print("DLFAIL", fn, str(e)[:50])
            return False
    meta = None
    try:
        with zipfile.ZipFile(path) as z:
            z.extractall(LIB)
            for n in z.namelist():
                if n.endswith(".dist-info/METADATA"):
                    meta = z.read(n).decode("utf-8", "ignore")
                    break
    except Exception as e:
        print("UNZIPFAIL", fn, str(e)[:50])
        return False
    installed[key] = ver
    deps = []
    if meta:
        for line in meta.splitlines():
            if line.startswith("Requires-Dist:"):
                spec = line.split(":", 1)[1].strip()
                if "extra ==" in spec:
                    continue
                m = re.match(r"([A-Za-z0-9_.\-]+)", spec)
                if m:
                    deps.append(m.group(1))
    print("INST", req_name, ver, len(deps))
    for d in deps:
        install(d)
    return True


if __name__ == "__main__":
    for top in (sys.argv[1:] or ["faster-whisper"]):
        install(top)
    print("TOTAL", len(installed), "->", LIB)
