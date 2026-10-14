#!/usr/bin/env python3
import os, sys, json, time, subprocess, requests

CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_dexte_original.json",
]

PEND = "/tmp/videos_pending"
MAX_FILA = 3
LOTE_UPLOAD = 2

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs(PEND, exist_ok=True)

def log(msg):
    print(f"[worker]: {msg}", flush=True)

def url_com_cache(url):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(time.time())}"

def ler_config(url):
    r = requests.get(url_com_cache(url), timeout=20)
    r.raise_for_status()
    return r.json()

def gh_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "akash-worker-fila"
    }

def ensure_release(token, repo, tag):
    h = gh_headers(token)
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(url, headers=h, timeout=20)

    if r.ok:
        return r.json()

    if r.status_code == 404:
        body = {
            "tag_name": tag,
            "name": tag,
            "body": "Automático via Akash worker",
            "draft": False,
            "prerelease": False
        }
        r2 = requests.post(
            f"https://api.github.com/repos/{repo}/releases",
            headers=h,
            json=body,
            timeout=20
        )
        if r2.ok:
            return r2.json()
        raise RuntimeError(f"Falha criando release: {r2.status_code} {r2.text[:300]}")

    raise RuntimeError(f"Falha lendo release: {r.status_code} {r.text[:300]}")

def listar_existentes(token, repo, tag):
    rel = ensure_release(token, repo, tag)
    rid = rel["id"]
    h = gh_headers(token)
    nomes = set()
    page = 1

    while True:
        url = f"https://api.github.com/repos/{repo}/releases/{rid}/assets?per_page=100&page={page}"
        r = requests.get(url, headers=h, timeout=20)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")

        arr = r.json()
        if not arr:
            break

        for a in arr:
            nomes.add(a["name"])

        if len(arr) < 100:
            break

        page += 1

    return nomes

def arquivos_pendentes():
    try:
        return sorted([f for f in os.listdir(PEND) if f.lower().endswith(".mp4")])
    except FileNotFoundError:
        return []

def pedir_link(tunnel, tmdb, t, e):
    url = f"{tunnel.rstrip('/')}/resolver/{tmdb}/{t}/{e}"

    for i in range(1, 4):
        try:
            r = requests.get(url, timeout=90)
            d = r.json()
            mp4 = d.get("mp4")
            if mp4:
                return mp4
            log(f"    ponte sem link tentativa {i}: {d}")
        except Exception as ex:
            log(f"    erro ponte tentativa {i}: {ex}")

        time.sleep(10)

    return None

def baixar(url, destino):
    r = requests.get(url, stream=True, timeout=60, headers={"User-Agent": UA})
    r.raise_for_status()

    total = int(r.headers.get("content-length", 0))
    baixado = 0
    ultimo = 0
    part = destino + ".part"

    with open(part, "wb") as f:
        for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
            if chunk:
                f.write(chunk)
                baixado += len(chunk)

                if total:
                    pct = int(baixado * 100 / total)
                    if pct >= ultimo + 5:
                        print(
                            f"\r[worker]:     {pct}% ({baixado // 1024 // 1024}/{total // 1024 // 1024} MB)",
                            end="",
                            flush=True
                        )
                        ultimo = pct

    print("", flush=True)
    os.replace(part, destino)
    return baixado

def enviar_pendentes(token, repo, tag):
    files = [os.path.join(PEND, n) for n in arquivos_pendentes()]
    if not files:
        return 0

    enviados = 0
    env = os.environ.copy()
    env["GH_TOKEN"] = token

    for i in range(0, len(files), LOTE_UPLOAD):
        lote = files[i:i + LOTE_UPLOAD]
        nomes = [os.path.basename(x) for x in lote]

        cmd = [
            "gh", "release", "upload",
            tag,
            *lote,
            "--repo", repo,
            "--clobber"
        ]

        sucesso = False

        for tentativa in range(1, 4):
            log(f"    → Enviando lote: {', '.join(nomes)} (tentativa {tentativa}/3)")

            r = subprocess.run(cmd, env=env, capture_output=True, text=True)
            erro = (r.stderr or r.stdout).strip()
            low = erro.lower()

            if r.returncode == 0:
                for fp in lote:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass

                enviados += len(lote)
                log(f"    ✅ Lote enviado: {', '.join(nomes)}")
                sucesso = True
                time.sleep(30)
                break

            sinais = [
                "rate limit",
                "timeout",
                "timed out",
                "403",
                "502",
                "503",
                "connection reset",
                "temporary failure",
                "secondary rate"
            ]

            if any(x in low for x in sinais):
                espera = 90 * tentativa
                log(f"    ⚠️ API instável/limite. Arquivos ficam na fila. Aguardando {espera}s...")
                time.sleep(espera)
                continue

            log(f"    ❌ Erro definitivo no upload: {erro[:500]}")
            return enviados

        if not sucesso:
            log("    ⚠️ Limite persistente. Deixo na fila e continuo no próximo ciclo.")
            return enviados

    return enviados

def processar_config(cfg_url):
    log("=" * 60)
    log(f"CONFIG: {cfg_url}")

    try:
        cfg = ler_config(cfg_url)
    except Exception as ex:
        log(f"❌ Erro lendo config: {ex}")
        return

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        log("❌ GH_TOKEN vazio")
        return

    repo = cfg["github_repo"]
    tag = cfg["github_tag"]
    tmdb = cfg["tmdb"]
    tunnel = cfg["tunnel_url"].rstrip("/")
    tmpl = cfg.get("nome_template", "S{t:02d}E{e:02d}.mp4")
    seasons = cfg["seasons"]

    log(f"Túnel: {tunnel}")
    log(f"Repo: {repo} ({tag})")
    log(f"TMDB: {tmdb}")

    # 1) Primeiro tenta enviar o que já está na fila, mesmo se a ponte cair depois.
    enviados = enviar_pendentes(token, repo, tag)
    if enviados:
        log(f"✅ Enviados da fila antes de baixar: {enviados}")

    # 2) Testa ponte.
    try:
        h = requests.get(f"{tunnel}/health", timeout=15).json()
        log(f"✅ Ponte online: {h}")
    except Exception as ex:
        log(f"❌ Ponte offline: {ex}")
        return

    # 3) Calcula faltantes considerando GitHub + fila local.
    try:
        existentes = listar_existentes(token, repo, tag)
    except Exception as ex:
        log(f"❌ Erro listando GitHub: {ex}")
        return

    pendentes = set(arquivos_pendentes())
    faltando = []

    for s in seasons:
        t = int(s["numero"])
        eps = int(s["episodios"])

        for e in range(1, eps + 1):
            nome = tmpl.format(t=t, e=e)
            if nome not in existentes and nome not in pendentes:
                faltando.append((t, e, nome))

    log(f"📋 GitHub: {len(existentes)} | Fila local: {len(pendentes)} | Faltando: {len(faltando)}")

    if not faltando:
        log("✅ Nada pendente.")
        return

    for t, e, nome in faltando:
        if len(arquivos_pendentes()) >= MAX_FILA:
            log("📦 Fila cheia. Tentando enviar antes de baixar mais.")
            enviar_pendentes(token, repo, tag)

            if len(arquivos_pendentes()) >= MAX_FILA:
                log("⚠️ Ainda cheio após envio. Parando download neste ciclo.")
                break

        log(f"── [{nome}] ──")

        link = pedir_link(tunnel, tmdb, t, e)
        if not link:
            log("❌ Sem link, pulando")
            continue

        destino = os.path.join(PEND, nome)

        try:
            size = baixar(link, destino)
            log(f"✅ Baixado e colocado na fila: {nome} ({size / 1024 / 1024:.1f} MB)")
        except Exception as ex:
            log(f"❌ Falha baixando: {ex}")
            for sufixo in ("", ".part"):
                p = destino + sufixo
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            continue

        # Quando tiver 2 arquivos, tenta enviar os 2 juntos.
        if len(arquivos_pendentes()) >= LOTE_UPLOAD:
            enviar_pendentes(token, repo, tag)

    # No fim, tenta mandar o que sobrou.
    enviar_pendentes(token, repo, tag)

def main():
    log("WORKER FILA COM UPLOAD EM LOTE iniciado")

    while True:
        for c in CONFIGS:
            try:
                processar_config(c)
            except Exception as ex:
                log(f"Erro no config {c}: {ex}")

        log("⏸ Ciclo completo. Esperando 5 min...")
        time.sleep(300)

if __name__ == "__main__":
    main()
