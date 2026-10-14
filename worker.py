#!/usr/bin/env python3
import os, json, time, shutil, requests, boto3
from datetime import datetime
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

CONFIGS = [
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_dexter_archive.json",
    "https://raw.githubusercontent.com/bootdanniel/akash-worker/main/config_lacasa_archive.json",
]

PEND = "/tmp/videos_pending"
S3_ENDPOINT = "https://s3.us.archive.org"
PAUSA_ENTRE_ENVIOS = 20
RETRIES_ARQUIVO = 3
FALHAS_SEGUIDAS_PARA = 3

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
os.makedirs(PEND, exist_ok=True)

def log(m): print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def url_cache(u): return f"{u}{'&' if '?' in u else '?'}t={int(time.time())}"
def ler_config(u):
    r = requests.get(url_cache(u), timeout=30); r.raise_for_status(); return r.json()

def make_s3():
    ak = os.environ.get("IA_ACCESS_KEY", "")
    sk = os.environ.get("IA_SECRET_KEY", "")
    if not ak or not sk:
        raise RuntimeError("IA_ACCESS_KEY / IA_SECRET_KEY vazias")
    sess = boto3.session.Session(aws_access_key_id=ak, aws_secret_access_key=sk)
    return sess.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1",
                       config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}))

def listar_itens(s3, identifier):
    keys = set(); token = None
    try:
        while True:
            kw = {"Bucket": identifier, "MaxKeys": 1000}
            if token: kw["ContinuationToken"] = token
            r = s3.list_objects_v2(**kw)
            for o in r.get("Contents", []): keys.add(o["Key"])
            if not r.get("IsTruncated"): break
            token = r["NextContinuationToken"]
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchBucket", "404"):
            return set()
        raise
    return keys

def ensure_title(identifier, title):
    try:
        g = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30)
        if g.status_code != 200:
            return
        md = g.json().get("metadata", {}) or {}
        if md.get("title") == title:
            return
        p = requests.post(f"https://archive.org/metadata/{identifier}",
                          json={"title": title, "mediatype": "movies"}, timeout=30)
        log(f"🏷️  title -> {title!r} (http {p.status_code})")
    except Exception as e:
        log(f"⚠️  ensure_title falhou (seguimos): {e}")

def pendentes():
    try: return sorted([f for f in os.listdir(PEND) if f.lower().endswith(".mp4")])
    except FileNotFoundError: return []

def remover_local(n):
    p = os.path.join(PEND, n)
    try:
        if os.path.exists(p): os.remove(p)
    except OSError: pass

def pedir_link(tun, tmdb, t, e):
    u = f"{tun.rstrip('/')}/resolver/{tmdb}/{t}/{e}"
    for i in range(1, 4):
        try:
            d = requests.get(u, timeout=120).json()
            if d.get("mp4"): return d["mp4"]
            log(f"    ponte sem link {i}: {d}")
        except Exception as ex:
            log(f"    erro ponte {i}: {ex}")
        time.sleep(15)
    return None

def baixar(url, dest):
    r = requests.get(url, stream=True, timeout=(30, 300), headers={"User-Agent": UA})
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0)); b = 0; ult = 0; part = dest + ".part"
    with open(part, "wb") as f:
        for c in r.iter_content(4 * 1024 * 1024):
            if c:
                f.write(c); b += len(c)
                if total:
                    p = int(b * 100 / total)
                    if p >= ult + 5:
                        print(f"\r[worker]:     {p}% ({b//1024//1024}/{total//1024//1024} MB)", end="", flush=True); ult = p
    print("", flush=True); os.replace(part, dest); return b

def subir_um(s3, identifier, nome):
    path = os.path.join(PEND, nome)
    for tent in range(1, RETRIES_ARQUIVO + 1):
        try:
            s3.upload_file(path, identifier, nome)
            return True
        except Exception as e:
            log(f"    ⚠️  upload {nome} tent {tent}/{RETRIES_ARQUIVO}: {str(e)[:200]}")
            time.sleep(30 * tent)
    return False

def enviar_pendentes(s3, identifier, existentes):
    fila = pendentes()
    if not fila: return 0
    log(f"📤 reenviando {len(fila)} pendente(s) do ciclo anterior")
    enviados = 0; falhas = 0
    for n in fila:
        if n in existentes:
            remover_local(n); continue
        if subir_um(s3, identifier, n):
            remover_local(n); existentes.add(n); enviados += 1; falhas = 0
            log(f"✅ enviado {n}"); time.sleep(PAUSA_ENTRE_ENVIOS)
        else:
            falhas += 1
            log(f"❌ {n} ficou em PEND (falha {falhas})")
            if falhas >= FALHAS_SEGUIDAS_PARA:
                log(" muitas falhas seguidas, paro reenvio deste ciclo"); break
    return enviados

def processar_config(cu):
    log("=" * 60); log(f"CONFIG: {cu}")
    try: cfg = ler_config(cu)
    except Exception as ex: log(f"❌ config: {ex}"); return

    identifier = cfg["archive_identifier"]; title = cfg["archive_title"]
    tmdb = cfg["tmdb"]; tun = cfg["tunnel_url"].rstrip("/")
    tmpl = cfg.get("nome_template", "S{t:02d}E{e:02d}.mp4")

    log(f"Item Archive: {identifier}  (título: {title!r}) | TMDB: {tmdb}")

    try: s3 = make_s3()
    except Exception as ex: log(f"❌ S3: {ex}"); return

    ensure_title(identifier, title)

    try: existentes = listar_itens(s3, identifier)
    except Exception as ex: log(f"❌ listando item: {ex}"); return

    enviar_pendentes(s3, identifier, existentes)

    fila_set = set(pendentes())
    faltando = []
    for s in cfg["seasons"]:
        t = int(s["numero"])
        for e in range(1, int(s["episodios"]) + 1):
            n = tmpl.format(t=t, e=e)
            if n not in existentes and n not in fila_set:
                faltando.append((t, e, n))

    log(f"📋 {len(existentes)} no item | {len(faltando)} a baixar/enviar")
    if not faltando:
        log("✅ nada pendente."); return

    for i, (t, e, n) in enumerate(faltando, 1):
        if shutil.disk_usage(PEND).free < 1_600_000_000:
            log("⚠️ disco baixo, paro download"); break
        log(f"── [{i}/{len(faltando)}] {n} ──")
        link = pedir_link(tun, tmdb, t, e)
        if not link:
            log("❌ sem link, pulando"); continue
        try:
            sz = baixar(link, os.path.join(PEND, n))
            log(f"✅ baixado {n} ({sz/1024/1024:.1f} MB)")
        except Exception as ex:
            log(f"❌ falha baixando: {ex}")
            for sx in ("", ".part"):
                p = os.path.join(PEND, n) + sx
                if os.path.exists(p):
                    try: os.remove(p)
                    except OSError: pass
            continue
        if subir_um(s3, identifier, n):
            remover_local(n); existentes.add(n)
            log(f"✅ enviado {n}"); time.sleep(PAUSA_ENTRE_ENVIOS)
        else:
            log(f"❌ {n} falhou no upload, fica em PEND pro próximo ciclo")

def main():
    log("WORKER v15 — ARCHIVE.ORG 1 a 1 (Dexter -> La Casa) iniciado")
    while True:
        for c in CONFIGS:
            try: processar_config(c)
            except Exception as ex: log(f"Erro config {c}: {ex}")
        log("⏸ ciclo completo, espero 5 min"); time.sleep(300)

if __name__ == "__main__":
    main()
