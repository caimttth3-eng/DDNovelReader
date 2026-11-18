# -*- coding: utf-8 -*-
"""GitHub Release 操作（沙箱下 Schannel 不可用，统一走 Python + requests，见开发指南坑 24）。

用法：
  python _gh_release.py list
  python _gh_release.py create <tag> <title> <body_file>
  python _gh_release.py upload <release_id> <file> [<file> ...]
  python _gh_release.py assets <release_id>
"""
import io
import json
import os
import sys

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OWNER, REPO = "caimttth3-eng", "DDNovelReader"
API = "https://api.github.com"
PROXY = os.environ.get("DD_PROXY", "http://127.0.0.1:1001")
PROXIES = {"http": PROXY, "https": PROXY}


def token():
    with io.open("_gh_token.txt", encoding="utf-8") as f:
        return f.read().strip()


def headers(extra=None):
    h = {"Authorization": "token " + token(), "User-Agent": "dd-release", "Accept": "application/vnd.github+json"}
    if extra:
        h.update(extra)
    return h


def list_releases():
    r = requests.get("%s/repos/%s/%s/releases" % (API, OWNER, REPO), headers=headers(), proxies=PROXIES, timeout=40)
    r.raise_for_status()
    for rel in r.json():
        print("%s | tag=%s | id=%s | assets=%s | %s" % (
            rel["name"], rel["tag_name"], rel["id"], len(rel.get("assets") or []), rel.get("published_at")))


def create(tag, title, body_file):
    with io.open(body_file, encoding="utf-8") as f:
        body = f.read()
    payload = {"tag_name": tag, "name": title, "body": body, "draft": False, "prerelease": False}
    r = requests.post("%s/repos/%s/%s/releases" % (API, OWNER, REPO), headers=headers(),
                      proxies=PROXIES, timeout=60, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if r.status_code >= 300:
        print("CREATE FAIL %s %s" % (r.status_code, r.text[:400]))
        return 1
    data = r.json()
    print("CREATED id=%s tag=%s url=%s" % (data["id"], data["tag_name"], data["html_url"]))
    with io.open("_release_id_v228.txt", "w", encoding="utf-8") as f:
        f.write(str(data["id"]))
    return 0


def upload(release_id, files):
    rc = 0
    for path in files:
        name = os.path.basename(path)
        size = os.path.getsize(path)
        print("uploading %s (%.2f MB) ..." % (name, size / 1048576.0))
        with io.open(path, "rb") as f:
            data = f.read()
        url = "https://uploads.github.com/repos/%s/%s/releases/%s/assets?name=%s" % (OWNER, REPO, release_id, name)
        r = requests.post(url, headers=headers({"Content-Type": "application/octet-stream"}),
                          proxies=PROXIES, timeout=1800, data=data)
        if r.status_code >= 300:
            print("  FAIL %s %s" % (r.status_code, r.text[:300]))
            rc = 1
        else:
            print("  OK id=%s size=%s" % (r.json().get("id"), r.json().get("size")))
    return rc


def assets(release_id):
    r = requests.get("%s/repos/%s/%s/releases/%s/assets" % (API, OWNER, REPO, release_id),
                     headers=headers(), proxies=PROXIES, timeout=40)
    r.raise_for_status()
    for a in r.json():
        print("%s | %s | %.2f MB | state=%s" % (a["name"], a["id"], a["size"] / 1048576.0, a["state"]))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "list":
        list_releases()
        return 0
    if cmd == "create":
        return create(sys.argv[2], sys.argv[3], sys.argv[4])
    if cmd == "upload":
        return upload(sys.argv[2], sys.argv[3:])
    if cmd == "assets":
        assets(sys.argv[2])
        return 0
    print("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
