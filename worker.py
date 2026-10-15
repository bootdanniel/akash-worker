#!/usr/bin/env python3
import os, sys, time, shutil, subprocess, requests
from datetime import datetime
import internetarchive as ia

PLAYLIST_URL = (os.environ.get("PLAYLIST_URL") or
    "https://www.youtube.com/playlist?list=PLkbr4tX7v1Eehwa9zU55PatmrrcFufzz3").strip()
EP_START = int(os.environ.get("EP_START") or 1)
EP_END   = int(os.environ.get("EP_END") or 128)

IDENT = (os.environ.get("IA_IDENTIFIER") or
    "michaeline1dc92f-s8-pointnexus-org-marcados-pelo-passado").strip()
TITLE = (os.environ.get("IA_TITLE") or "Marcados Pelo Passado").strip()

YTDLP = "yt-dlp"
FMT = "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*[height<=720]+ba/b"
PEND = "/tmp/videos_pending"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

ENVIADOS = set()
FALHAS = []          # (n, motivo)
FATAL_WRITE = False
block_streak = 0

def log(m): print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def garantir_ini():
    ak = (os.environ.get("IA_ACCESS_KEY") or "").strip()
    sk = (os.environ.get("IA_SECRET_KEY") or "").strip()
    if not ak or not sk:
        log("CRITICO: chaves vazias."); return False
    cfg = os.path.expanduser("~/.config/ia.ini")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    open(cfg, "w").write(f"[s3]\naccess = {ak}\nsecret = {sk}\n")
    log("ia.ini gravado."); return True

def eh_acesso_negado(t):
    t = (t or "").lower()
    return any(s in t for s in ["access denied","sufficient privileges","usernamecanwrite",
                                "invalidaccesskeyid","signaturedoesnotmatch"])

def eh_block_yt(t):
    t = (t or "").lower()
    return any(s in t for s in ["not a bot","sign in to confirm","captcha","http error 403",
                                "precondition failed","please sign in"])

def stream_run(cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    buf = []
    for line in p.stdout:
        s = line.rstrip("\n")
        if s:
            log("    ytdlp| " + s[:240]); buf.append(s)
    p.wait()
    return p.returncode, "\n".join(buf)

def listar_existentes():
    try:
        r = requests.get(f"https://archive.org/metadata/{IDENT}", timeout=30)
        if r.status_code != 200: return set()
        return {f.get("name") for f in (r.json().get("files") or [])
                if str(f.get("name","")).lower().endswith(".mp4")}
    except Exception as e:
        log(f"⚠️ listar: {repr(e)[:200]}"); return set()

def nome_ep(n): return f"Marcados_Pelo_Passado_E{n:03d}.mp4"

def baixar_ep(n):
    """Baixa a posição n da playlist. Retorna (ok, titulo, tamanho, motivo)."""
    tmp = os.path.join(PEND, f"_dl_{n}")
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)
    base = [YTDLP, "--no-warnings", "--no-progress", "--newline",
            "-f", FMT, "--merge-output-format", "mp4",
            "-o", os.path.join(tmp, "v.%(ext)s"),
            "--print", "%(title)s",
            "--playlist-items", str(n), PLAYLIST_URL]
    rc, out = stream_run(base)
    if rc != 0 and eh_block_yt(out):
        log("    retry com player_client alternativo...")
        rc, out = stream_run(base + ["--extractor-args", "youtube:player_client=tv,android"])
    # titulo = 1a linha que nao parece log de erro/progresso
    titulo = ""
    for l in out.splitlines():
        ls = l.strip()
        if not ls or ls.startswith("[") or "error" in ls.lower() or "yt-dlp" in ls.lower():
            continue
        titulo = ls; break
    arq = None
    if os.path.isdir(tmp):
        for f in sorted(os.listdir(tmp)):
            if f.lower().endswith(".mp4"): arq = os.path.join(tmp, f); break
        if arq is None:
            fs = os.listdir(tmp); arq = os.path.join(tmp, fs[0]) if fs else None
    if rc != 0 or arq is None:
        shutil.rmtree(tmp, ignore_errors=True)
        motivo = "block_youtube" if eh_block_yt(out) else (out.splitlines()[-1][:160] if out.strip() else "sem arquivo")
        return False, titulo, 0, motivo
    destino = os.path.join(PEND, nome_ep(n))
    os.replace(arq, destino); shutil.rmtree(tmp, ignore_errors=True)
    return True, titulo, os.path.getsize(destino), ""

def subir(path, nome):
    global FATAL_WRITE
    ultimo = ""
    for usar_derive in (True, False):   # True=padrao, False=queue_derive=False
        try:
            kw = {"metadata": {"title": TITLE, "mediatype": "movies"}, "verbose": False}
            if not usar_derive: kw["queue_derive"] = False
            sts = []
            for resp in ia.upload(IDENT, [path], **kw):
                sc = getattr(resp, "status_code", 0) or 0; sts.append(sc)
                log(f"    Archive.org HTTP {sc} {nome}")
            if sts and all(200 <= s < 400 for s in sts):
                return True
            ultimo = f"status={sts}"
            if any(s in (401, 403) for s in sts):
                continue   # tenta o outro modo de derive
        except Exception as e:
            ultimo = repr(e); log(f"    upload {nome} modo_derive={usar_derive}: {ultimo[:300]}")
            if eh_acesso_negado(ultimo):
                continue
    if eh_acesso_negado(ultimo):
        FATAL_WRITE = True
        log("    ❌ Access Denied nos dois modos. Parando por bloqueio de escrita.")
    return False

def main():
    global block_streak
    log(f"WORKER v27 — YouTube playlist -> Archive | item={IDENT} | eps {EP_START}..{EP_END}")
    tunnel = (os.environ.get("TUNNEL_URL") or "").strip()
    log(f"TUNNEL_URL (ponte) = {tunnel or '(nao usada no download do YT; yt-dlp vai direto)'}")
    if not garantir_ini(): sys.exit(1)
    os.makedirs(PEND, exist_ok=True)

    ENVIADOS.update(listar_existentes())
    if ENVIADOS: log(f"ℹ️ ja no item (metadata): {len(ENVIADOS)} mp4")

    while True:
        faltam = [n for n in range(EP_START, EP_END+1) if nome_ep(n) not in ENVIADOS]
        if not faltam:
            log(f"✅ Completo {EP_END-EP_START+1}/{EP_END-EP_START+1}. Pode fechar o deploy.")
            time.sleep(86400)
        log(f"📋 Faltam {len(faltam)} posicoes.")
        for n in faltam:
            if FATAL_WRITE: sys.exit(3)
            nome = nome_ep(n)
            if shutil.disk_usage(PEND).free < 2_000_000_000:
                log("⚠️ disco baixo, espera 5min."); time.sleep(300); break
            ok, titulo, sz, motivo = baixar_ep(n)
            if not ok:
                log(f"❌ [{n}] download falhou: {motivo}")
                FALHAS.append((n, motivo))
                if motivo == "block_youtube":
                    block_streak += 1
                    if block_streak >= 4:
                        log("⛔ YouTube bloqueando o IP (4 falhas seguidas de bot/403). Parando."); sys.exit(4)
                else:
                    block_streak = 0
                continue
            block_streak = 0
            log(f"✅ baixado [{n}] {titulo!r} ({sz/1024/1024:.1f} MB)")
            path = os.path.join(PEND, nome)
            if subir(path, nome):
                ENVIADOS.add(nome)
                try: os.remove(path)
                except OSError: pass
                log(f"✅ ENVIADO [{n}] {nome}")
                time.sleep(10)
            else:
                try: os.remove(path)
                except OSError: pass
                FALHAS.append((n, "upload"))
                if FATAL_WRITE: sys.exit(3)
        log("⏸ varredura completa; espera 5min e repete so as nao enviadas.")
        time.sleep(300)

if __name__ == "__main__":
    main()
