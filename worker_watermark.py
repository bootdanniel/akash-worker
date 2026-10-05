#!/usr/bin/env python3
import os, sys, json, time, subprocess, shutil, requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── CONFIG ────────────────────────────────────────────────
TOKEN     = os.environ["GH_TOKEN"]
REPO_ORIG = "bootdanniel/live24h"
TAG_ORIG  = "v2"
REPO_DEST = "bootdanniel/live24com-marcadagua"
TAG_DEST  = "v1"
LOGO_URL  = "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/logo.png"

# Watermark: canto INFERIOR ESQUERDO
LOGO_W    = 150       # px de largura
MARGEM_X  = 20        # px da esquerda
MARGEM_Y  = 20        # px de baixo
OPACIDADE = 0.85      # 0.0-1.0

WORK = "/tmp/work"
# ──────────────────────────────────────────────────────────

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=5, backoff_factor=3,
    status_forcelist=[429, 500, 502, 503, 504])))

HDR = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json"}

def gh(method, path, **kw):
    kw.setdefault("timeout", 60)
    return session.request(method, f"https://api.github.com{path}", headers=HDR, **kw)

def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

def ensure_repo(repo):
    owner, name = repo.split("/")
    r = gh("GET", f"/repos/{repo}")
    if r.status_code == 200:
        log(f"repo {repo} já existe"); return
    log(f"criando repo {repo}...")
    r = gh("POST", "/user/repos", json={"name": name, "private": False,
        "description": "live24 com marca d'agua"})
    if r.status_code not in (200, 201):
        log("erro criando repo:", r.status_code, r.text[:300]); sys.exit(1)
    time.sleep(3)

def ensure_release(repo, tag):
    r = gh("GET", f"/repos/{repo}/releases/tags/{tag}")
    if r.status_code == 200:
        return r.json()["id"]
    log(f"criando release {tag} em {repo}...")
    r = gh("POST", f"/repos/{repo}/releases", json={
        "tag_name": tag, "name": tag, "body": "com marca d'agua"})
    if r.status_code not in (200, 201):
        log("erro criando release:", r.status_code, r.text[:300]); sys.exit(1)
    return r.json()["id"]

def list_assets(repo, tag):
    r = gh("GET", f"/repos/{repo}/releases/tags/{tag}")
    if r.status_code != 200:
        return []
    return r.json().get("assets", [])

def baixar(url, dest):
    with session.get(url, stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for c in r.iter_content(1024*512):
                f.write(c)

def watermark(inp, outp, logo):
    flt = (f"[1:v]scale={LOGO_W}:-1,format=rgba,"
           f"colorchannelmixer=aa={OPACIDADE}[wm];"
           f"[0:v][wm]overlay={MARGEM_X}:H-h-{MARGEM_Y}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-i", inp, "-i", logo,
           "-filter_complex", flt,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p",
           "-c:a", "copy",
           "-movflags", "+faststart",
           outp]
    subprocess.run(cmd, check=True)

def upload(repo, release_id, path, name):
    url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
    with open(path, "rb") as f:
        r = session.post(url, params={"name": name},
            headers={"Authorization": f"token {TOKEN}",
                     "Content-Type": "video/mp4"},
            data=f, timeout=3600)
    return r

def main():
    os.makedirs(WORK, exist_ok=True)
    logo = f"{WORK}/logo.png"
    log("baixando logo...")
    baixar(LOGO_URL, logo)
    log(f"logo: {os.path.getsize(logo)} bytes")

    ensure_repo(REPO_DEST)
    release_id = ensure_release(REPO_DEST, TAG_DEST)

    orig = [a for a in list_assets(REPO_ORIG, TAG_ORIG) if a["name"].endswith(".mp4")]
    orig.sort(key=lambda a: a["name"])
    feitos = {a["name"] for a in list_assets(REPO_DEST, TAG_DEST)}
    log(f"{len(orig)} originais | {len(feitos)} já feitos")

    for i, a in enumerate(orig, 1):
        name = a["name"]
        if name in feitos:
            log(f"[{i}/{len(orig)}] SKIP {name}")
            continue
        log(f"[{i}/{len(orig)}] {name}")
        tin  = f"{WORK}/in.mp4"
        tout = f"{WORK}/out.mp4"
        for f in (tin, tout):
            try: os.remove(f)
            except: pass
        try:
            t0 = time.time()
            baixar(a["browser_download_url"], tin)
            log(f"  ↓ {os.path.getsize(tin)//1024//1024}MB em {time.time()-t0:.0f}s")
            t0 = time.time()
            watermark(tin, tout, logo)
            log(f"  🎨 {os.path.getsize(tout)//1024//1024}MB em {time.time()-t0:.0f}s")
            t0 = time.time()
            r = upload(REPO_DEST, release_id, tout, name)
            if r.status_code in (200, 201):
                log(f"  ✓ subiu em {time.time()-t0:.0f}s")
            else:
                log(f"  ✗ upload {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"  ✗ erro: {e}")
        finally:
            for f in (tin, tout):
                try: os.remove(f)
                except: pass

    log("FIM")

if __name__ == "__main__":
    main()
