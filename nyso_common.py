"""Utilidades compartidas por los scripts de Nyso Coach.

- Fija la zona horaria a America/Santiago (GitHub Actions corre en UTC).
- Cliente de Intervals.icu, Supabase y Telegram.
- Helpers tolerantes a los nombres de campos de la API de Intervals.
- Umbrales actuales leídos desde la configuración de deportes de Intervals.
- Próxima carrera leída desde el calendario de Intervals.
"""
import os
import re
import time
import base64
from datetime import datetime, timedelta

import requests

# === ZONA HORARIA ===
# Todo datetime.now() en los scripts pasa a ser hora de Santiago (cambia sola invierno/verano).
os.environ["TZ"] = "America/Santiago"
try:
    time.tzset()
except AttributeError:  # Windows
    pass

INTERVALS_ATHLETE_ID = os.environ["INTERVALS_ATHLETE_ID"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

TIMEOUT = 30


def ahora():
    return datetime.now()


def hoy_str():
    return ahora().strftime("%Y-%m-%d")


def es_ejecucion_programada():
    """True si el workflow lo disparó el cron (no un 'Run workflow' manual)."""
    return os.environ.get("GITHUB_EVENT_NAME") == "schedule"


# === INTERVALS.ICU ===

def _intervals_headers():
    credentials = base64.b64encode(f"API_KEY:{INTERVALS_API_KEY}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


def intervals_get(endpoint=""):
    url = f"https://intervals.icu/api/v1/athlete/{INTERVALS_ATHLETE_ID}"
    if endpoint:
        url += f"/{endpoint}"
    response = requests.get(url, headers=_intervals_headers(), timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _rango(dias_atras=0, dias_adelante=0):
    hoy = ahora()
    oldest = (hoy - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
    newest = (hoy + timedelta(days=dias_adelante)).strftime("%Y-%m-%d")
    return oldest, newest


def get_wellness(days=14):
    oldest, newest = _rango(dias_atras=days)
    data = intervals_get(f"wellness?oldest={oldest}&newest={newest}")
    return sorted(data, key=lambda w: w.get("id", ""))


def get_activities(days=7):
    oldest, newest = _rango(dias_atras=days)
    data = intervals_get(f"activities?oldest={oldest}&newest={newest}")
    return sorted(data, key=lambda a: a.get("start_date_local", ""))


def get_events(days=60):
    oldest, newest = _rango(dias_adelante=days)
    try:
        return intervals_get(f"events?oldest={oldest}&newest={newest}")
    except Exception:
        return []


# === CAMPOS (tolerantes a nombres distintos de la API) ===

def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def carga(a):
    """Training load / TSS de una actividad."""
    for k in ("icu_training_load", "training_load", "tss"):
        v = _num(a.get(k))
        if v is not None:
            return v
    return 0.0


def fc_media(a):
    for k in ("average_heartrate", "icu_average_hr", "average_hr"):
        v = _num(a.get(k))
        if v:
            return v
    return 0.0


def fc_reposo(w):
    for k in ("restingHR", "restingHeartRate", "resting_hr"):
        v = w.get(k)
        if v:
            return v
    return None


def horas_sueno(w):
    if w.get("sleepSecs"):
        return float(w["sleepSecs"]) / 3600
    if w.get("sleepHours"):
        try:
            return float(w["sleepHours"])
        except (TypeError, ValueError):
            pass
    return None


def tsb(w):
    """Intervals no siempre manda TSB en wellness: Form = CTL - ATL."""
    v = _num(w.get("tsb"))
    if v is not None:
        return round(v, 1)
    ctl, atl = _num(w.get("ctl")), _num(w.get("atl"))
    if ctl is not None and atl is not None:
        return round(ctl - atl, 1)
    return None


_RESP_RE = re.compile(r"(?:respiraci[oó]n|respiratory|resp(?:iration)?|rr)\D{0,25}(\d{1,2}(?:[.,]\d)?)", re.I)


def respiracion(w):
    """El campo nativo 'respiration' viene null: se lee desde 'comments' con regex."""
    v = _num(w.get("respiration"))
    if v:
        return v
    m = _RESP_RE.search(w.get("comments") or "")
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def spo2(w):
    for k in ("spO2", "spo2"):
        v = _num(w.get(k))
        if v:
            return v
    return None


def actividad_es_hoy(a, fecha_str):
    return (a.get("start_date_local") or "").startswith(fecha_str)


# === UMBRALES ACTUALES (desde Intervals) ===

def _pace_ms_a_minkm(ms):
    try:
        ms = float(ms)
        if ms <= 0:
            return None
        s = 1000 / ms
        return f"{int(s // 60)}:{int(round(s % 60)):02d}"
    except (TypeError, ValueError):
        return None


def get_umbrales():
    """Lee FTP, LTHR, ritmo umbral y zonas de FC por deporte desde Intervals.

    Retorna {"run": {...}, "bike": {...}, "swim": {...}} con claves
    ftp, lthr, threshold_pace, hr_zones (lista de topes de zona en bpm).
    Si falla, retorna {} y los scripts usan valores de respaldo.
    """
    try:
        athlete = intervals_get("")
    except Exception as e:
        print(f"   ⚠ No pude leer umbrales de Intervals: {e}")
        return {}

    umbrales = {}
    for s in athlete.get("sportSettings", []) or []:
        tipos = [t.lower() for t in (s.get("types") or [])]
        if any("run" in t for t in tipos):
            deporte = "run"
        elif any("ride" in t for t in tipos):
            deporte = "bike"
        elif any("swim" in t for t in tipos):
            deporte = "swim"
        else:
            continue
        if deporte in umbrales:
            continue
        umbrales[deporte] = {
            "ftp": s.get("ftp"),
            "lthr": s.get("lthr"),
            "max_hr": s.get("max_hr"),
            "threshold_pace": _pace_ms_a_minkm(s.get("threshold_pace")) if deporte == "run" else s.get("threshold_pace"),
            "hr_zones": s.get("hr_zones") or [],
        }
    return umbrales


def clasificar_deporte(tipo):
    t = (tipo or "").lower()
    if "swim" in t or "pool" in t:
        return "swim"
    if any(k in t for k in ("ride", "bike", "cycling", "velo", "gravel", "mtb")):
        return "bike"
    if any(k in t for k in ("run", "trail", "treadmill")):
        return "run"
    return "otro"


def zona_fc(fc, deporte, umbrales):
    """Zona 1-5 según las zonas de FC del deporte en Intervals (respaldo: % de LTHR)."""
    cfg = umbrales.get(deporte) or umbrales.get("run") or {}
    zonas = cfg.get("hr_zones") or []
    if len(zonas) >= 4:
        for i, tope in enumerate(zonas[:4]):
            if fc <= tope:
                return i + 1
        return 5
    lthr = cfg.get("lthr") or 158
    pct = fc / lthr
    if pct < 0.85:
        return 1
    if pct < 0.90:
        return 2
    if pct < 0.95:
        return 3
    if pct < 1.0:
        return 4
    return 5


def texto_umbrales(umbrales):
    if not umbrales:
        return "Sin datos de umbrales en Intervals (usar tests_rendimiento)."
    lineas = []
    r = umbrales.get("run", {})
    if r:
        lineas.append(f"- Run: ritmo umbral {r.get('threshold_pace') or '?'}/km | LTHR {r.get('lthr') or '?'} bpm | potencia umbral {r.get('ftp') or '?'} W | zonas FC {r.get('hr_zones')}")
    b = umbrales.get("bike", {})
    if b:
        lineas.append(f"- Bike: FTP {b.get('ftp') or '?'} W | LTHR {b.get('lthr') or '?'} bpm | zonas FC {b.get('hr_zones')}")
    s = umbrales.get("swim", {})
    if s:
        lineas.append(f"- Swim: ritmo umbral {s.get('threshold_pace') or '?'} (m/s en Intervals) | zonas FC {s.get('hr_zones')}")
    return "\n".join(lineas)


# === PRÓXIMA CARRERA (desde el calendario de Intervals) ===

def get_proxima_carrera(dias=365):
    """Primer evento RACE_A/B/C del calendario. None si no hay."""
    eventos = get_events(days=dias)
    carreras = [e for e in eventos if str(e.get("category", "")).upper().startswith("RACE")]
    if not carreras:
        return None
    carreras.sort(key=lambda e: e.get("start_date_local", ""))
    c = carreras[0]
    try:
        fecha = datetime.strptime(c["start_date_local"][:10], "%Y-%m-%d")
    except Exception:
        return None
    return {
        "nombre": c.get("name", "Carrera"),
        "fecha": fecha.strftime("%Y-%m-%d"),
        "categoria": c.get("category"),
        "dias": (fecha.date() - ahora().date()).days,
    }


# === SUPABASE ===

def _sb_headers(extra=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def supabase_get(table, params=""):
    response = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=_sb_headers(), timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def supabase_post(table, data):
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
        json=data,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response


def supabase_patch(table, match_field, match_value, data):
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_field}=eq.{match_value}",
        headers=_sb_headers(),
        json=data,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response


def get_perfil():
    try:
        perfil_raw = supabase_get("perfil_atleta", "")
        return {r["clave"]: r["valor"] for r in perfil_raw if r.get("valor")}
    except Exception:
        return {}


# === TELEGRAM ===

def _partir(mensaje, limite=4000):
    partes = []
    while len(mensaje) > limite:
        corte = mensaje[:limite].rfind("\n")
        if corte <= 0:
            corte = limite
        partes.append(mensaje[:corte])
        mensaje = mensaje[corte:].lstrip("\n")
    if mensaje.strip():
        partes.append(mensaje)
    return partes


def send_telegram(mensaje):
    """Envía con Markdown; si Telegram lo rechaza (formato roto), reintenta como texto plano."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True
    for parte in _partir(mensaje):
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": parte, "parse_mode": "Markdown"}, timeout=TIMEOUT)
        if not r.ok:
            print(f"   ⚠ Telegram rechazó Markdown ({r.status_code}): {r.text[:200]} — reintento sin formato")
            r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": parte}, timeout=TIMEOUT)
            if not r.ok:
                print(f"   ✗ Telegram falló: {r.status_code} {r.text[:200]}")
                ok = False
    return ok
