import os
import requests
from datetime import datetime, timedelta
from anthropic import Anthropic
import base64
import json

# === CONFIGURACIÓN ===
INTERVALS_ATHLETE_ID = os.environ["INTERVALS_ATHLETE_ID"]
INTERVALS_API_KEY = os.environ["INTERVALS_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HAWAII_DATE = datetime(2026, 10, 10)
CTL_OBJETIVO = 135  # CTL de Texas (mejor Ironman del atleta)

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# === INTERVALS.ICU ===

def intervals_get(endpoint):
    credentials = base64.b64encode(f"API_KEY:{INTERVALS_API_KEY}".encode()).decode()
    response = requests.get(
        f"https://intervals.icu/api/v1/athlete/{INTERVALS_ATHLETE_ID}/{endpoint}",
        headers={"Authorization": f"Basic {credentials}"}
    )
    response.raise_for_status()
    return response.json()

def get_wellness(days=14):
    today = datetime.now()
    oldest = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    return intervals_get(f"wellness?oldest={oldest}&newest={newest}")

def get_activities(days=7):
    today = datetime.now()
    oldest = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    return intervals_get(f"activities?oldest={oldest}&newest={newest}")

# === SUPABASE ===

def supabase_get(table, params=""):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}?{params}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
    )
    response.raise_for_status()
    return response.json()

def supabase_post(table, data):
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates"
        },
        json=data
    )
    response.raise_for_status()
    return response

# === RECOLECCIÓN DE DATOS ===

def get_datos_semana():
    datos = {}

    datos["wellness_14d"] = get_wellness(days=14)
    datos["actividades_7d"] = get_activities(days=7)

    try:
        datos["recovery_7d"] = supabase_get("recovery_metrics", "order=fecha.desc&limit=7")
    except:
        datos["recovery_7d"] = []

    try:
        perfil_raw = supabase_get("perfil_atleta", "")
        datos["perfil"] = {r["clave"]: r["valor"] for r in perfil_raw if r.get("valor")}
    except:
        datos["perfil"] = {}

    try:
        datos["historia"] = supabase_get("historia_atleta", "order=importancia.desc&limit=5")
    except:
        datos["historia"] = []

    try:
        datos["ultimo_examen"] = supabase_get("examenes_sangre", "order=fecha.desc&limit=1")
    except:
        datos["ultimo_examen"] = []

    return datos

# === CÁLCULOS PRE-CLAUDE ===

def calcular_metricas(datos):
    actividades = datos["actividades_7d"]
    wellness = datos["wellness_14d"]
    recovery_7d = datos["recovery_7d"]
    today = datetime.now()

    # Clasificadores de deporte (Intervals.icu + Strava types)
    swim_keywords = {"swim", "pool"}
    bike_keywords = {"ride", "bike", "cycling", "velo", "gravel", "mtb"}
    run_keywords = {"run", "trail", "treadmill"}

    def clasificar(tipo):
        t = tipo.lower()
        if any(k in t for k in swim_keywords):
            return "swim"
        if any(k in t for k in bike_keywords):
            return "bike"
        if any(k in t for k in run_keywords):
            return "run"
        return "otro"

    # Volumen por deporte
    km_swim = km_bike = km_run = 0.0
    horas_total = 0.0
    tss_total = 0

    for a in actividades:
        tipo = a.get("type", "")
        dist_km = a.get("distance_km", 0) or 0
        tiempo_s = a.get("moving_time", 0) or 0
        tss = a.get("tss", 0) or 0

        horas_total += tiempo_s / 3600
        tss_total += tss
        deporte = clasificar(tipo)
        if deporte == "swim":
            km_swim += dist_km
        elif deporte == "bike":
            km_bike += dist_km
        elif deporte == "run":
            km_run += dist_km

    # CTL/ATL/TSB: inicio (hace 7 días) vs fin (hoy)
    ctl_inicio = atl_inicio = tsb_inicio = None
    ctl_fin = atl_fin = tsb_fin = None

    if wellness:
        w_fin = wellness[-1]
        ctl_fin = w_fin.get("ctl")
        atl_fin = w_fin.get("atl")
        tsb_fin = w_fin.get("tsb")

        w_inicio = wellness[-8] if len(wellness) >= 8 else wellness[0]
        ctl_inicio = w_inicio.get("ctl")
        atl_inicio = w_inicio.get("atl")

    delta_ctl = round(ctl_fin - ctl_inicio, 1) if ctl_fin is not None and ctl_inicio is not None else None
    delta_atl = round(atl_fin - atl_inicio, 1) if atl_fin is not None and atl_inicio is not None else None
    ramp_rate = round(atl_fin / atl_inicio, 2) if atl_fin and atl_inicio and atl_inicio > 0 else None

    ramp_status = "sin datos"
    if ramp_rate is not None:
        if ramp_rate > 1.5:
            ramp_status = "🔴 muy alto"
        elif ramp_rate > 1.3:
            ramp_status = "🟡 elevado"
        else:
            ramp_status = "🟢 normal"

    # Recovery semanal
    recovery_sorted = sorted(recovery_7d, key=lambda x: x.get("fecha", ""))
    recovery_scores = [r.get("recovery_score") for r in recovery_sorted if r.get("recovery_score") is not None]
    recovery_promedio = round(sum(recovery_scores) / len(recovery_scores), 1) if recovery_scores else None

    recovery_por_dia = []
    for r in recovery_sorted:
        score = r.get("recovery_score")
        fecha = r.get("fecha", "")[-5:]  # MM-DD
        color = r.get("recovery_color", "")
        emoji = "🟢" if color == "verde" else ("🟡" if color == "amarillo" else "🔴")
        if score is not None:
            recovery_por_dia.append(f"{fecha}:{emoji}{score}%")

    # Distribución de zonas por FC media ponderada
    # Zonas estimadas por FC: Z1<130, Z2 130-150, Z3 150-162, Z4-Z5 >162
    z1_s = z2_s = z3_s = z45_s = 0
    total_con_hr = 0
    for a in actividades:
        fc = a.get("average_heartrate", 0) or 0
        t = a.get("moving_time", 0) or 0
        if t == 0 or fc == 0:
            continue
        total_con_hr += t
        if fc < 130:
            z1_s += t
        elif fc < 150:
            z2_s += t
        elif fc < 162:
            z3_s += t
        else:
            z45_s += t

    pct_z1_z2 = round((z1_s + z2_s) / total_con_hr * 100) if total_con_hr > 0 else None
    zonas_horas = {
        "z1": round(z1_s / 3600, 1),
        "z2": round(z2_s / 3600, 1),
        "z3": round(z3_s / 3600, 1),
        "z4_5": round(z45_s / 3600, 1),
    }

    # Hawaii countdown y proyección CTL
    dias_hawaii = (HAWAII_DATE - today).days
    semanas_hawaii = dias_hawaii / 7
    deficit_ctl = round(CTL_OBJETIVO - ctl_fin, 1) if ctl_fin is not None else None

    ctl_proyeccion = None
    if delta_ctl is not None and ctl_fin is not None and semanas_hawaii > 0:
        ctl_proyeccion = round(ctl_fin + delta_ctl * semanas_hawaii, 1)

    # Sesión clave: mayor TSS
    sesion_clave = None
    if actividades:
        mejor = max(actividades, key=lambda a: a.get("tss", 0) or 0)
        sesion_clave = {
            "nombre": mejor.get("name", ""),
            "tipo": mejor.get("type", ""),
            "duracion_min": round((mejor.get("moving_time", 0) or 0) / 60),
            "distancia_km": round(mejor.get("distance_km", 0) or 0, 1),
            "tss": mejor.get("tss"),
            "fc_media": mejor.get("average_heartrate"),
            "fecha": mejor.get("start_date_local", "")[:10],
        }

    semana_num = today.isocalendar()[1]
    fecha_inicio_semana = (today - timedelta(days=6)).strftime("%d/%m")
    fecha_fin_semana = today.strftime("%d/%m")

    return {
        "km_swim": round(km_swim, 2),
        "km_bike": round(km_bike, 1),
        "km_run": round(km_run, 1),
        "horas_total": round(horas_total, 1),
        "tss_total": int(tss_total),
        "ctl_inicio": ctl_inicio,
        "ctl_fin": ctl_fin,
        "atl_inicio": atl_inicio,
        "atl_fin": atl_fin,
        "tsb_fin": tsb_fin,
        "delta_ctl": delta_ctl,
        "delta_atl": delta_atl,
        "ramp_rate": ramp_rate,
        "ramp_status": ramp_status,
        "recovery_promedio": recovery_promedio,
        "recovery_por_dia": recovery_por_dia,
        "zonas_horas": zonas_horas,
        "pct_z1_z2": pct_z1_z2,
        "dias_hawaii": dias_hawaii,
        "semanas_hawaii": round(semanas_hawaii, 1),
        "ctl_proyeccion": ctl_proyeccion,
        "deficit_ctl": deficit_ctl,
        "sesion_clave": sesion_clave,
        "semana_num": semana_num,
        "fecha_inicio_semana": fecha_inicio_semana,
        "fecha_fin_semana": fecha_fin_semana,
    }

# === CLAUDE ===

def _fmt(v, decimals=1, prefix="", suffix="", fallback="sin datos"):
    if v is None:
        return fallback
    try:
        return f"{prefix}{round(float(v), decimals)}{suffix}"
    except:
        return fallback

def _fmt_delta(v, decimals=1, fallback="sin datos"):
    if v is None:
        return fallback
    try:
        return f"{float(v):+.{decimals}f}"
    except:
        return fallback

def generar_resumen(datos, m):
    recovery_dias_str = " | ".join(m["recovery_por_dia"]) if m["recovery_por_dia"] else "sin datos"

    prompt = f"""Eres un coach de triatlón experto generando el resumen semanal de tu atleta. Todos los números ya están calculados — úsalos directamente, no los recalcules.

## MÉTRICAS DE LA SEMANA (pre-calculadas)

Semana {m['semana_num']} — {m['fecha_inicio_semana']} al {m['fecha_fin_semana']}

### Volumen:
- Natación: {_fmt(m['km_swim'], 2)} km
- Ciclismo: {_fmt(m['km_bike'])} km
- Running: {_fmt(m['km_run'])} km
- Horas totales: {_fmt(m['horas_total'])}h
- TSS total: {m['tss_total']}

### Carga:
- CTL: {_fmt(m['ctl_inicio'])} → {_fmt(m['ctl_fin'])} (delta {_fmt_delta(m['delta_ctl'])})
- ATL: {_fmt(m['atl_inicio'])} → {_fmt(m['atl_fin'])} (delta {_fmt_delta(m['delta_atl'])})
- TSB fin de semana: {_fmt(m['tsb_fin'])}
- Ramp rate: {_fmt(m['ramp_rate'], 2)} — {m['ramp_status']}

### Recovery semanal:
- Promedio: {_fmt(m['recovery_promedio'], 1, suffix='%')} (referencia: marzo pico 81%)
- Por día (fecha:emoji:score): {recovery_dias_str}

### Distribución de zonas (estimación desde FC media, Z1<130bpm, Z2 130-150, Z3 150-162, Z4-Z5>162):
- Z1: {m['zonas_horas']['z1']}h | Z2: {m['zonas_horas']['z2']}h | Z3: {m['zonas_horas']['z3']}h | Z4-Z5: {m['zonas_horas']['z4_5']}h
- % Z1-Z2 del tiempo con HR registrado: {_fmt(m['pct_z1_z2'], 0, suffix='%')} (objetivo >75%)

### Hawaii countdown:
- Días: {m['dias_hawaii']} ({m['semanas_hawaii']} semanas)
- CTL actual: {_fmt(m['ctl_fin'])} | Objetivo Texas: {CTL_OBJETIVO} | Déficit: {_fmt(m['deficit_ctl'])} puntos
- Proyección CTL en octubre si mantiene delta semanal actual: {_fmt(m['ctl_proyeccion'])}

### Sesión clave de la semana (mayor TSS):
{json.dumps(m['sesion_clave'], indent=2, ensure_ascii=False) if m['sesion_clave'] else 'Sin actividades'}

## CONTEXTO ADICIONAL

### Actividades detalladas:
{json.dumps(datos['actividades_7d'], indent=2, ensure_ascii=False)}

### Wellness últimos 14 días:
{json.dumps(datos['wellness_14d'], indent=2, ensure_ascii=False)}

### Perfil del atleta:
{json.dumps(datos['perfil'], indent=2, ensure_ascii=False)}

### Historia relevante:
{json.dumps(datos['historia'], indent=2, ensure_ascii=False)}

### Último examen de sangre:
{json.dumps(datos['ultimo_examen'], indent=2, ensure_ascii=False)}

## INSTRUCCIONES

Genera el resumen semanal para Telegram con estas secciones en este orden exacto:

1. *Semana {m['semana_num']} — {m['fecha_inicio_semana']} al {m['fecha_fin_semana']}*
2. *Volumen* — swim / bike / run en km, horas totales, TSS total
3. *Carga* — CTL con delta, ATL con delta, TSB, ramp rate con semáforo. Explica qué significa el ramp rate en una línea.
4. *Recovery* — promedio semanal con emoji color + fila de emojis/scores por día + comparación vs marzo (81%) en una línea
5. *Sesión clave* — nombre, tipo, duración, TSS y por qué destacó
6. *Zonas* — horas por zona, porcentaje Z1-Z2 real vs objetivo 75%, una línea de evaluación
7. *Hawaii* — días restantes, CTL actual vs objetivo, déficit, proyección, semáforo de progreso (🔴🟡🟢)
8. *Top 3 insights* — uno positivo, uno a mejorar, uno de tendencia. Específicos y accionables, sin perogrulladas.
9. *Semana que viene* — foco concreto en 2 líneas máximo

Formato Markdown Telegram (* para negrita). Máximo 500 palabras. Usa los números calculados exactos. Sé directo y específico.

Después del mensaje, separado por ---JSON---:

{{
  "insights": ["insight positivo", "insight a mejorar", "tendencia"],
  "recomendacion_semana": "foco concreto semana siguiente",
  "banderas": []
}}
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    full_response = message.content[0].text

    if "---JSON---" in full_response:
        parts = full_response.split("---JSON---")
        mensaje = parts[0].strip()
        try:
            metadata = json.loads(parts[1].strip())
        except:
            metadata = {"insights": [], "recomendacion_semana": "", "banderas": []}
    else:
        mensaje = full_response
        metadata = {"insights": [], "recomendacion_semana": "", "banderas": []}

    return mensaje, metadata

# === GUARDAR ===

def guardar_resumen(m, mensaje, metadata):
    today = datetime.now()
    fecha_fin = today.strftime("%Y-%m-%d")
    fecha_inicio = (today - timedelta(days=6)).strftime("%Y-%m-%d")

    def to_float(v):
        try: return round(float(v), 2) if v is not None else None
        except: return None

    def to_int(v):
        try: return int(float(v)) if v is not None else None
        except: return None

    data = {
        "fecha_inicio": fecha_inicio,
        "fecha_fin": fecha_fin,
        "semana_numero": to_int(m["semana_num"]),
        "horas_total": to_float(m["horas_total"]),
        "tss_total": to_int(m["tss_total"]),
        "ctl_inicio": to_float(m["ctl_inicio"]),
        "ctl_fin": to_float(m["ctl_fin"]),
        "atl_inicio": to_float(m["atl_inicio"]),
        "atl_fin": to_float(m["atl_fin"]),
        "tsb_fin": to_float(m["tsb_fin"]),
        "ramp_rate": to_float(m["ramp_rate"]),
        "recovery_promedio": to_float(m["recovery_promedio"]),
        "km_swim": to_float(m["km_swim"]),
        "km_bike": to_float(m["km_bike"]),
        "km_run": to_float(m["km_run"]),
        "mensaje_telegram": str(mensaje),
        "insights": metadata.get("insights", []),
    }
    data = {k: v for k, v in data.items() if v is not None}
    supabase_post("resumenes_semanales", data)

# === TELEGRAM ===

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(message) <= 4096:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        })
    else:
        mid = message[:4096].rfind("\n")
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message[:mid],
            "parse_mode": "Markdown"
        })
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message[mid:],
            "parse_mode": "Markdown"
        })

# === MAIN ===

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"Resumen Semanal — {today_str}")
    print("="*40)

    print("1. Recolectando datos...")
    datos = get_datos_semana()
    print(f"   ✓ Wellness: {len(datos['wellness_14d'])} días")
    print(f"   ✓ Actividades: {len(datos['actividades_7d'])}")
    print(f"   ✓ Recovery: {len(datos['recovery_7d'])} días")
    print(f"   ✓ Historia: {len(datos['historia'])} entradas")
    print(f"   ✓ Examen sangre: {len(datos['ultimo_examen'])} registro(s)")

    print("2. Calculando métricas...")
    metricas = calcular_metricas(datos)
    print(f"   ✓ Volumen: swim {metricas['km_swim']}km | bike {metricas['km_bike']}km | run {metricas['km_run']}km")
    print(f"   ✓ Horas: {metricas['horas_total']}h | TSS: {metricas['tss_total']}")
    print(f"   ✓ CTL: {metricas['ctl_inicio']} → {metricas['ctl_fin']} | Ramp: {metricas['ramp_rate']} {metricas['ramp_status']}")
    print(f"   ✓ Recovery promedio: {metricas['recovery_promedio']}%")
    print(f"   ✓ Hawaii: {metricas['dias_hawaii']} días | CTL proyectado: {metricas['ctl_proyeccion']}")

    print("3. Generando resumen con Claude...")
    mensaje, metadata = generar_resumen(datos, metricas)
    print(f"   ✓ Mensaje generado ({len(mensaje)} chars)")
    print(f"   ✓ Insights: {len(metadata.get('insights', []))}")

    print("4. Enviando a Telegram...")
    send_telegram(mensaje)
    print("   ✓ Enviado!")

    print("5. Guardando en Supabase...")
    try:
        guardar_resumen(metricas, mensaje, metadata)
        print("   ✓ Guardado en resumenes_semanales")
    except Exception as e:
        print(f"   ⚠ Error guardando: {e}")

    print("\n✅ Done. Resumen semanal enviado.")

if __name__ == "__main__":
    main()
