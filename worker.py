#!/usr/bin/env python3
import os, sys, time, shutil, subprocess, requests, re
from datetime import datetime
import internetarchive as ia

AK = (os.environ.get("IA_ACCESS_KEY") or "").strip()
SK = (os.environ.get("IA_SECRET_KEY") or "").strip()

EXPECTED_USERNAME = (os.environ.get("EXPECTED_USERNAME") or "").strip().lower()

PLAYLIST_URL = (
    os.environ.get("PLAYLIST_URL")
    or "https://www.youtube.com/playlist?list=PLkbr4tX7v1Eehwa9zU55PatmrrcFufzz3"
).strip()

EP_START = int(os.environ.get("EP_START") or 1)
EP_END = int(os.environ.get("EP_END") or 128)

IDENT = (os.environ.get("IA_IDENTIFIER") or "").strip()
TITLE = (os.environ.get("IA_TITLE") or "Marcados Pelo Passado").strip()

UPLOAD_PAUSE_SECONDS = int(os.environ.get("UPLOAD_PAUSE_SECONDS") or 120)
RATE_PAUSE_SECONDS = int(os.environ.get("RATE_PAUSE_SECONDS") or 1800)
MAX_RATE_HITS = int(os.environ.get("MAX_RATE_HITS") or 3)

BURST_EVERY = int(os.environ.get("BURST_EVERY") or 10)
BURST_PAUSE_SECONDS = int(os.environ.get("BURST_PAUSE_SECONDS") or 900)

PEND = "/tmp/videos_pending"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

FMT = (
    "b[ext=mp4][height<=720]/"
    "b[ext=mp4]/"
    "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
    "b"
)

ENVIADOS = set()
FALHAS = []

def log(m):
    print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)

def sanitize(s):
    return re.sub(r'[^a-zA-Z0-9_-]', '-', (s or "").strip()).strip('-')

def garantir_ini():
    if not AK or not SK:
        log("CRITICO: IA_ACCESS_KEY / IA_SECRET_KEY vazias.")
        return False

    cfg = os.path.expanduser("~/.config/ia.ini")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)

    with open(cfg, "w", encoding="utf-8") as f:
        f.write("[s3]\n")
        f.write(f"access = {AK}\n")
        f.write(f"secret = {SK}\n")

    log("ia.ini regravado com as chaves do deploy.")
    return True

def checar_identidade():
    global IDENT

    try:
        user = ia.get_username(AK, SK)
    except Exception as e:
        log(f"CRITICO: get_username falhou: {repr(e)[:350]}")
        return None

    log(f"DONO DA CHAVE = {user!r}")

    if EXPECTED_USERNAME and user.strip().lower() != EXPECTED_USERNAME:
        log("CRITICO: a chave NÃO pertence à conta esperada.")
        log(f"Esperado: {EXPECTED_USERNAME}")
        log(f"Encontrado: {user}")
        log("Encerrando para não usar conta antiga por engano.")
        return None

    if not IDENT:
        IDENT = f"{sanitize(user)}-marcados-pelo-passado"

    log(f"IA_IDENTIFIER = {IDENT}")
    log(f"IA_TITLE = {TITLE}")
    return user

def eh_rate(texto):
    t = (texto or "").lower()
    return any(
        s in t
        for s in [
            "reduce your request rate",
            "appears to be spam",
            "rate limit",
            "too many requests",
            "http error 429",
            "429 too many",
            "503 service unavailable",
        ]
    )

def eh_denied(texto):
    t = (texto or "").lower()
    return any(
        s in t
        for s in [
            "access denied",
            "sufficient privileges",
            "usernamecanwrite",
            "invalidaccesskeyid",
            "signaturedoesnotmatch",
            "forbidden",
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
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-warnings",
        "--no-progress",
        "--newline",
        "--user-agent", UA,
        "-f", FMT,
        "--merge-output-format", "mp4",
        "-o", os.path.join(tmp, "v.%(ext)s"),
        "--no-simulate",
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
            or "ytdlp" in low
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

def upload_once(path, nome):
    statuses = []

    try:
        for resp in ia.upload(
            IDENT,
            [path],
            metadata={"title": TITLE, "mediatype": "movies"},
            verbose=False,
        ):
            sc = getattr(resp, "status_code", 0) or 0

            try:
                sc = int(sc)
            except Exception:
                sc = 0

            statuses.append(sc)
            log(f"    Archive.org HTTP {sc} {nome}")

            if sc in (429, 503):
                return "RATE"

            if sc in (401, 403):
                return "DENIED"

        if statuses and all(200 <= s < 400 for s in statuses):
            return "OK"

        return "FAIL"

    except Exception as e:
        txt = repr(e)
        log(f"    upload {nome}: {txt[:350]}")

        if eh_rate(txt):
            return "RATE"

        if eh_denied(txt):
            return "DENIED"

        return "FAIL"

def enviar_com_backoff(path, nome):
    rate_hits = 0
    fail_attempts = 0

    while True:
        res = upload_once(path, nome)

        if res == "OK":
            return True

        if res == "RATE":
            rate_hits += 1

            if rate_hits > MAX_RATE_HITS:
                log("⛔ Rate/spam atingiu o limite de tentativas.")
                log("Saindo para não prejudicar a conta nova.")
                sys.exit(5)

            log(
                f"⚠️ Rate/spam no Archive. Esperando {RATE_PAUSE_SECONDS}s "
                f"e tentando o MESMO arquivo de novo. Tentativa {rate_hits}/{MAX_RATE_HITS}"
            )
            time.sleep(RATE_PAUSE_SECONDS)
            continue

        if res == "DENIED":
            log("⛔ Acesso negado pelo Archive. Encerrando.")
            sys.exit(3)

        fail_attempts += 1

        if fail_attempts >= 3:
            return False

        log(f"⚠️ Falha comum no upload. Tentando de novo em 30s ({fail_attempts}/3).")
        time.sleep(30)

def main():
    log("WORKER v29 — YouTube -> Archive, conta nova, anti-conta-antiga, throttle anti-spam")

    if not AK or not SK:
        log("CRITICO: chaves vazias.")
        sys.exit(1)

    if not garantir_ini():
        sys.exit(1)

    user = checar_identidade()
    if not user:
        sys.exit(1)

    tunnel = (os.environ.get("TUNNEL_URL") or "").strip()
    log(f"TUNNEL_URL opcional = {tunnel or '(não definida; YouTube usa conexão direta do Akash)'}")
    log(f"PLAYLIST_URL = {PLAYLIST_URL}")
    log(f"EP_START..EP_END = {EP_START}..{EP_END}")
    log(f"UPLOAD_PAUSE_SECONDS = {UPLOAD_PAUSE_SECONDS}")
    log(f"RATE_PAUSE_SECONDS = {RATE_PAUSE_SECONDS}")
    log(f"BURST_EVERY = {BURST_EVERY}, BURST_PAUSE_SECONDS = {BURST_PAUSE_SECONDS}")

    os.makedirs(PEND, exist_ok=True)

    ENVIADOS.update(listar_existentes())

    if ENVIADOS:
        log(f"ℹ️ já no item (metadata): {len(ENVIADOS)} mp4")

    block_streak = 0
    success_count = 0

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
            free = shutil.disk_usage(PEND).free

            if free < 2_000_000_000:
                log(f"⚠️ Espaço baixo: {free // 1024 // 1024} MB livres. Esperando 5 min.")
                time.sleep(300)
                break

            nome = nome_ep(n)

            ok, titulo, sz, motivo = baixar_ep(n)

            if not ok:
                log(f"❌ [{n}] download falhou: {motivo}")
                FALHAS.append((n, motivo))

                if motivo == "block_youtube":
                    block_streak += 1

                    if block_streak >= 3:
                        log("⛔ YouTube bloqueando o IP do Akash (3 falhas seguidas). Parando.")
                        sys.exit(4)
                else:
                    block_streak = 0

                continue

            block_streak = 0

            num_titulo = numero_do_titulo(titulo, n)

            log(f"✅ baixado [{n}] {titulo!r} ({sz / 1024 / 1024:.1f} MB)")

            if num_titulo != n:
                log(f"⚠️ atenção: título diz episódio {num_titulo}, mas posição da playlist é {n}")

            path = os.path.join(PEND, nome)

            if enviar_com_backoff(path, nome):
                ENVIADOS.add(nome)

                try:
                    os.remove(path)
                except OSError:
                    pass

                success_count += 1
                log(f"✅ ENVIADO [{n}] {nome}")

                if success_count % BURST_EVERY == 0:
                    log(f"⏸ Pausa anti-spam após {success_count} uploads: {BURST_PAUSE_SECONDS}s")
                    time.sleep(BURST_PAUSE_SECONDS)
                else:
                    time.sleep(UPLOAD_PAUSE_SECONDS)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass

                FALHAS.append((n, "upload"))
                log(f"❌ FALHOU upload [{n}] {nome}")

        log("⏸ Varredura completa; espera 5 min e repete só as não enviadas.")
        time.sleep(300)

if __name__ == "__main__":
    main()
