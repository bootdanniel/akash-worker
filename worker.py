#!/usr/bin/env python3
import os, json, time, subprocess, requests
from datetime import datetime

CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_dexte_original.json",
]

PEND = "/tmp/videos_pending"
MAX_FILA = 2
COOLDOWN_RATE = 2700          # 45 min quando GitHub limitar
PAUSA_ENTRE_UPLOADS = 120     # 2 min entre uploads OK
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs(PEND, exist_ok=True)

def log(msg):
    print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)

def gh_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "akash-worker-v9"
    }

def rate_info(token):
    try:
        r = requests.get(
            "https://api.github.com/rate_limit",
            headers=gh_headers(token),
            timeout=20
        )
        d = r.json()
        c = d.get("resources", {}).get("core", {})
        return c.get("remaining"), c.get("reset")
    except Exception as e:
        log(f"⚠️ Não consegui ler rate_limit: {e}")
        return None, None

def garantir_rate(token, minimo=20):
    rem, reset = rate_info(token)

    if rem is None:
        log("⚠️ Sem informação de rate limit. Esperando 10 min por segurança.")
        time.sleep(600)
        return

    if reset is None:
        reset = time.time() + 600

    if rem >= minimo:
        return

    espera = max(120, int(reset - time.time() + 120))
    if espera > 3600:
        espera = 3600

    log(f"⛔ GitHub rate limit baixo. remaining={rem}. Esperando {espera}s...")
    time.sleep(espera)

def erro_rate(text):
    t = text.lower()
    sinais = [
        "rate limit",
        "secondary rate",
        "abuse detection",
        "too many requests",
        "403",
        "429",
        "timeout",
        "timed out",
        "connection reset",
        "temporary failure",
        "502",
        "503",
        "service unavailable",
    ]
    return any(s in t for s in sinais)

def arquivos_pendentes():
    try:
        return sorted([f for f in os.listdir(PEND) if f.lower().endswith(".mp4")])
    except FileNotFoundError:
        return []

def enviar_um(token, repo, tag, nome):
    caminho = os.path.join(PEND, nome)

    if not os.path.exists(caminho):
        return True

    env = os.environ.copy()
    env["GH_TOKEN"] = token

    for tentativa in range(1, 3):
        garantir_rate(token, minimo=20)

        log(f"    → Enviando {nome} (tentativa {tentativa}/2)")

        cmd = [
            "gh", "release", "upload",
            tag,
            caminho,
            "--repo", repo,
            "--clobber"
        ]

        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            log("    ⚠️ Timeout no upload. Arquivo fica na fila.")
            log(f"    ⏳ Cooldown de {COOLDOWN_RATE // 60} min...")
            time.sleep(COOLDOWN_RATE)
            return False

        out = (r.stderr or r.stdout).strip()

        if r.returncode == 0:
            try:
                os.remove(caminho)
            except OSError:
                pass

            log(f"    ✅ Enviado: {nome}")
            time.sleep(PAUSA_ENTRE_UPLOADS)
            return True

        if erro_rate(out):
            log(f"    ⚠️ GitHub limitou/instável: {out[:250]}")
            log(f"    ⏳ Arquivo fica na fila. Cooldown de {COOLDOWN_RATE // 60} min...")
            time.sleep(COOLDOWN_RATE)
            return False

        log(f"    ❌ Erro definitivo no upload: {out[:500]}")
        return False

    return False

def enviar_pendentes(token, repo, tag):
    files = arquivos_pendentes()
    if not files:
        return 0

    enviados = 0

    for nome in files:
        if enviar_um(token, repo, tag, nome):
            enviados += 1
        else:
            break

    return enviados

def url_cache(u):
    sep = "&" if "?" in u else "?"
    return f"{u}{sep}t={int(time.time())}"

def ler_config(u):
    r = requests.get(url_cache(u), timeout=30)
    r.raise_for_status()
    return r.json()

def ensure_release(token, repo, tag):
    h = gh_headers(token)
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(url, headers=h, timeout=30)

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
            timeout=30
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
        garantir_rate(token, minimo=10)

        url = f"https://api.github.com/repos/{repo}/releases/{rid}/assets?per_page=100&page={page}"
        r = requests.get(url, headers=h, timeout=30)

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

def pedir_link(tunnel, tmdb, t, e):
    url = f"{tunnel.rstrip('/')}/resolver/{tmdb}/{t}/{e}"

    for i in range(1, 4):
        try:
            r = requests.get(url, timeout=120)
            d = r.json()
            mp4 = d.get("mp4")
            if mp4:
                return mp4
            log(f"    ponte sem link tentativa {i}: {d}")
        except Exception as ex:
            log(f"    erro ponte tentativa {i}: {ex}")

        time.sleep(15)

    return None

def baixar(url, destino):
    r = requests.get(url, stream=True, timeout=(30, 300), headers={"User-Agent": UA})
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

    # 1) Se já tem fila, tenta enviar antes de baixar qualquer coisa.
    if arquivos_pendentes():
        log(f"📦 Fila local: {len(arquivos_pendentes())}. Tentando enviar antes de baixar.")
        enviados = enviar_pendentes(token, repo, tag)

        if enviados:
            log(f"✅ Enviados da fila: {enviados}")

        if arquivos_pendentes():
            log("⚠️ Ainda há arquivos na fila após tentativa.")
            log("⛔ Não vou baixar mais neste ciclo para não piorar o rate limit.")
            return

    # 2) Testa ponte.
    try:
        h = requests.get(f"{tunnel}/health", timeout=15).json()
        log(f"✅ Ponte online: {h}")
    except Exception as ex:
        log(f"❌ Ponte offline: {ex}")
        return

    # 3) Calcula faltantes.
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

        # Quando tiver 2 na fila, tenta enviar antes de baixar o próximo.
        if len(arquivos_pendentes()) >= MAX_FILA:
            enviar_pendentes(token, repo, tag)

    # No fim, tenta mandar o que sobrou.
    enviar_pendentes(token, repo, tag)

def main():
    log("WORKER v9 COM COOLDOWN FORTE iniciado")

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
