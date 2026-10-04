#!/usr/bin/env python3
import requests, time, os, sys, subprocess, json, re

# ===== CONFIG =====
CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_verificar.json",
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
        env=env, capture_output=True, text=True, timeout=60
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
        env=env, capture_output=True, text=True, timeout=300
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


def verificar_resolucoes(cfg):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import defaultdict

    token = os.environ.get("GH_TOKEN", "")
    repo = cfg["github_repo"]
    tag = cfg["github_tag"]
    output_file = cfg.get("output_file", "resolucoes.txt")

    print(f"=== MODO VERIFICAR ===")
    print(f"Repo: {repo} ({tag})")

    env = os.environ.copy()
    env["GH_TOKEN"] = token

    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        env=env, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        print(f"ERRO listando assets: {r.stderr}")
        return

    data = json.loads(r.stdout)
    assets = data.get("assets", [])
    print(f"Total de arquivos: {len(assets)}")

    eps = [{"name": a["name"], "url": a["url"]} for a in assets if a["name"].endswith(".mp4")]
    print(f"Videos .mp4: {len(eps)}")
    if not eps:
        print("Nada pra verificar.")
        return

    def checar(ep):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,bit_rate",
                 "-of", "csv=p=0",
                 "-analyzeduration", "1000000",
                 "-probesize", "500000",
                 ep["url"]],
                capture_output=True, text=True, timeout=60
            )
            if r.returncode != 0:
                return (ep["name"], "ERRO", "")
            parts = r.stdout.strip().split(",")
            w, h = parts[0], parts[1]
            br = parts[2] if len(parts) > 2 else "?"
            return (ep["name"], f"{w}x{h}", br)
        except Exception as e:
            return (ep["name"], f"ERRO:{e}", "")

    print("Rodando ffprobe em paralelo (4 workers)...")
    resultados = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(checar, ep) for ep in eps]
        for i, f in enumerate(as_completed(futures), 1):
            nome, res, br = f.result()
            resultados.append({"name": nome, "res": res, "br": br})
            if i % 10 == 0 or i == len(eps):
                print(f"  [{i}/{len(eps)}] {nome} -> {res}")

    por_res = defaultdict(list)
    for r in resultados:
        por_res[r["res"]].append(r["name"])

    linhas = []
    linhas.append("=" * 70)
    linhas.append("  RESOLUCOES DOS EPISODIOS")
    linhas.append("=" * 70)
    linhas.append("")
    linhas.append(f"Total: {len(resultados)} episodios")
    linhas.append("")
    linhas.append("=" * 70)
    linhas.append("  AGRUPAMENTO POR RESOLUCAO")
    linhas.append("=" * 70)
    linhas.append("")
    for res, lista in sorted(por_res.items(), key=lambda x: -len(x[1])):
        linhas.append(f"### {res} ({len(lista)} eps)")
        for n in sorted(lista):
            linhas.append(f"  {n}")
        linhas.append("")
    linhas.append("=" * 70)
    linhas.append("  RESUMO")
    linhas.append("=" * 70)
    for res, lista in sorted(por_res.items(), key=lambda x: -len(x[1])):
        linhas.append(f"  {res}: {len(lista)} eps")

    conteudo = "\n".join(linhas)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(conteudo)

    print(f"\nSalvo local: {output_file}")
    print("=" * 40)
    print(conteudo[:3000])
    print("=" * 40)

    print(f"Subindo {output_file} pro GitHub...")
    r = subprocess.run(
        ["gh", "release", "upload", tag, output_file,
         "--repo", repo, "--clobber"],
        env=env, capture_output=True, text=True, timeout=300
    )
    if r.returncode == 0:
        print(f"OK: {output_file} enviado")
    else:
        print(f"FALHA: {r.stderr}")


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
                cfg_test = ler_config(url)
                if cfg_test and cfg_test.get("modo") == "verificar":
                    verificar_resolucoes(cfg_test)
                else:
                    processar_config(url, i, len(CONFIGS))
            except Exception as e:
                print(f"Erro no config {i}: {e}")

        print(f"\n⏸  Ciclo completo. Esperando 5 min pra reler tudo...")
        time.sleep(300)

if __name__ == "__main__":
    main()
