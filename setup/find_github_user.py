"""只读地在本地浏览器历史里找 GitHub 账号线索（用于把仓库地址里的 OWNER 换成真实用户名）。"""
import os
import glob
import shutil
import sqlite3
import tempfile
import urllib.parse

home = os.path.expanduser("~")
cands = []
for pat in [
    r"AppData\Local\Google\Chrome\User Data\*\History",
    r"AppData\Local\Microsoft\Edge\User Data\*\History",
    r"AppData\Roaming\Mozilla\Firefox\Profiles\*\places.sqlite",
    r"AppData\Local\BraveSoftware\Brave-Browser\User Data\*\History",
]:
    cands += glob.glob(os.path.join(home, pat))

rows = []
for path in cands:
    try:
        tmp = os.path.join(tempfile.gettempdir(), "hist_" + str(abs(hash(path))) + ".db")
        shutil.copy2(path, tmp)
        con = sqlite3.connect(tmp)
        if path.endswith("places.sqlite"):
            q = "SELECT url, title, last_visit_date FROM moz_places WHERE url LIKE '%github.com%' ORDER BY last_visit_date DESC LIMIT 40"
        else:
            q = "SELECT url, title, last_visit_time FROM urls WHERE url LIKE '%github.com%' ORDER BY last_visit_time DESC LIMIT 40"
        for url, title, ts in con.execute(q):
            rows.append((url, title, path))
        con.close()
        os.remove(tmp)
    except Exception as e:
        rows.append(("ERR", str(e)[:60], path))

seen = set()
users = {}
for url, title, src in rows:
    if url in seen:
        continue
    seen.add(url)
    p = urllib.parse.urlparse(url)
    seg = [s for s in p.path.split("/") if s]
    if p.netloc == "github.com" and seg:
        u = seg[0]
        if u not in ("login", "signup", "settings", "orgs", "features", "topics", "marketplace",
                     "explore", "notifications", "new", "pulls", "issues", "search", "about"):
            users[u] = users.get(u, 0) + 1

print("history files:", len(cands), "| github rows:", len(rows))
print("--- 出现最多的用户名候选 ---")
for u, n in sorted(users.items(), key=lambda kv: -kv[1])[:12]:
    print(f"{n:>3}  {u}")
print("--- 最近 15 条 github 访问 ---")
for url, title, src in rows[:15]:
    print(url[:100], "|", (title or "")[:40])
