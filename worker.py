#!/usr/bin/env python3
import os, sys, time, shutil, requests
from datetime import datetime
import internetarchive as ia

IDENT = "michaeline1dc92f-s8-pointnexus-org-la-casa-de-papel-24h-parte5-final"
TITLE = "lá casa papel.. parte 5 final"
TMDB = "71446"
PEND = "/tmp/videos_pending"

MAPA = {
    "La_Casa_de_Papel_S05E06.mp4": (6, 1),
    "La_Casa_de_Papel_S05E07.mp4": (6, 2),
    "La_Casa_de_Papel_S05E08.mp4": (6, 3),
    "La_Casa_de_Papel_S05E09.mp4": (6, 4),
    "La_Casa_de_Papel_S05E10.mp4": (6, 5),
}

ENVIADOS = set()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def log(m): print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def garantir_ini():
    ak = (os.environ.get("IA_ACCESS_KEY") or "").strip()
    sk = (os.environ.get("IA_SECRET_KEY") or "").strip()
    if not ak or not sk:
        log("CRITICO: chaves vazias"); return False
    cfg = os.path.expanduser("~/.config/ia.ini")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    open(cfg, "w").write(f"[s3]\naccess = {ak}\nsecret = {sk}\n")
    log("ia.ini gravado"); return True

def pedir_link(tunnel, t, e):
    url = f"{tunnel.rstrip('/')}/resolver/{TMDB}/{t}/{e}"
    for i in range(1, 4):
        try:
            d = requests.get(url, timeout=120).json()
            if d.get("mp4"): return d["mp4"]
            log(f"    sem link {i}: {d}")
        except Exception as ex:
            log(f"    erro {i}: {repr(ex)[:200]}")
        time.sleep(15)
    return None

def baixar(url, destino):
    r = requests.get(url, stream=True, timeout=(30, 300), headers={"User-Agent": UA})
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0)); b = 0; u = 0; part = destino + ".part"
    with open(part, "wb") as f:
        for c in r.iter_content(4 * 1024 * 1024):
            if c:
                f.write(c); b += len(c)
                if total:
                    p = int(b * 100 / total)
                    if p >= u + 5:
                        print(f"\r[worker]:     {p}% ({b//1024//1024}/{total//1024//1024} MB)", end="", flush=True); u = p
    print("", flush=True); os.replace(part, destino); return b

def subir(nome):
    path = os.path.join(PEND, nome)
    for tent in range(1, 4):
        try:
            sts = []
            for resp in ia.upload(IDENT, [path],
                                  metadata={"title": TITLE, "mediatype": "movies"},
                                  queue_derive=False, verbose=False):
                sc = getattr(resp, "status_code", 0) or 0; sts.append(sc)
                log(f"    Archive.org HTTP {sc} {nome}")
            if sts and all(200 <= s < 400 for s in sts):
                return True
        except Exception as e:
            log(f"    upload {nome} tent {tent}/3: {repr(e)[:250]}")
        time.sleep(15 * tent)
    return False

def main():
    log("WORKER v25 — La Casa: 5 finais no item novo, queue_derive=False, sem checar Archive")
    tunnel = (os.environ.get("TUNNEL_URL") or "").strip().rstrip("/")
    if not tunnel:
        log("CRITICO: TUNNEL_URL vazia"); sys.exit(1)
    log(f"TUNNEL_URL = {tunnel}")
    if not garantir_ini(): sys.exit(1)
    os.makedirs(PEND, exist_ok=True)

    while True:
        pend = [(n, t, e) for n, (t, e) in MAPA.items() if n not in ENVIADOS]
        if not pend:
            log("✅ 5/5 enviados. Pode fechar o deploy."); time.sleep(86400)
        log(f"📋 Faltam {len(pend)} neste container.")
        for nome, t, e in pend:
            if shutil.disk_usage(PEND).free < 2_000_000_000:
                log("⚠️ disco baixo, espera 5min"); time.sleep(300); break
            log(f"── {nome} ← fonte T{t:02d}E{e:02d} ──")
            link = pedir_link(tunnel, t, e)
            if not link:
                log("❌ sem link, pula"); continue
            destino = os.path.join(PEND, nome)
            try:
                sz = baixar(link, destino); log(f"✅ baixado {nome} ({sz/1024/1024:.1f} MB)")
            except Exception as ex:
                log(f"❌ falha download: {repr(ex)[:300]}")
                for sx in ("", ".part"):
                    p = destino + sx
                    if os.path.exists(p):
                        try: os.remove(p)
                        except OSError: pass
                continue
            if subir(nome):
                ENVIADOS.add(nome)
                try: os.remove(destino)
                except OSError: pass
                log(f"✅ ENVIADO {nome}"); time.sleep(10)
            else:
                try: os.remove(destino)
                except OSError: pass
                log(f"❌ FALHOU upload {nome}")
        log("⏸ espera 5min e repete só os não enviados."); time.sleep(300)

if __name__ == "__main__":
    main()
