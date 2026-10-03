#!/usr/bin/env python3
import requests, time, os, sys, subprocess, json, re

# ===== CONFIG =====
CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_tapas.json",
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_grimm.json",
]
PASTA = "/tmp/videos"
# ==================

def ler_config(url):
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ Erro lendo {url}: {e}")
        return None

def listar_github(token, repo, tag):
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        env=env, capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"  ⚠️  {r.stderr.strip()[:200]}")
        return set()
    try:
        data = json.loads(r.stdout)
        return {a["name"] for a in data.get("assets", [])}
    except Exception:
        return set()

def pedir_link(tunnel, tmdb, t, e):
    url = f"{tunnel}/resolver/{tmdb}/{t}/{e}"
    for tent in range(3):
        try:
            r = requests.get(url, timeout=30)
            d = r.json()
            if d.get("mp4"):
                return d["mp4"]
        except Exception as ex:
            print(f"  ⚠️  Tentativa {tent+1}: {ex}")
            time.sleep(3)
    return None

def baixar(mp4, destino):
    h = {"User-Agent": "Mozilla/5.0", "Referer": "https://1take.top/"}
    with requests.get(mp4, headers=h, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        b = 0
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=4*1024*1024):
                f.write(chunk)
                b += len(chunk)
                if total:
                    pct = b * 100 // total
                    print(f"\r    {pct}% ({b//1024//1024}/{total//1024//1024} MB)", end="")
        print()

def upload(token, repo, tag, arquivo, nome):
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    r = subprocess.run(
        ["gh", "release", "upload", tag, arquivo, "--repo", repo, "--clobber"],
        env=env, capture_output=True, text=True
    )
    return r.returncode == 0, r.stderr

def processar_config(cfg_url, cfg_num, cfg_total):
    print(f"\n{'='*60}")
    print(f"  CONFIG {cfg_num}/{cfg_total}: {cfg_url.split('/')[-1]}")
    print(f"{'='*60}")

    cfg = ler_config(cfg_url)
    if not cfg:
        print("  ❌ Falhou. Pulando pro próximo.")
        return

    tunnel = cfg["tunnel_url"].rstrip("/")
    token  = os.environ.get("GH_TOKEN", "")
    repo   = cfg["github_repo"]
    tag    = cfg["github_tag"]
    tmdb   = cfg["tmdb"]
    tmpl   = cfg.get("nome_template", "temp_{t}__ep{e:02d}.mp4")

    print(f"  Túnel: {tunnel}")
    print(f"  Repo:  {repo} ({tag})")
    print(f"  TMDB:  {tmdb}")

    # testa túnel
    try:
        h = requests.get(f"{tunnel}/health", timeout=15).json()
        print(f"  ✅ Ponte online: {h}")
    except Exception as ex:
        print(f"  ❌ Ponte offline: {ex}")
        print(f"     Vai tentar de novo em 60s...")
        time.sleep(60)
        return  # vai pro próximo ciclo do while principal

    print(f"\n  📋 Listando assets no GitHub...")
    existentes = listar_github(token, repo, tag)
    print(f"  {len(existentes)} já no release")

    # monta lista pendente
    pendentes = []
    for s in cfg["seasons"]:
        t = s["numero"]
        for e in range(1, s["episodios"]+1):
            nome = tmpl.format(t=t, e=e)
            if nome not in existentes:
                pendentes.append((t, e, nome))

    print(f"  🎯 {len(pendentes)} pendentes")
    if not pendentes:
        print(f"  ✅ Config completo! Passa pro próximo.")
        return

    for t, e, nome in pendentes:
        print(f"\n  ── [{nome}] ──")
        print(f"  → Pedindo link...")
        mp4 = pedir_link(tunnel, tmdb, t, e)
        if not mp4:
            print(f"  ❌ Sem link, pulando")
            time.sleep(5)
            continue

        destino = os.path.join(PASTA, nome)
        print(f"  → Baixando...")
        try:
            baixar(mp4, destino)
            sz = os.path.getsize(destino)//1024//1024
            print(f"  ✅ Baixado ({sz} MB)")
        except Exception as ex:
            print(f"  ❌ Erro download: {ex}")
            if os.path.exists(destino): os.remove(destino)
            continue

        print(f"  → Enviando pro GitHub...")
        ok, err = upload(token, repo, tag, destino, nome)
        if ok:
            print(f"  ✅ Enviado")
        else:
            print(f"  ❌ Falha: {err.strip()[:200]}")

        try:
            os.remove(destino)
            print(f"  🗑  Apagado local")
        except: pass
        time.sleep(3)

def main():
    print("="*60)
    print("  WORKER FILA — Múltiplas séries")
    print("="*60)
    print(f"  Configs: {len(CONFIGS)}")

    os.makedirs(PASTA, exist_ok=True)

    while True:
        print(f"\n{'#'*60}")
        print(f"# CICLO COMPLETO — {time.strftime('%H:%M:%S')}")
        print(f"{'#'*60}")

        for i, url in enumerate(CONFIGS, 1):
            try:
                processar_config(url, i, len(CONFIGS))
            except Exception as e:
                print(f"❌ Erro no config {i}: {e}")

        print(f"\n⏸  Ciclo completo. Esperando 5 min pra reler tudo...")
        time.sleep(300)

if __name__ == "__main__":
    main()
