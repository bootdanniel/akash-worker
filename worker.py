#!/usr/bin/env python3
import requests, time, os, sys, subprocess, json, re

CONFIG_URL = "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config.json"
PASTA = "/tmp/videos"

def ler_config():
    r = requests.get(CONFIG_URL, timeout=15)
    r.raise_for_status()
    return r.json()

def listar_github(token, repo, tag):
    """Retorna set de nomes de assets já no release."""
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        env=env, capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"  ⚠️  Erro listando GitHub: {r.stderr.strip()[:200]}")
        return set()
    try:
        data = json.loads(r.stdout)
        return {a["name"] for a in data.get("assets", [])}
    except Exception:
        return set()

def pedir_link(tunnel, tmdb, t, e):
    """Chama a ponte do Termux."""
    url = f"{tunnel}/resolver/{tmdb}/{t}/{e}"
    for tent in range(3):
        try:
            r = requests.get(url, timeout=30)
            d = r.json()
            if d.get("mp4"):
                return d["mp4"]
        except Exception as ex:
            print(f"  ⚠️  Tentativa {tent+1} falhou: {ex}")
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

def main():
    print("="*60)
    print("  WORKER SUPERnatural — Akash")
    print("="*60)

    os.makedirs(PASTA, exist_ok=True)

    while True:
        try:
            print("\n📡 Lendo config.json do GitHub...")
            cfg = ler_config()
        except Exception as ex:
            print(f"❌ Não leu config: {ex}")
            print("   Tentando de novo em 60s...")
            time.sleep(60)
            continue

        tunnel = cfg["tunnel_url"].rstrip("/")
        token  = os.environ.get("GH_TOKEN", "")
        repo   = cfg["github_repo"]
        tag    = cfg["github_tag"]
        tmdb   = cfg["tmdb"]

        print(f"  Túnel: {tunnel}")
        print(f"  Repo:  {repo} ({tag})")

        # testa túnel
        try:
            r = requests.get(f"{tunnel}/health", timeout=15)
            h = r.json()
            print(f"  ✅ Ponte online: {h}")
        except Exception as ex:
            print(f"  ❌ Ponte offline: {ex}")
            print(f"     Esperando 60s...")
            time.sleep(60)
            continue

        print("\n📋 Listando assets existentes no GitHub...")
        existentes = listar_github(token, repo, tag)
        print(f"  {len(existentes)} já no release")

        # monta lista de eps pendentes
        pendentes = []
        for s in cfg["seasons"]:
            t = s["numero"]
            for e in range(1, s["episodios"]+1):
                nome = cfg.get("nome_template", "temp_{t}__ep{e:02d}.mp4").format(t=t, e=e)
                if nome not in existentes:
                    pendentes.append((t, e, nome))

        print(f"\n🎯 {len(pendentes)} episódios pendentes")
        if not pendentes:
            print("✅ TUDO COMPLETO! Saindo.")
            break

        for t, e, nome in pendentes:
            print(f"\n{'='*60}")
            print(f"  [{nome}]")
            print(f"{'='*60}")

            print(f"  → Pedindo link pro celular...")
            mp4 = pedir_link(tunnel, tmdb, t, e)
            if not mp4:
                print(f"  ❌ Sem link, pulando")
                time.sleep(5)
                continue

            destino = os.path.join(PASTA, nome)
            print(f"  → Baixando do MediaFire...")
            try:
                baixar(mp4, destino)
                sz = os.path.getsize(destino)//1024//1024
                print(f"  ✅ Baixado ({sz} MB)")
            except Exception as ex:
                print(f"  ❌ Erro no download: {ex}")
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

        print("\n🔄 Ciclo completo. Checando de novo em 5 min...")
        time.sleep(300)

if __name__ == "__main__":
    main()
