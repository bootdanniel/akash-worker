#!/usr/bin/env python3
import os, sys, json, time, shutil, requests
from datetime import datetime
import internetarchive as ia

CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_dexter_archive.json",
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_lacasa_archive.json",
]

PEND = "/tmp/videos_pending"
STATE = "/tmp/archive_state"

PAUSA_ENTRE_ENVIOS = 10
RETRIES_ARQUIVO = 3
FALHAS_SEGUIDAS_PARA = 3

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs(PEND, exist_ok=True)
os.makedirs(STATE, exist_ok=True)

def log(m):
    print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def url_cache(u):
    return f"{u}{'&' if '?' in u else '?'}t={int(time.time())}"

def ler_config(u):
    r = requests.get(url_cache(u), timeout=30)
    r.raise_for_status()
    return r.json()

def garantir_ini():
    ak = (os.environ.get("IA_ACCESS_KEY") or "").strip()
    sk = (os.environ.get("IA_SECRET_KEY") or "").strip()

    if not ak or not sk:
        log("CRITICO: IA_ACCESS_KEY / IA_SECRET_KEY vazias no env do deploy.")
        return False

    cfg = os.path.expanduser("~/.config/ia.ini")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)

    with open(cfg, "w", encoding="utf-8") as f:
        f.write(f"[s3]\naccess_key = {ak}\nsecret_key = {sk}\n")

    log("ia.ini gravado com as chaves do deploy.")
    return True

def state_path(identifier):
    return os.path.join(STATE, identifier + ".txt")

def carregar_estado(identifier):
    p = state_path(identifier)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()
    except Exception as e:
        log(f"⚠️ carregar_estado: {repr(e)[:200]}")
        return set()

def marcar_enviado(identifier, nome):
    p = state_path(identifier)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(nome + "\n")
    except Exception as e:
        log(f"⚠️ marcar_enviado: {repr(e)[:200]}")

def listar_mp4(identifier):
    try:
        r = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30)

        if r.status_code != 200:
            return set()

        files = r.json().get("files") or []
        return {
            f.get("name")
            for f in files
            if str(f.get("name", "")).lower().endswith(".mp4")
        }

    except Exception as e:
        log(f"⚠️ listar_mp4: {repr(e)[:240]}")
        return set()

def pendentes():
    try:
        return sorted([f for f in os.listdir(PEND) if f.lower().endswith(".mp4")])
    except FileNotFoundError:
        return []

def remover_local(nome):
    p = os.path.join(PEND, nome)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass

def pedir_link(tunnel, tmdb, t, e):
    url = f"{tunnel.rstrip('/')}/resolver/{tmdb}/{t}/{e}"

    for i in range(1, 4):
        try:
            d = requests.get(url, timeout=120).json()
            if d.get("mp4"):
                return d["mp4"]
            log(f"    ponte sem link {i}: {d}")
        except Exception as ex:
            log(f"    erro ponte {i}: {ex}")

        time.sleep(15)

    return None

def baixar(url, destino):
    r = requests.get(url, stream=True, timeout=(30, 300), headers={"User-Agent": UA})
    r.raise_for_status()

    total = int(r.headers.get("content-length", 0))
    baix = 0
    ult = 0
    part = destino + ".part"

    with open(part, "wb") as f:
        for c in r.iter_content(4 * 1024 * 1024):
            if c:
                f.write(c)
                baix += len(c)

                if total:
                    p = int(baix * 100 / total)
                    if p >= ult + 5:
                        print(
                            f"\r[worker]:     {p}% ({baix // 1024 // 1024}/{total // 1024 // 1024} MB)",
                            end="",
                            flush=True
                        )
                        ult = p

    print("", flush=True)
    os.replace(part, destino)
    return baix

def subir_um(identifier, nome, title):
    path = os.path.join(PEND, nome)

    if not os.path.exists(path):
        return True

    ultimo = ""

    for tent in range(1, RETRIES_ARQUIVO + 1):
        try:
            statuses = []

            for resp in ia.upload(
                identifier,
                [path],
                metadata={"title": title, "mediatype": "movies"},
                retries=3,
                verbose=False
            ):
                sc = getattr(resp, "status_code", 0) or 0
                statuses.append(sc)
                log(f"    Archive.org HTTP {sc} {nome}")

            if statuses and all(200 <= sc < 400 for sc in statuses):
                return True

            ultimo = f"status={statuses}"

        except Exception as e:
            ultimo = repr(e)
            log(f"    upload {nome} tent {tent}/{RETRIES_ARQUIVO}: {ultimo[:300]}")

        time.sleep(15 * tent)

    log(f"    ❌ {nome} falhou definitivamente: {ultimo[:450]}")
    return False

def processar_config(cfg_url):
    log("=" * 60)
    log(f"CONFIG: {cfg_url}")

    try:
        cfg = ler_config(cfg_url)
    except Exception as ex:
        log(f"❌ Erro lendo config: {ex}")
        return

    identifier = cfg["archive_identifier"]
    title = cfg["archive_title"]
    tmdb = cfg["tmdb"]
    tunnel = cfg["tunnel_url"].rstrip("/")
    tmpl = cfg.get("nome_template", "S{t:02d}E{e:02d}.mp4")

    log(f"Item Archive: {identifier} | título: {title!r} | TMDB: {tmdb}")

    existentes = listar_mp4(identifier)
    existentes.update(carregar_estado(identifier))

    # 1) Primeiro reenvia o que já estiver na fila local.
    falhas = 0

    for nome in pendentes():
        if nome in existentes:
            remover_local(nome)
            continue

        if subir_um(identifier, nome, title):
            remover_local(nome)
            marcar_enviado(identifier, nome)
            existentes.add(nome)
            log(f"✅ enviado da fila: {nome}")
            falhas = 0
            time.sleep(PAUSA_ENTRE_ENVIOS)
        else:
            falhas += 1
            log(f"❌ {nome} ficou na fila (falha {falhas})")
            if falhas >= FALHAS_SEGUIDAS_PARA:
                log("⚠️ Muitas falhas seguidas no reenvio. Paro este ciclo.")
                break

    # 2) Confere ponte antes de baixar.
    try:
        h = requests.get(f"{tunnel}/health", timeout=15).json()
        log(f"✅ Ponte online: {h}")
    except Exception as ex:
        log(f"❌ Ponte offline: {ex}")
        return

    # 3) Calcula faltantes.
    fila_set = set(pendentes())
    faltando = []

    for s in cfg["seasons"]:
        t = int(s["numero"])
        for e in range(1, int(s["episodios"]) + 1):
            n = tmpl.format(t=t, e=e)
            if n not in existentes and n not in fila_set:
                faltando.append((t, e, n))

    log(f"📋 {len(existentes)} no item/estado local | {len(faltando)} a baixar/enviar")

    if not faltando:
        log("✅ Nada pendente.")
        return

    # 4) Baixa 1, envia 1, apaga local, vai para o próximo.
    falhas = 0

    for i, (t, e, nome) in enumerate(faltando, 1):
        free = shutil.disk_usage(PEND).free

        if free < 1_600_000_000:
            log(f"⚠️ Espaço baixo ({free // 1024 // 1024} MB livres). Paro download deste ciclo.")
            break

        log(f"── [{i}/{len(faltando)}] {nome} ──")

        link = pedir_link(tunnel, tmdb, t, e)

        if not link:
            log("❌ Sem link, pulando")
            continue

        destino = os.path.join(PEND, nome)

        try:
            sz = baixar(link, destino)
            log(f"✅ baixado {nome} ({sz / 1024 / 1024:.1f} MB)")
        except Exception as ex:
            log(f"❌ falha baixando: {ex}")
            for sx in ("", ".part"):
                p = destino + sx
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            continue

        if subir_um(identifier, nome, title):
            remover_local(nome)
            marcar_enviado(identifier, nome)
            existentes.add(nome)
            log(f"✅ ENVIADO {nome}")
            falhas = 0
            time.sleep(PAUSA_ENTRE_ENVIOS)
        else:
            falhas += 1
            log(f"❌ {nome} falhou no upload, fica em PEND (falha {falhas})")
            if falhas >= FALHAS_SEGUIDAS_PARA:
                log("⚠️ Muitas falhas seguidas. Paro este ciclo e tento de novo depois.")
                break

def main():
    log("WORKER v21 — ARCHIVE.ORG via internetarchive, 1 a 1, sem multipart")

    if not garantir_ini():
        sys.exit(1)

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
