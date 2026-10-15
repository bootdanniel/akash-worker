#!/usr/bin/env python3
import os, sys, time, shutil, requests
from datetime import datetime
import internetarchive as ia

IDENT = (
    os.environ.get("IA_IDENTIFIER")
    or "michaeline1dc92f-s8-pointnexus-org-la-casa-de-papel-24h-parte5-final-2"
).strip()

TITLE = (os.environ.get("IA_TITLE") or "lá casa papel.. parte 5 final").strip()
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
FATAL_WRITE = False

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

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
    return (
        "access denied" in t
        or "sufficient privileges" in t
        or "usernamecanwrite" in t
        or "invalidaccesskeyid" in t
        or "signaturedoesnotmatch" in t
    )

def pedir_link(tunnel, t, e):
    url = f"{tunnel.rstrip('/')}/resolver/{TMDB}/{t}/{e}"

    for i in range(1, 4):
        try:
            d = requests.get(url, timeout=120).json()
            if d.get("mp4"):
                return d["mp4"]
            log(f"    ponte sem link {i}: {d}")
        except Exception as ex:
            log(f"    erro ponte {i}: {repr(ex)[:250]}")

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

def upload_arquivo(path, nome_log):
    global FATAL_WRITE

    ultimo = ""

    for tent in range(1, 4):
        try:
            statuses = []

            for resp in ia.upload(
                IDENT,
                [path],
                metadata={"title": TITLE, "mediatype": "movies"},
                queue_derive=False,
                verbose=False
            ):
                sc = getattr(resp, "status_code", 0) or 0
                statuses.append(sc)
                log(f"    Archive.org HTTP {sc} {nome_log}")

            if statuses and all(200 <= sc < 400 for sc in statuses):
                return True

            if any(sc in (401, 403) for sc in statuses):
                FATAL_WRITE = True
                ultimo = f"status={statuses}"
                log("    ❌ 401/403 do Archive.org. Parando para não gastar download.")
                return False

            ultimo = f"status={statuses}"

        except Exception as e:
            ultimo = repr(e)
            log(f"    upload {nome_log} tent {tent}/3: {ultimo[:350]}")

            if eh_acesso_negado(ultimo):
                FATAL_WRITE = True
                log("    ❌ Access Denied. Parando para não baixar os próximos à toa.")
                return False

        time.sleep(15 * tent)

    log(f"    ❌ {nome_log} falhou definitivamente: {ultimo[:450]}")
    return False

def teste_escrita_minima():
    global FATAL_WRITE

    os.makedirs(PEND, exist_ok=True)
    path = os.path.join(PEND, "_write_ok.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write("write test ok\n")

    log(f"Testando escrita mínima no item: {IDENT}")

    ok = upload_arquivo(path, "_write_ok.txt")

    if ok:
        log("✅ Escrita mínima OK. O item aceita upload. Não vou apagar _write_ok.txt.")
        return True

    if FATAL_WRITE:
        log("CRITICO: item novo também recusou escrita. Provável bloqueio temporário da conta/chave ou item queimado.")
        return False

    log("CRITICO: teste de escrita mínima falhou sem Access Denied explícito.")
    return False

def main():
    log("WORKER v26 — La Casa 5 finais, item novo, teste de escrita sem apagar, para em Access Denied")

    tunnel = (os.environ.get("TUNNEL_URL") or "").strip().rstrip("/")

    if not tunnel:
        log("CRITICO: TUNNEL_URL vazia.")
        sys.exit(1)

    log(f"TUNNEL_URL = {tunnel}")
    log(f"IA_IDENTIFIER = {IDENT}")
    log(f"IA_TITLE = {TITLE}")

    if not garantir_ini():
        sys.exit(1)

    if not teste_escrita_minima():
        log("⛔ Não vou baixar episódios porque a escrita mínima falhou.")
        log("Feche este deploy e espere um pouco, ou use outra chave/conta/item.")
        sys.exit(2)

    os.makedirs(PEND, exist_ok=True)

    while True:
        pend = [(n, t, e) for n, (t, e) in MAPA.items() if n not in ENVIADOS]

        if not pend:
            log("✅ 5/5 enviados. Pode fechar o deploy do Akash.")
            time.sleep(86400)

        log(f"📋 Faltam {len(pend)} episódios neste container.")

        for nome, t, e in pend:
            if FATAL_WRITE:
                log("⛔ Parando ciclo por erro de escrita.")
                sys.exit(3)

            free = shutil.disk_usage(PEND).free

            if free < 2_000_000_000:
                log(f"⚠️ Espaço baixo: {free // 1024 // 1024} MB livres. Esperando 5 min.")
                time.sleep(300)
                break

            log(f"── {nome} ← fonte T{t:02d}E{e:02d} ──")

            link = pedir_link(tunnel, t, e)

            if not link:
                log("❌ Sem link, pulando por enquanto.")
                continue

            destino = os.path.join(PEND, nome)

            try:
                sz = baixar(link, destino)
                log(f"✅ baixado {nome} ({sz / 1024 / 1024:.1f} MB)")
            except Exception as ex:
                log(f"❌ falha baixando {nome}: {repr(ex)[:350]}")

                for sx in ("", ".part"):
                    p = destino + sx
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass

                continue

            if upload_arquivo(destino, nome):
                ENVIADOS.add(nome)

                try:
                    os.remove(destino)
                except OSError:
                    pass

                log(f"✅ ENVIADO {nome}")
                time.sleep(10)
            else:
                try:
                    os.remove(destino)
                except OSError:
                    pass

                log(f"❌ FALHOU upload de {nome}")

                if FATAL_WRITE:
                    log("⛔ Access Denied detectado. Encerrando para não repetir downloads.")
                    sys.exit(3)

        log("⏸ Passou pelos pendentes. Esperando 5 min antes de repetir só os não enviados.")
        time.sleep(300)

if __name__ == "__main__":
    main()
