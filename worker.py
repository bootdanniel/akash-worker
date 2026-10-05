#!/usr/bin/env python3
import requests, time, os, sys, subprocess, json, re

# ===== CONFIG =====
CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_storj.json",
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
        ["gh", "release", "upload", tag, arquivo, "--repo", repo, "--clobber", f"--name={nome}"],
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
         "--repo", repo, "--clobber", f"--name={nome}"],
        env=env, capture_output=True, text=True, timeout=300
    )
    if r.returncode == 0:
        print(f"OK: {output_file} enviado")
    else:
        print(f"FALHA: {r.stderr}")




def carregar_progresso(token, repo, tag):
    """Lê recodificados.json do GitHub. Retorna set de EPs já processados."""
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    # Tenta baixar do release
    try:
        url = f"https://github.com/{repo}/releases/download/{tag}/recodificados.json"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return set(r.json())
    except: pass
    return set()


def salvar_progresso(token, repo, tag, processados, nome_arquivo="recodificados.json"):
    """Salva o set de EPs processados no GitHub."""
    import tempfile
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    try:
        json.dump(sorted(list(processados)), tmp, indent=2)
        tmp.close()
        r = subprocess.run(
            ["gh", "release", "upload", tag, tmp.name, "--repo", repo,
             "--clobber", f"--name={nome_arquivo}"],
            env=env, capture_output=True, text=True, timeout=60
        )
        return r.returncode == 0
    finally:
        try: os.remove(tmp.name)
        except: pass


def recodificar_eps(cfg):
    """Modo: baixa EP do GitHub, recodifica pra 1280x720, deleta, sobe de volta."""
    import shutil

    token = os.environ.get("GH_TOKEN", "")
    repo = cfg["github_repo"]
    tag = cfg["github_tag"]
    lista = cfg["eps_recodificar"]
    crf = cfg.get("crf", 23)
    preset = cfg.get("preset", "veryfast")

    print(f"=== MODO RECODIFICAR ===")
    print(f"Repo:   {repo} ({tag})")
    print(f"Total:  {len(lista)} EPs")
    print(f"CRF:    {crf}  |  preset: {preset}")
    print()

    env = os.environ.copy()
    env["GH_TOKEN"] = token

    # Lista de URLs atuais
    print("Buscando URLs dos assets...")
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        env=env, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        print(f"ERRO: {r.stderr}")
        return
    assets = {a["name"]: a["url"] for a in json.loads(r.stdout).get("assets", [])}
    print(f"  {len(assets)} assets disponiveis")
    print()

    ok = 0
    falhas = []

    # Carrega progresso
    ja_feitos = carregar_progresso(token, repo, tag)
    print(f"Ja processados em rodadas anteriores: {len(ja_feitos)}")
    print()

    for i, nome in enumerate(lista, 1):
        print(f"\n{'='*60}")
        print(f"  [{i}/{len(lista)}] {nome}")
        print(f"{'='*60}")

        if nome in ja_feitos:
            print(f"  SKIP: ja processado em rodada anterior")
            ok += 1
            continue

        if nome not in assets:
            print(f"  SKIP: nao encontrado no release")
            falhas.append(f"{nome}: nao encontrado")
            continue

        url_in = assets[nome]
        tmp_in  = f"/tmp/{nome}"
        tmp_out = f"/tmp/recode_{nome}"

        # 1) Baixa
        print(f"  [1/5] Baixando...")
        try:
            with requests.get(url_in, stream=True, timeout=300) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                b = 0
                with open(tmp_in, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4*1024*1024):
                        f.write(chunk); b += len(chunk)
                        if total and b % (20*1024*1024) < 4*1024*1024:
                            print(f"\r    {b*100//total}% ({b//1024//1024}/{total//1024//1024} MB)", end="")
                print()
            print(f"  OK: {os.path.getsize(tmp_in)//1024//1024} MB")
        except Exception as ex:
            print(f"  FALHA download: {ex}")
            if os.path.exists(tmp_in): os.remove(tmp_in)
            falhas.append(f"{nome}: download")
            continue

        # 2) Recodifica
        print(f"  [2/5] Recodificando pra 1280x720 high profile...")
        try:
            cmd = [
                "ffmpeg", "-y", "-i", tmp_in,
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1:1",
                "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.1",
                "-preset", preset, "-crf", str(crf),
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart",
                tmp_out
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                print(f"  FALHA ffmpeg: {r.stderr[-500:]}")
                if os.path.exists(tmp_in): os.remove(tmp_in)
                falhas.append(f"{nome}: ffmpeg")
                continue
            print(f"  OK: {os.path.getsize(tmp_out)//1024//1024} MB")
        except Exception as ex:
            print(f"  FALHA recode: {ex}")
            if os.path.exists(tmp_in): os.remove(tmp_in)
            falhas.append(f"{nome}: recode {ex}")
            continue

        # 3) Apaga original do disco
        try: os.remove(tmp_in)
        except: pass

        # 4) Deleta o asset antigo no GitHub
        print(f"  [3/5] Deletando asset antigo do GitHub...")
        r = subprocess.run(
            ["gh", "release", "delete-asset", tag, nome, "--repo", repo, "--yes"],
            env=env, capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            print(f"  AVISO: {r.stderr.strip()[:150]}")
        else:
            print(f"  OK")

        # 5) Sobe o recodificado
        print(f"  [4/5] Subindo recodificado...")
        r = subprocess.run(
            ["gh", "release", "upload", tag, tmp_out, "--repo", repo, "--clobber", f"--name={nome}"],
            env=env, capture_output=True, text=True, timeout=600
        )
        if r.returncode != 0:
            print(f"  FALHA upload: {r.stderr[:200]}")
            falhas.append(f"{nome}: upload")
            # Não apaga local, deixa pra debug
            continue
        print(f"  OK")

        # 6) Apaga local
        try: os.remove(tmp_out)
        except: pass

        ok += 1
        ja_feitos.add(nome)
        salvar_progresso(token, repo, tag, ja_feitos)
        print(f"  [5/5] ✅ {nome} RECODIFICADO E SUBIDO (progresso salvo)")

    print(f"\n{'='*60}")
    print(f"  RESUMO RECODIFICAR")
    print(f"{'='*60}")
    print(f"  OK:      {ok}/{len(lista)}")
    print(f"  Falhas:  {len(falhas)}")
    for f in falhas:
        print(f"    - {f}")



def catalogar(cfg):
    """Baixa o catálogo completo (filmes + séries) e sobe pro GitHub."""
    import time as _t

    tunnel = cfg["tunnel_url"].rstrip("/")
    repo_destino = cfg.get("repo_destino", "bootdanniel/nexustvplay-catalogo")
    token = os.environ.get("GH_TOKEN", "")

    env = os.environ.copy()
    env["GH_TOKEN"] = token

    print(f"=== MODO CATALOGAR ===")
    print(f"Túnel origem: {tunnel}")
    print(f"Repo destino: {repo_destino}")

    # Testa túnel
    try:
        r = requests.get(f"{tunnel}/health", timeout=15)
        print(f"  ✅ Ponte online: {r.json()}")
    except Exception as e:
        print(f"  ❌ Ponte offline: {e}")
        return

    # ============ FILMES ============
    print(f"\n>>> Baixando lista de filmes...")
    r = requests.get(f"{tunnel}/catalogo/filmes", timeout=60)
    slugs_filmes = r.json()["slugs"]
    print(f"    {len(slugs_filmes)} slugs recebidos")

    filmes = []
    inicio = _t.time()
    for i, slug in enumerate(slugs_filmes, 1):
        try:
            r = requests.get(f"{tunnel}/scrape/filme/{slug}", timeout=20)
            if r.status_code == 200:
                d = r.json()
                if d.get("titulo"):
                    filmes.append(d)
        except: pass

        if i % 50 == 0 or i == len(slugs_filmes):
            dur = (_t.time() - inicio) / 60
            taxa = i / dur if dur else 0
            eta = (len(slugs_filmes) - i) / taxa if taxa else 0
            print(f"    [{i}/{len(slugs_filmes)}] OK={len(filmes)} | {taxa:.0f}/min | ETA {eta:.0f}min")

    print(f"\n✅ Filmes coletados: {len(filmes)}")
    with open("/tmp/filmes.json", "w", encoding="utf-8") as f:
        json.dump(filmes, f, ensure_ascii=False, indent=2)
    print(f"   Salvo: /tmp/filmes.json ({os.path.getsize('/tmp/filmes.json')//1024//1024} MB)")

    # Sobe filmes
    print(f"\n>>> Subindo filmes.json pro GitHub...")
    r = subprocess.run(
        ["gh", "release", "upload", "v1", "/tmp/filmes.json",
         "--repo", repo_destino, "--clobber", "--name=filmes.json"],
        env=env, capture_output=True, text=True, timeout=600
    )
    print(f"   {'✅ OK' if r.returncode == 0 else '❌ ' + r.stderr[:200]}")

    # ============ SÉRIES ============
    print(f"\n>>> Baixando lista de séries...")
    r = requests.get(f"{tunnel}/catalogo/series", timeout=60)
    slugs_series = r.json()["slugs"]
    print(f"    {len(slugs_series)} slugs recebidos")

    series = []
    inicio = _t.time()
    for i, slug in enumerate(slugs_series, 1):
        try:
            r = requests.get(f"{tunnel}/scrape/serie/{slug}", timeout=30)
            if r.status_code == 200:
                d = r.json()
                if d.get("titulo"):
                    series.append(d)
        except: pass

        if i % 25 == 0 or i == len(slugs_series):
            dur = (_t.time() - inicio) / 60
            taxa = i / dur if dur else 0
            eta = (len(slugs_series) - i) / taxa if taxa else 0
            print(f"    [{i}/{len(slugs_series)}] OK={len(series)} | {taxa:.1f}/min | ETA {eta:.0f}min")

    print(f"\n✅ Séries coletadas: {len(series)}")
    with open("/tmp/series.json", "w", encoding="utf-8") as f:
        json.dump(series, f, ensure_ascii=False, indent=2)
    print(f"   Salvo: /tmp/series.json ({os.path.getsize('/tmp/series.json')//1024//1024} MB)")

    # Sobe séries
    print(f"\n>>> Subindo series.json pro GitHub...")
    r = subprocess.run(
        ["gh", "release", "upload", "v1", "/tmp/series.json",
         "--repo", repo_destino, "--clobber", "--name=series.json"],
        env=env, capture_output=True, text=True, timeout=600
    )
    print(f"   {'✅ OK' if r.returncode == 0 else '❌ ' + r.stderr[:200]}")

    print(f"\n{'='*60}")
    print(f"  ✅ CATALOGAÇÃO COMPLETA!")
    print(f"  Filmes: {len(filmes)}")
    print(f"  Séries: {len(series)}")
    print(f"{'='*60}")



def upload_storj(arquivo, nome_remoto, cfg_storj):
    """Sobe arquivo pro STORJ via S3 API."""
    import boto3
    from botocore.config import Config as BotoConfig

    session = boto3.session.Session()
    s3 = session.client(
        service_name="s3",
        aws_access_key_id=cfg_storj["access_key"],
        aws_secret_access_key=cfg_storj["secret_key"],
        endpoint_url=cfg_storj["endpoint"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    try:
        s3.upload_file(arquivo, cfg_storj["bucket"], nome_remoto)
        return True
    except Exception as e:
        print(f"    ❌ STORJ erro: {e}")
        return False


def baixar_top_storj(cfg):
    """Baixa filmes TOP via ponte e sobe pro STORJ."""
    import time as _t

    tunnel = cfg["tunnel_url"].rstrip("/")
    cfg_storj = cfg["storj"]
    lista = cfg["filmes"]

    print(f"=== MODO BAIXAR_TOP_STORJ ===")
    print(f"Túnel: {tunnel}")
    print(f"STORJ: {cfg_storj['endpoint']} | bucket: {cfg_storj['bucket']}")
    print(f"Filmes a baixar: {len(lista)}")
    print()

    # Testa ponte
    try:
        r = requests.get(f"{tunnel}/health", timeout=15)
        print(f"✅ Ponte online: {r.json()}")
    except Exception as e:
        print(f"❌ Ponte offline: {e}")
        return

    # Prepara pasta temp
    os.makedirs("/tmp/videos_top", exist_ok=True)

    ok = 0
    falhas = []

    for i, f in enumerate(lista, 1):
        tmdb = f["tmdb"]
        titulo = f["titulo"]
        ano = f.get("ano", "")
        print(f"\n{'='*60}")
        print(f"  [{i}/{len(lista)}] {titulo} ({ano}) [tmdb {tmdb}]")
        print(f"{'='*60}")

        # Nome do arquivo no STORJ
        nome_limpo = re.sub(r'[^A-Za-z0-9_-]', '_', titulo)[:50]
        nome_arquivo = f"{nome_limpo}_{ano}_{tmdb}.mp4"
        destino_local = f"/tmp/videos_top/{nome_arquivo}"

        # 1) Pede link pra ponte
        print(f"  [1/3] Pedindo link pra ponte...")
        mp4_url = None
        for tent in range(3):
            try:
                r = requests.get(f"{tunnel}/resolver/{tmdb}", timeout=30)
                d = r.json()
                if d.get("mp4"):
                    mp4_url = d["mp4"]
                    break
            except: pass
            time.sleep(3)

        if not mp4_url:
            print(f"  ❌ Sem link")
            falhas.append(f"{titulo}: sem link")
            continue

        # 2) Baixa do MediaFire
        print(f"  [2/3] Baixando {f.get('tamanho_mb', '?')} MB...")
        try:
            h = {"User-Agent": "Mozilla/5.0", "Referer": "https://1take.top/"}
            with requests.get(mp4_url, headers=h, stream=True, timeout=180) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                baixado = 0
                with open(destino_local, "wb") as fp:
                    for chunk in r.iter_content(chunk_size=4*1024*1024):
                        fp.write(chunk); baixado += len(chunk)
                        if total:
                            pct = baixado * 100 // total
                            print(f"\r      {pct}% ({baixado//1024//1024}/{total//1024//1024} MB)", end="", flush=True)
                print()
            sz = os.path.getsize(destino_local) // 1024 // 1024
            print(f"  ✅ Baixado: {sz} MB")
        except Exception as e:
            print(f"  ❌ Erro no download: {e}")
            if os.path.exists(destino_local): os.remove(destino_local)
            falhas.append(f"{titulo}: erro download")
            continue

        # 3) Sobe pro STORJ
        print(f"  [3/3] Subindo pro STORJ...")
        if upload_storj(destino_local, nome_arquivo, cfg_storj):
            print(f"  ✅ Enviado: {nome_arquivo}")
            ok += 1
        else:
            falhas.append(f"{titulo}: erro STORJ")

        # Apaga local
        try: os.remove(destino_local)
        except: pass

    print(f"\n{'='*60}")
    print(f"  RESUMO")
    print(f"{'='*60}")
    print(f"  ✅ OK: {ok}/{len(lista)}")
    print(f"  ❌ Falhas: {len(falhas)}")
    for f in falhas: print(f"    - {f}")


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
                elif cfg_test and cfg_test.get("modo") == "recodificar":
                    recodificar_eps(cfg_test)
                elif cfg_test and cfg_test.get("modo") == "catalogar":
                    catalogar(cfg_test)
                    print("\n✅ Catalogação feita. Sai em 60s...")
                    time.sleep(60)
                    sys.exit(0)
                elif cfg_test and cfg_test.get("modo") == "baixar_top_storj":
                    baixar_top_storj(cfg_test)
                    print("\n✅ Download completo. Sai em 60s...")
                    time.sleep(60)
                    sys.exit(0)
                else:
                    processar_config(url, i, len(CONFIGS))
            except Exception as e:
                print(f"Erro no config {i}: {e}")

        print(f"\n⏸  Ciclo completo. Esperando 5 min pra reler tudo...")
        time.sleep(300)

if __name__ == "__main__":
    main()
