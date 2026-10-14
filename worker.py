#!/usr/bin/env python3
import os, json, time, shutil, subprocess, requests
from datetime import datetime

CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_dexte_original.json",
]

PEND = "/tmp/videos_pending"
COOLDOWN = 300                 # 10 min entre tentativas de upload
MAX_TENTATIVAS = 20            # depois cai pro ciclo externo (5 min) e tenta de novo
UPLOAD_TIMEOUT = 10800         # 3h pro lote gigante
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs(PEND, exist_ok=True)

def log(m):
    print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def gh_h(token):
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
            "User-Agent": "akash-worker-v10"}

def erro_limit(t):
    t = t.lower()
    return any(s in t for s in ["rate limit","secondary rate","abuse detection",
        "too many requests","403","429","timeout","timed out","connection reset",
        "temporary failure","502","503","service unavailable","bad gateway"])

def url_cache(u):
    return f"{u}{'&' if '?' in u else '?'}t={int(time.time())}"

def ler_config(u):
    r = requests.get(url_cache(u), timeout=30); r.raise_for_status(); return r.json()

def ensure_release(token, repo, tag):
    h = gh_h(token)
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", headers=h, timeout=30)
    if r.ok: return r.json()
    if r.status_code == 404:
        r2 = requests.post(f"https://api.github.com/repos/{repo}/releases", headers=h,
            json={"tag_name":tag,"name":tag,"body":"auto","draft":False,"prerelease":False}, timeout=30)
        if r2.ok: return r2.json()
        raise RuntimeError(f"release create {r2.status_code} {r2.text[:200]}")
    raise RuntimeError(f"release get {r.status_code} {r.text[:200]}")

def listar_existentes(token, repo, tag):
    rid = ensure_release(token, repo, tag)["id"]
    h = gh_h(token); nomes = set(); page = 1
    while True:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/{rid}/assets?per_page=100&page={page}",
                         headers=h, timeout=30)
        if not r.ok: raise RuntimeError(f"assets {r.status_code} {r.text[:200]}")
        arr = r.json()
        if not arr: break
        for a in arr: nomes.add(a["name"])
        if len(arr) < 100: break
        page += 1
    return nomes

def pendentes():
    try: return sorted([f for f in os.listdir(PEND) if f.lower().endswith(".mp4")])
    except FileNotFoundError: return []

def pedir_link(tunnel, tmdb, t, e):
    url = f"{tunnel.rstrip('/')}/resolver/{tmdb}/{t}/{e}"
    for i in range(1,4):
        try:
            d = requests.get(url, timeout=120).json()
            if d.get("mp4"): return d["mp4"]
            log(f"    ponte sem link {i}: {d}")
        except Exception as ex:
            log(f"    erro ponte {i}: {ex}")
        time.sleep(15)
    return None

def baixar(url, destino):
    r = requests.get(url, stream=True, timeout=(30,300), headers={"User-Agent": UA})
    r.raise_for_status()
    total = int(r.headers.get("content-length",0)); baix=0; ult=0; part=destino+".part"
    with open(part,"wb") as f:
        for c in r.iter_content(4*1024*1024):
            if c:
                f.write(c); baix+=len(c)
                if total:
                    p=int(baix*100/total)
                    if p>=ult+5:
                        print(f"\r[worker]:     {p}% ({baix//1024//1024}/{total//1024//1024} MB)",end="",flush=True); ult=p
    print("",flush=True); os.replace(part,destino); return baix

def fase_download(cfg, token):
    repo,tag,tmdb = cfg["github_repo"],cfg["github_tag"],cfg["tmdb"]
    tunnel = cfg["tunnel_url"].rstrip("/"); tmpl = cfg.get("nome_template","S{t:02d}E{e:02d}.mp4")
    try: existentes = listar_existentes(token,repo,tag)
    except Exception as ex: log(f"❌ listando GitHub: {ex}"); return
    faltando=[]
    for s in cfg["seasons"]:
        t=int(s["numero"])
        for e in range(1,int(s["episodios"])+1):
            n=tmpl.format(t=t,e=e)
            if n not in existentes and n not in set(pendentes()): faltando.append((t,e,n))
    log(f"📥 FASE DOWNLOAD — {len(existentes)} no GitHub | {len(faltando)} para baixar")
    if not faltando: log("✅ nada a baixar."); return
    for i,(t,e,n) in enumerate(faltando,1):
        free = shutil.disk_usage(PEND).free
        if free < 1_600_000_000:
            log(f"⚠️ Espaço baixo ({free//1024//1024} MB livres). Parando download e indo enviar o que já tem."); return
        log(f"── [{i}/{len(faltando)}] {n} ──")
        link = pedir_link(tunnel,tmdb,t,e)
        if not link: log("❌ sem link, pulando"); continue
        try:
            sz = baixar(link, os.path.join(PEND,n))
            log(f"✅ baixado {n} ({sz/1024/1024:.1f} MB) | fila={len(pendentes())}")
        except Exception as ex:
            log(f"❌ falha baixando: {ex}")
            for sx in ("",".part"):
                p=os.path.join(PEND,n)+sx
                if os.path.exists(p):
                    try: os.remove(p)
                    except OSError: pass

def fase_upload(cfg, token):
    repo,tag = cfg["github_repo"],cfg["github_tag"]
    env = os.environ.copy(); env["GH_TOKEN"]=token
    for tent in range(1, MAX_TENTATIVAS+1):
        try: existentes = listar_existentes(token,repo,tag)
        except Exception as ex:
            log(f"    ⚠️ listagem falhou ({ex}). Espera {COOLDOWN//60}min e tenta de novo."); time.sleep(COOLDOWN); continue
        # limpa da fila o que já subiu em tentativa anterior
        for n in pendentes():
            if n in existentes:
                try: os.remove(os.path.join(PEND,n))
                except OSError: pass
        a_subir = pendentes()
        if not a_subir:
            log("✅ FILA VAZIA — todos enviados."); return True
        paths = [os.path.join(PEND,n) for n in a_subir]
        log(f"    → Enviando lote de {len(paths)} arquivo(s) (tentativa {tent}/{MAX_TENTATIVAS})")
        cmd = ["gh","release","upload",tag,*paths,"--repo",repo,"--clobber"]
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=UPLOAD_TIMEOUT)
            out=(r.stderr or r.stdout).strip()
        except subprocess.TimeoutExpired:
            out="timeout no upload do lote"
            r=None
        if r is not None and r.returncode==0:
            for p in paths:
                try: os.remove(p)
                except OSError: pass
            log(f"    ✅ Lote enviado ({len(paths)} arquivo(s)).")
            # confirma se sobrou algo
            if not pendentes():
                log("✅ FILA VAZIA — todos enviados."); return True
            continue
        msg = out if r is None else (r.stderr or r.stdout).strip()
        if erro_limit(msg):
            log(f"    ⚠️ GitHub limitou/instável: {msg[:200]}")
            log(f"    ⏳ Parado {COOLDOWN//60} min e tento o lote de novo...")
            time.sleep(COOLDOWN); continue
        log(f"    ❌ Erro definitivo (não é limite): {msg[:400]}")
        return False
    log(f"    ⚠️ Limite persistente após {MAX_TENTATIVAS} tentativas. Cai pro ciclo externo e tenta de novo.")
    return False

def processar(cfg_url):
    log("="*60); log(f"CONFIG: {cfg_url}")
    try: cfg = ler_config(cfg_url)
    except Exception as ex: log(f"❌ config: {ex}"); return
    token = os.environ.get("GH_TOKEN","")
    if not token: log("❌ GH_TOKEN vazio"); return
    log(f"Repo: {cfg['github_repo']} ({cfg['github_tag']}) | TMDB: {cfg['tmdb']}")
    try:
        h = requests.get(f"{cfg['tunnel_url'].rstrip('/')}/health", timeout=15).json()
        log(f"✅ Ponte online: {h}")
    except Exception as ex:
        log(f"❌ Ponte offline: {ex}")
        # mesmo com ponte caída, tenta enviar o que já está na fila
        if pendentes(): fase_upload(cfg, token)
        return
    if pendentes():
        log(f"📦 Já há {len(pendentes())} na fila → indo direto pro upload (sem baixar de novo).")
    else:
        fase_download(cfg, token)
    fase_upload(cfg, token)

def main():
    log("WORKER v10 — baixa tudo, envia tudo, loop 10 min iniciado")
    while True:
        for c in CONFIGS:
            try: processar(c)
            except Exception as ex: log(f"Erro no config {c}: {ex}")
        log("⏸ Ciclo completo. Esperando 5 min..."); time.sleep(300)

if __name__ == "__main__":
    main()
