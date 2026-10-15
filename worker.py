#!/usr/bin/env python3
import os, sys, time, shutil, subprocess, requests, re
from datetime import datetime
import internetarchive as ia

PLAYLIST_URL = (
    os.environ.get("PLAYLIST_URL")
    or "https://www.youtube.com/playlist?list=PLkbr4tX7v1Eehwa9zU55PatmrrcFufzz3"
).strip()

EP_START = int(os.environ.get("EP_START") or 1)
EP_END = int(os.environ.get("EP_END") or 128)

IDENT = (
    os.environ.get("IA_IDENTIFIER")
    or "michaeline1dc92f-s8-pointnexus-org-marcados-pelo-passado"
).strip()

TITLE = (os.environ.get("IA_TITLE") or "Marcados Pelo Passado").strip()

YTDLP = "yt-dlp"
FMT = "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*[height<=720]+ba/b"
PEND = "/tmp/videos_pending"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

ENVIADOS = set()
FALHAS = []
FATAL_WRITE = False
block_streak = 0

def log(m):
    print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def garantir_ini():
    ak = (os.environ.get("IA_ACCESS_KEY") or "").strip()
    sk = (os.environ.get("IA_SECRET_KEY") or "").strip()

    if not ak or not sk:
        log("CRITICO: IA_ACCESS_KEY / IA_SECRET_KEY vazias.")
        return False

    cfg = os.path.expanduser("~/.config/ia.ini")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)

    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[s3]\n")
        f.write(f"access = {ak}\n")
        f.write(f"secret = {sk}\n")

    log("ia.ini gravado.")
    return True

def eh_acesso_negado(texto):
    t = (texto or "").lower()
    return any(
        s in t
        for s in [
            "access denied",
            "sufficient privileges",
            "usernamecanwrite",
            "invalidaccesskeyid",
            "signaturedoesnotmatch",
        ]
    )

def eh_block_yt(texto):
    t = (texto or "").lower()
    return any(
        s in t
        for s in [
            "not a bot",
            "sign in to confirm",
            "captcha",
            "http error 403",
            "precondition failed",
            "please sign in",
        ]
    )

def stream_run(cmd):
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    buf = []

    for line in p.stdout:
        s = line.rstrip("\n")
        if s:
            log("    ytdlp| " + s[:240])
            buf.append(s)

    p.wait()
    return p.returncode, "\n".join(buf)

def listar_existentes():
    try:
        r = requests.get(f"https://archive.org/metadata/{IDENT}", timeout=30)

        if r.status_code != 200:
            return set()

        files = r.json().get("files") or []

        return {
            f.get("name")
            for f in files
            if str(f.get("name", "")).lower().endswith(".mp4")
        }

    except Exception as e:
        log(f"⚠️ listar_existentes: {repr(e)[:220]}")
        return set()

def nome_ep(n):
    return f"Marcados_Pelo_Passado_E{n:03d}.mp4"

def numero_do_titulo(titulo, fallback):
    if not titulo:
        return fallback

    t = titulo.lower()

    m = re.search(r"epis[oó]dio[^0-9]{0,8}([0-9]{1,3})", t)
    if m:
        return int(m.group(1))

    m = re.search(r"\b([0-9]{1,3})\b", t)
    if m:
        return int(m.group(1))

    return fallback

def baixar_ep(n):
    tmp = os.path.join(PEND, f"_dl_{n}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)

    base = [
        YTDLP,
        "--no-warnings",
        "--no-progress",
        "--newline",
        "-f", FMT,
        "--merge-output-format", "mp4",
        "-o", os.path.join(tmp, "v.%(ext)s"),
        "--download",
        "--print", "%(title)s",
        "--retries", "5",
        "--fragment-retries", "5",
        "--socket-timeout", "30",
        "--playlist-items", str(n),
        PLAYLIST_URL,
    ]

    rc, out = stream_run(base)

    if rc != 0 and eh_block_yt(out):
        log("    retry com player_client alternativo...")
        rc, out = stream_run(
            base + ["--extractor-args", "youtube:player_client=tv,android"]
        )

    titulo = ""

    for l in out.splitlines():
        ls = l.strip()

        if not ls:
            continue

        low = ls.lower()

        if (
            ls.startswith("[")
            or "error" in low
            or "warning" in low
            or "yt-dlp" in low
        ):
            continue

        titulo = ls
        break

    arq = None

    if os.path.isdir(tmp):
        mp4s = [f for f in os.listdir(tmp) if f.lower().endswith(".mp4")]

        if mp4s:
            arq = os.path.join(tmp, sorted(mp4s)[0])
        else:
            fs = os.listdir(tmp)
            if fs:
                arq = os.path.join(tmp, sorted(fs)[0])

    if rc != 0 or arq is None:
        shutil.rmtree(tmp, ignore_errors=True)

        if eh_block_yt(out):
            motivo = "block_youtube"
        elif out.strip():
            motivo = out.strip().splitlines()[-1][:220]
        else:
            motivo = "sem arquivo"

        return False, titulo, 0, motivo

    destino = os.path.join(PEND, nome_ep(n))
    os.replace(arq, destino)
    shutil.rmtree(tmp, ignore_errors=True)

    return True, titulo, os.path.getsize(destino), ""

def upload_arquivo(path, nome_log):
    global FATAL_WRITE

    ultimo = ""

    for usar_derive in (True, False):
        try:
            kw = {
                "metadata": {"title": TITLE, "mediatype": "movies"},
                "verbose": False,
            }

            if not usar_derive:
                kw["queue_derive"] = False

            statuses = []

            for resp in ia.upload(IDENT, [path], **kw):
                sc = getattr(resp, "status_code", 0) or 0
                statuses.append(sc)
                log(f"    Archive.org HTTP {sc} {nome_log}")

            if statuses and all(200 <= sc < 400 for sc in statuses):
                return True

            ultimo = f"status={statuses}"

            if any(sc in (401, 403) for sc in statuses):
                continue

        except Exception as e:
            ultimo = repr(e)
            log(f"    upload {nome_log} modo_derive={usar_derive}: {ultimo[:350]}")

            if eh_acesso_negado(ultimo):
                continue

    if eh_acesso_negado(ultimo):
        FATAL_WRITE = True
        log("    ❌ Access Denied. Parando para não gastar download à toa.")

    return False

def teste_escrita_minima():
    os.makedirs(PEND, exist_ok=True)

    path = os.path.join(PEND, "_write_ok.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write("write test ok\n")

    log(f"Testando escrita mínima no item: {IDENT}")

    ok = upload_arquivo(path, "_write_ok.txt")

    if ok:
        log("✅ Escrita mínima OK. Não vou apagar _write_ok.txt.")
        return True

    if FATAL_WRITE:
        log("CRITICO: item recusou escrita. Provável bloqueio temporário da conta/chave.")
    else:
        log("CRITICO: teste de escrita mínima falhou.")

    return False

def main():
    global block_streak

    log(
        f"WORKER v28 — YouTube playlist -> Archive | item={IDENT} | "
        f"eps {EP_START}..{EP_END}"
    )

    tunnel = (os.environ.get("TUNNEL_URL") or "").strip()
    log(f"TUNNEL_URL (ponte) = {tunnel or '(não usada para YouTube; yt-dlp vai direto)'}")

    if not garantir_ini():
        sys.exit(1)

    os.makedirs(PEND, exist_ok=True)

    if not teste_escrita_minima():
        log("⛔ Não vou baixar episódios porque a escrita mínima falhou.")
        log("Feche este deploy e espere um pouco, ou use outra chave/conta/item.")
        sys.exit(2)

    ENVIADOS.update(listar_existentes())

    if ENVIADOS:
        log(f"ℹ️ já no item (metadata): {len(ENVIADOS)} mp4")

    while True:
        faltam = [
            n
            for n in range(EP_START, EP_END + 1)
            if nome_ep(n) not in ENVIADOS
        ]

        if not faltam:
            log(f"✅ Completo {EP_END - EP_START + 1}/{EP_END - EP_START + 1}. Pode fechar o deploy.")
            time.sleep(86400)

        log(f"📋 Faltam {len(faltam)} posições.")

        for n in faltam:
            if FATAL_WRITE:
                log("⛔ Parando ciclo por erro de escrita.")
                sys.exit(3)

            nome = nome_ep(n)

            free = shutil.disk_usage(PEND).free

            if free < 2_000_000_000:
                log(f"⚠️ Espaço baixo: {free // 1024 // 1024} MB livres. Esperando 5 min.")
                time.sleep(300)
                break

            ok, titulo, sz, motivo = baixar_ep(n)

            if not ok:
                log(f"❌ [{n}] download falhou: {motivo}")
                FALHAS.append((n, motivo))

                if motivo == "block_youtube":
                    block_streak += 1

                    if block_streak >= 4:
                        log("⛔ YouTube bloqueando o IP (4 falhas seguidas de bot/403). Parando.")
                        sys.exit(4)
                else:
                    block_streak = 0

                continue

            block_streak = 0

            num_titulo = numero_do_titulo(titulo, n)

            log(f"✅ baixado [{n}] {titulo!r} ({sz / 1024 / 1024:.1f} MB)")

            if num_titulo != n:
                log(
                    f"⚠️ atenção: título diz episódio {num_titulo}, "
                    f"mas posição da playlist é {n}"
                )

            path = os.path.join(PEND, nome)

            if upload_arquivo(path, nome):
                ENVIADOS.add(nome)

                try:
                    os.remove(path)
                except OSError:
                    pass

                log(f"✅ ENVIADO [{n}] {nome}")
                time.sleep(10)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass

                FALHAS.append((n, "upload"))

                if FATAL_WRITE:
                    log("⛔ Access Denied detectado. Encerrando para não repetir downloads.")
                    sys.exit(3)

        log("⏸ varredura completa; espera 5 min e repete só as não enviadas.")
        time.sleep(300)

if __name__ == "__main__":
    main()
