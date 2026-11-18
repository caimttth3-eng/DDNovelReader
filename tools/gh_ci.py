# -*- coding: utf-8 -*-
"""GitHub Actions 监控与产物下载（沙箱下走 Python + requests，见开发指南坑 24）。

用法：
  python _gh_ci.py runs
  python _gh_ci.py wait [<run_id>]
  python _gh_ci.py artifacts <run_id>
  python _gh_ci.py download <run_id> <outdir>
"""
import io
import os
import sys
import time
import zipfile

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OWNER, REPO = "caimttth3-eng", "DDNovelReader"
API = "https://api.github.com"
PROXIES = {"http": "http://127.0.0.1:1001", "https": "http://127.0.0.1:1001"}


def token():
    with io.open("_gh_token.txt", encoding="utf-8") as f:
        return f.read().strip()


def headers():
    return {"Authorization": "token " + token(), "User-Agent": "dd-ci", "Accept": "application/vnd.github+json"}


def latest_run():
    r = requests.get("%s/repos/%s/%s/actions/runs?per_page=1" % (API, OWNER, REPO),
                     headers=headers(), proxies=PROXIES, timeout=40)
    r.raise_for_status()
    runs = r.json().get("workflow_runs") or []
    return runs[0] if runs else None


def show(run):
    print("run id=%s head=%s status=%s conclusion=%s created=%s" % (
        run["id"], run["head_sha"][:8], run["status"], run.get("conclusion"), run["created_at"]))


def runs():
    r = requests.get("%s/repos/%s/%s/actions/runs?per_page=5" % (API, OWNER, REPO),
                     headers=headers(), proxies=PROXIES, timeout=40)
    r.raise_for_status()
    for run in r.json().get("workflow_runs") or []:
        show(run)


def wait(run_id=None):
    deadline = time.time() + 3600
    last = None
    while time.time() < deadline:
        run = latest_run() if run_id is None else requests.get(
            "%s/repos/%s/%s/actions/runs/%s" % (API, OWNER, REPO, run_id),
            headers=headers(), proxies=PROXIES, timeout=40).json()
        if run is None:
            print("no runs yet")
            return 1
        state = (run["status"], run.get("conclusion"))
        if state != last:
            show(run)
            last = state
        if run["status"] == "completed":
            return 0 if run.get("conclusion") == "success" else 1
        time.sleep(20)
    print("timeout")
    return 1


def list_artifacts(run_id):
    r = requests.get("%s/repos/%s/%s/actions/runs/%s/artifacts" % (API, OWNER, REPO, run_id),
                     headers=headers(), proxies=PROXIES, timeout=40)
    r.raise_for_status()
    return r.json().get("artifacts") or []


def artifacts(run_id):
    for a in list_artifacts(run_id):
        print("%s | id=%s | %.2f MB" % (a["name"], a["id"], a["size_in_bytes"] / 1048576.0))


def _stream_download(url, dest, expected=0):
    """先拿重定向地址，再不带鉴权头流式下载（否则存储端会拒绝、拿到 0 字节）。"""
    r = requests.get(url, headers=headers(), proxies=PROXIES, timeout=120, allow_redirects=False, stream=True)
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers["Location"]
        r.close()
        r = requests.get(loc, proxies=PROXIES, timeout=1800, stream=True)
    r.raise_for_status()
    got = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            if chunk:
                f.write(chunk)
                got += len(chunk)
    print("  bytes=%d expected=%d" % (got, expected))
    return got


def download(run_id, outdir):
    os.makedirs(outdir, exist_ok=True)
    rc = 0
    for a in list_artifacts(run_id):
        url = "%s/repos/%s/%s/actions/artifacts/%s/zip" % (API, OWNER, REPO, a["id"])
        zp = os.path.join(outdir, a["name"] + ".zip")
        got = _stream_download(url, zp, a["size_in_bytes"])
        if got == 0 or got > a["size_in_bytes"]:
            print("FAIL %s size mismatch" % a["name"])
            rc = 1
            continue
        print("saved %s (%.2f MB)" % (zp, got / 1048576.0))
        out = os.path.join(outdir, "extracted")
        os.makedirs(out, exist_ok=True)
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                print("   -", n)
            try:
                z.extractall(out)
            except Exception as e:
                print("  extract warn: %s" % e)
                for n in z.namelist():
                    if n.endswith("/"):
                        continue
                    try:
                        z.extract(n, out)
                    except Exception as e2:
                        print("   skip %s: %s" % (n, e2))
    return rc


def jobs(run_id):
    r = requests.get("%s/repos/%s/%s/actions/runs/%s/jobs" % (API, OWNER, REPO, run_id),
                     headers=headers(), proxies=PROXIES, timeout=60)
    r.raise_for_status()
    for j in r.json().get("jobs") or []:
        print("job id=%s name=%s status=%s conclusion=%s" % (
            j["id"], j["name"], j["status"], j.get("conclusion")))
        for s in j.get("steps") or []:
            print("    %-38s %s %s" % (s["name"], s["status"], s.get("conclusion")))


def job_log(job_id):
    url = "%s/repos/%s/%s/actions/jobs/%s/logs" % (API, OWNER, REPO, job_id)
    r = requests.get(url, headers=headers(), proxies=PROXIES, timeout=300, allow_redirects=True)
    if r.status_code >= 300:
        print("LOG FAIL %s %s" % (r.status_code, r.text[:200]))
        return 1
    text = r.content.decode("utf-8", "replace")
    lines = text.splitlines()
    print("共 %d 行，最后 60 行：" % len(lines))
    for ln in lines[-60:]:
        print(ln)
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "runs":
        runs()
        return 0
    if cmd == "jobs":
        jobs(sys.argv[2])
        return 0
    if cmd == "log":
        return job_log(sys.argv[2])
    if cmd == "wait":
        return wait(sys.argv[2] if len(sys.argv) > 2 else None)
    if cmd == "artifacts":
        artifacts(sys.argv[2])
        return 0
    if cmd == "download":
        return download(sys.argv[2], sys.argv[3])
    print("unknown")
    return 2


if __name__ == "__main__":
    sys.exit(main())
