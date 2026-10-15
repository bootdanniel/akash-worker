#!/usr/bin/env python3
import os, sys, time, shutil, subprocess, requests, re
from datetime import datetime
import internetarchive as ia

AK = (os.environ.get("IA_ACCESS_KEY") or "").strip()
SK = (os.environ.get("IA_SECRET_KEY") or "").strip()
EXPECTED_USERNAME = (os.environ.get("EXPECTED_USERNAME") or "").strip().lower()
TUNNEL = (os.environ.get("TUNNEL_URL") or "").strip().rstrip("/")

PLAYLIST_URL = (os.environ.get("PLAYLIST_URL")
    or "https://www.youtube.com/playlist?list=PLkbr4tX7v1Eehwa9zU55PatmrrcFufzz3").strip()

EP_START = int(os.environ.get("EP_START") or 1)
EP_END = int(os.environ.get("EP_END") or 128)
IDENT = (os.environ.get("IA_IDENTIFIER") or "").strip()
TITLE = (os.environ.get("IA_TITLE") or "Marcados Pelo Passado").strip()

UPLOAD_PAUSE_SECONDS = int(os.environ.get("UPLOAD_PAUSE_SECONDS") or 180)
RATE_PAUSE_SECONDS = int(os.environ.get("RATE_PAUSE_SECONDS") or 900)
MAX_RATE_HITS = int(os.environ.get("MAX_RATE_HITS") or 5)
BURST_EVERY = int(os.environ.get("BURST_EVERY") or 10)
BURST_PAUSE_SECONDS = int(os.environ.get("BURST_PAUSE_SECONDS") or 1800)

PEND = "/tmp/videos_pending"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ENVIADOS = set()
FALHAS = []

def log(m): print(f"[worker]: {datetime.now().strftime('%H:%M:%S')} {m}", flush=True)
def sanitize(s): return re.sub(r'[^a-zA-Z0-9_-]', '-', (s or "").strip()).strip('-')

def garantir_ini():
    if not AK or not SK:
        log("CRITICO: chaves vazias."); return False
    cfg = os.path.expanduser("~/.config/ia.ini")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    open(cfg, "w").write(f"[s3]\naccess = {AK}\nsecret = {SK}\n")
    log("ia.ini regravado com as chaves do deploy."); return True

def checar_identidade():
    global IDENT
    try:
        user = ia.get_username(AK, SK)
    except Exception as e:
        log(f"CRITICO: get_username falhou: {repr(e)[:350]}"); return None
    log(f"DONO DA CHAVE = {user!r}")
    if EXPECTED_USERNAME and user.strip().lower() != EXPECTED_USERNAME:
        log("CRITICO: chave NÃO pertence à conta esperada."); log(f"Esperado {EXPECTED_USERNAME} / Achado {user}")
        log("Encerrando (anti-conta-antiga)."); return None
    if not IDENT:
        IDENT = f"{sanitize(user)}-marcados-pelo-passado"
    log(f"IA_IDENTIFIER = {IDENT}"); log(f"IA_TITLE = {TITLE}"); return user

def eh_rate(t):
    t=(t or "").lower(); return any(s in t for s in ["reduce your request rate","appears to be spam","rate limit","too many requests","http error 429","429 too many","503 service unavailable","total_tasks_queued exceeds global_limit","global_limit"])
def eh_denied(t):
    t=(t or "").lower(); return any(s in t for s in ["access denied","sufficient privileges","usernamecanwrite","invalidaccesskeyid","signaturedoesnotmatch","forbidden"])
def eh_block_yt(t):
    t=(t or "").lower(); return any(s in t for s in ["not a bot","sign in to confirm","captcha","http error 403","403 forbidden","precondition failed","please sign in"])

def hdrs():
    return {"User-Agent": UA, "Referer": "https://www.youtube.com/", "Origin": "https://www.youtube.com", "Accept": "*/*"}

def listar_existentes():
    try:
        r = requests.get(f"https://archive.org/metadata/{IDENT}", timeout=30)
        if r.status_code != 200: return set()
        return {f.get("name") for f in (r.json().get("files") or []) if str(f.get("name","")).lower().endswith(".mp4")}
    except Exception as e:
        log(f"⚠️ listar_existentes: {repr(e)[:200]}"); return set()

def nome_ep(n): return f"Marcados_Pelo_Passado_E{n:03d}.mp4"

def pedir_links(n):
    url = f"{TUNNEL}/yt/{n}"
    for i in range(1,4):
        try:
            r = requests.get(url, timeout=240)
            log(f"    resolvedor HTTP {r.status_code} pos {n}")
            if r.status_code == 200:
                d = r.json()
                if d.get("mp4_url") or d.get("video_url") or d.get("audio_url"):
                    return d
                if eh_block_yt(str(d)):
                    return {"_block": True, "msg": str(d)[:300]}
                log(f"    resolvedor sem url: {str(d)[:200]}")
            else:
                if eh_block_yt(r.text): return {"_block": True, "msg": r.text[:300]}
                log(f"    resolvedor erro {r.status_code}: {r.text[:200]}")
        except Exception as ex:
            log(f"    erro resolvedor {i}: {repr(ex)[:200]}")
        time.sleep(20)
    return None

def probe_ip(d):
    """Baixa 1 MB da 1a url disponivel. OK / IP_BOUND / ERR."""
    u = d.get("mp4_url") or d.get("audio_url") or d.get("video_url")
    if not u: return "ERR", "sem url p/ probe"
    h = hdrs(); h["Range"] = "bytes=0-1048575"
    try:
        r = requests.get(u, headers=h, stream=True, timeout=(30,120))
        sc = r.status_code; r.close()
        if sc in (200, 206): return "OK", f"probe HTTP {sc}"
        if sc in (401, 403): return "IP_BOUND", f"probe HTTP {sc} (link amarrado ao IP do celular)"
        return "ERR", f"probe HTTP {sc}"
    except Exception as e:
        return "ERR", repr(e)[:200]

def download_url(url, destino):
    r = requests.get(url, headers=hdrs(), stream=True, timeout=(30,900))
    if r.status_code in (401,403): raise RuntimeError(f"http_error_{r.status_code}")
    r.raise_for_status()
    total=int(r.headers.get("content-length",0)); b=0; u=0; part=destino+".part"
    with open(part,"wb") as f:
        for c in r.iter_content(4*1024*1024):
            if c:
                f.write(c); b+=len(c)
                if total:
                    p=int(b*100/total)
                    if p>=u+10: print(f"\r[worker]:     {p}% ({b//1024//1024}/{total//1024//1024} MB)",end="",flush=True); u=p
    print("",flush=True); os.replace(part,destino); return b

def run_cmd(cmd, timeout=2400):
    try:
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
        return r.returncode,(r.stdout or "").strip()
    except Exception as e:
        return 999,repr(e)[:300]

def ffmpeg_video(url, out):
    head=f"Referer: https://www.youtube.com/\r\nUser-Agent: {UA}\r\nOrigin: https://www.youtube.com\r\n"
    cmd=["ffmpeg","-y","-headers",head,"-i",url,"-c","copy","-movflags","+faststart",out]
    rc,out2=run_cmd(cmd)
    if rc==0 and os.path.exists(out) and os.path.getsize(out)>1024*1024: return True,out2
    log("    ffmpeg video copy falhou, transcode leve...")
    cmd2=["ffmpeg","-y","-headers",head,"-i",url,"-c:v","libx264","-preset","veryfast","-crf","23","-an","-movflags","+faststart",out]
    rc2,o2=run_cmd(cmd2,timeout=3600)
    return (rc2==0 and os.path.exists(out) and os.path.getsize(out)>1024*1024),(o2 or out2)

def ffmpeg_merge(v,a,out):
    cmd=["ffmpeg","-y","-i",v,"-i",a,"-c","copy","-movflags","+faststart",out]
    rc,o=run_cmd(cmd,timeout=2400)
    if rc==0 and os.path.exists(out) and os.path.getsize(out)>1024*1024: return True,o
    cmd2=["ffmpeg","-y","-i",v,"-i",a,"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",out]
    rc2,o2=run_cmd(cmd2,timeout=3600)
    return (rc2==0 and os.path.exists(out) and os.path.getsize(out)>1024*1024),(o2 or o)

def baixar_ep(n):
    tmp=os.path.join(PEND,f"_dl_{n}"); shutil.rmtree(tmp,ignore_errors=True); os.makedirs(tmp,exist_ok=True)
    d=pedir_links(n)
    if not d:
        shutil.rmtree(tmp,ignore_errors=True); return False,"",0,"sem_link_resolvedor"
    if d.get("_block"):
        shutil.rmtree(tmp,ignore_errors=True); return False,"",0,"block_youtube"
    titulo=d.get("title") or ""; modo=d.get("modo") or ""
    destino=os.path.join(PEND,nome_ep(n))

    st,det=probe_ip(d)
    log(f"    probe IP: {st} - {det}")
    if st=="IP_BOUND":
        shutil.rmtree(tmp,ignore_errors=True)
        return False,titulo,0,"IP_BOUND: o YouTube amarrou o link ao IP do celular; o Akash nao consegue baixar. Use o modo celular (1 por vez, apaga na hora)."
    if st=="ERR":
        shutil.rmtree(tmp,ignore_errors=True); return False,titulo,0,"probe_err: "+det

    try:
        if d.get("mp4_url"):
            log("    modo progressivo, baixando mp4 direto")
            sz=download_url(d["mp4_url"],destino); shutil.rmtree(tmp,ignore_errors=True); return True,titulo,sz,""

        if d.get("video_url") and d.get("audio_url"):
            vpath=os.path.join(tmp,"v.mp4"); apath=os.path.join(tmp,"a.m4a")
            log("    baixando audio (GET)")
            download_url(d["audio_url"],apath)
            log("    baixando video (ffmpeg, suporta HLS/direto)")
            okv,msgv=ffmpeg_video(d["video_url"],vpath)
            if not okv: raise RuntimeError("ffmpeg_video: "+(msgv.splitlines()[-1][:200] if msgv.strip() else ""))
            log("    juntando video+audio (ffmpeg)")
            okm,msgm=ffmpeg_merge(vpath,apath,destino)
            if not okm: raise RuntimeError("ffmpeg_merge: "+(msgm.splitlines()[-1][:200] if msgm.strip() else ""))
            shutil.rmtree(tmp,ignore_errors=True); return True,titulo,os.path.getsize(destino),""

        if d.get("video_url"):
            log("    modo somente_video, baixando via ffmpeg")
            okv,msgv=ffmpeg_video(d["video_url"],destino)
            if not okv: raise RuntimeError("ffmpeg_video: "+(msgv.splitlines()[-1][:200] if msgv.strip() else ""))
            shutil.rmtree(tmp,ignore_errors=True); return True,titulo,os.path.getsize(destino),""

        raise RuntimeError("resolvedor sem url utilizavel")
    except Exception as ex:
        txt=repr(ex); shutil.rmtree(tmp,ignore_errors=True)
        try:
            if os.path.exists(destino): os.remove(destino)
        except OSError: pass
        if eh_block_yt(txt): return False,titulo,0,"block_youtube"
        if "http_error_403" in txt or "http_error_401" in txt:
            return False,titulo,0,"IP_BOUND: download direto tomou 403 (link amarrado ao IP do celular)."
        return False,titulo,0,txt[:300]

def upload_once(path,nome):
    statuses=[]
    try:
        for resp in ia.upload(IDENT,[path],metadata={"title":TITLE,"mediatype":"movies"},
                              access_key=AK,secret_key=SK,queue_derive=False,verbose=False):
            sc=getattr(resp,"status_code",0) or 0
            try: sc=int(sc)
            except: sc=0
            statuses.append(sc); log(f"    Archive.org HTTP {sc} {nome}")
            if sc in (429,503): return "RATE"
            if sc in (401,403): return "DENIED"
        if statuses and all(200<=s<400 for s in statuses): return "OK"
        return "FAIL"
    except Exception as e:
        txt=repr(e); log(f"    upload {nome}: {txt[:350]}")
        if eh_rate(txt): return "RATE"
        if eh_denied(txt): return "DENIED"
        return "FAIL"

def enviar_com_backoff(path,nome):
    rate=0; fail=0
    while True:
        res=upload_once(path,nome)
        if res=="OK": return True
        if res=="RATE":
            rate+=1
            if rate>MAX_RATE_HITS:
                log("⛔ Rate/global_limit excedeu limite. Saindo p/ proteger a conta."); sys.exit(5)
            log(f"⚠️ Archive pediu reducao de fila. Esperando {RATE_PAUSE_SECONDS}s e repetindo MESMO arquivo ({rate}/{MAX_RATE_HITS}).")
            time.sleep(RATE_PAUSE_SECONDS); continue
        if res=="DENIED":
            log("⛔ Acesso negado pelo Archive. Encerrando."); sys.exit(3)
        fail+=1
        if fail>=3: return False
        log(f"⚠️ Falha comum no upload, tentando de novo em 30s ({fail}/3)."); time.sleep(30)

def main():
    log("WORKER v33 — Akash baixa via resolvedor do celular + probe de IP + queue_derive=False")
    if not AK or not SK: log("CRITICO: chaves vazias."); sys.exit(1)
    if not TUNNEL: log("CRITICO: TUNNEL_URL vazia (precisa do tunel do resolvedor)."); sys.exit(1)
    if not garantir_ini(): sys.exit(1)
    if not checar_identidade(): sys.exit(1)
    log(f"TUNNEL_URL = {TUNNEL}"); log(f"PLAYLIST_URL = {PLAYLIST_URL}")
    log(f"EP_START..EP_END = {EP_START}..{EP_END}")
    os.makedirs(PEND,exist_ok=True)
    ENVIADOS.update(listar_existentes())
    if ENVIADOS: log(f"ℹ️ ja no item (metadata): {len(ENVIADOS)} mp4")
    block=0; succ=0; ipbound_streak=0
    while True:
        faltam=[n for n in range(EP_START,EP_END+1) if nome_ep(n) not in ENVIADOS]
        if not faltam:
            log(f"✅ Completo {EP_END-EP_START+1}/{EP_END-EP_START+1}. Pode fechar o deploy."); time.sleep(86400)
        log(f"📋 Faltam {len(faltam)} posicoes.")
        for n in faltam:
            free=shutil.disk_usage(PEND).free
            if free<2_000_000_000:
                log(f"⚠️ Espaco baixo ({free//1024//1024} MB). Esperando 5 min."); time.sleep(300); break
            nome=nome_ep(n)
            ok,titulo,sz,motivo=baixar_ep(n)
            if not ok:
                log(f"❌ [{n}] falhou: {motivo}"); FALHAS.append((n,motivo))
                if motivo.startswith("IP_BOUND"):
                    ipbound_streak+=1
                    if ipbound_streak>=1:
                        log("⛔ IP_BOUND confirmado: o Akash NAO consegue baixar os links do YouTube (estao amarrados ao IP do celular).")
                        log("➡️ Proximo passo: modo celular (baixa 1, sobe 1, apaga 1). Me avise que eu monto.")
                        sys.exit(7)
                elif motivo=="block_youtube":
                    block+=1
                    if block>=3: log("⛔ YouTube bloqueando o resolvedor (3 seguidas). Parando."); sys.exit(4)
                else:
                    block=0
                continue
            block=0; ipbound_streak=0
            log(f"✅ baixado [{n}] {titulo!r} ({sz/1024/1024:.1f} MB)")
            path=os.path.join(PEND,nome)
            if enviar_com_backoff(path,nome):
                ENVIADOS.add(nome)
                try: os.remove(path)
                except OSError: pass
                succ+=1; log(f"✅ ENVIADO [{n}] {nome}")
                if succ%BURST_EVERY==0:
                    log(f"⏸ Pausa anti-spam apos {succ} uploads: {BURST_PAUSE_SECONDS}s"); time.sleep(BURST_PAUSE_SECONDS)
                else:
                    time.sleep(UPLOAD_PAUSE_SECONDS)
            else:
                try: os.remove(path)
                except OSError: pass
                FALHAS.append((n,"upload")); log(f"❌ FALHOU upload [{n}] {nome}")
        log("⏸ Varredura completa; espera 5 min e repete so as nao enviadas."); time.sleep(300)

if __name__=="__main__":
    main()
