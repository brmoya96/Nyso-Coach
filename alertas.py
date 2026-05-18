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

client = Anthropic(api_key=ANTHROPIC_API_KEY)

HAWAII_DATE = datetime(2026, 10, 10)
MAX_ALERTAS_DIA = 2
HORA_INICIO = 6   # No alertas antes de las 6am
HORA_FIN = 22     # No alertas después de las 10pm

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

def get_activities(days=2):
    today = datetime.now()
    oldest = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    return intervals_get(f"activities?oldest={oldest}&newest={newest}")

def get_fitness(days=14):
    today = datetime.now()
    oldest = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    data = intervals_get(f"wellness?oldest={oldest}&newest={newest}")
    return data[-14:] if len(data) > 14 else data

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

def get_alertas_hoy():
    """Cuántas alertas se mandaron hoy"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        result = supabase_get(
            "alertas_log",
            f"fecha=eq.{today}&select=count"
        )
        return result[0].get("count", 0) if result else 0
    except:
        return 0

def registrar_alerta(tipo, mensaje):
    """Registra la alerta en el log"""
    today = datetime.now().strftime("%Y-%m-%d")
    hora = datetime.now().strftime("%H:%M")
    try:
        supabase_post("alertas_log", {
            "fecha": today,
            "hora": hora,
            "tipo": tipo,
            "mensaje": mensaje[:200]
        })
    except:
        pass

def get_ultima_alerta_tipo(tipo):
    """Cuándo fue la última alerta de este tipo"""
    try:
        result = supabase_get(
            "alertas_log",
            f"tipo=eq.{tipo}&order=fecha.desc&limit=1"
        )
        if result:
            return result[0].get("fecha")
    except:
        pass
    return None

def get_perfil():
    """Lee perfil del atleta"""
    try:
        perfil_raw = supabase_get("perfil_atleta", "")
        return {r["clave"]: r["valor"] for r in perfil_raw if r.get("valor")}
    except:
        return {}

def get_patrones():
    """Lee patrones activos"""
    try:
        return supabase_get("patrones", "activo=eq.true&order=ultima_actualizacion.desc")
    except:
        return []

def get_analisis_recientes(dias=7):
    """Lee análisis recientes para comparar"""
    try:
        return supabase_get(
            "analisis_diarios",
            f"order=fecha.desc&limit={dias}"
        )
    except:
        return []

# === TELEGRAM ===

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    })

# === LÓGICA DE ALERTAS ===

def calcular_ramp_rate(fitness_data):
    """Calcula ramp rate semanal (ATL esta semana vs semana pasada)"""
    if len(fitness_data) < 7:
        return None
    atl_hoy = fitness_data[-1].get("atl", 0) or 0
    atl_hace_7 = fitness_data[-7].get("atl", 0) or 0
    if atl_hace_7 == 0:
        return None
    return round(atl_hoy / atl_hace_7, 2)

def detectar_alertas(wellness_14d, activities_2d, fitness_14d, perfil, patrones, analisis_recientes):
    """Detecta todas las alertas posibles y retorna lista de las que aplican"""
    
    alertas = []
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    dias_hawaii = (HAWAII_DATE - today).days

    # Datos actuales
    wellness_hoy = wellness_14d[-1] if wellness_14d else {}
    wellness_ayer = wellness_14d[-2] if len(wellness_14d) >= 2 else {}

    hrv_hoy = wellness_hoy.get("hrv")
    fc_reposo_hoy = wellness_hoy.get("restingHeartRate")
    ctl_hoy = wellness_hoy.get("ctl")
    atl_hoy = wellness_hoy.get("atl")
    tsb_hoy = wellness_hoy.get("tsb")

    # Promedios históricos del perfil
    hrv_historico = float(perfil.get("hrv_promedio_historico", 43))
    fc_reposo_historico = float(perfil.get("fc_reposo_promedio", 53))

    # Promedios de análisis recientes
    hrv_recientes = [a["hrv"] for a in analisis_recientes if a.get("hrv")]
    hrv_promedio_reciente = sum(hrv_recientes) / len(hrv_recientes) if hrv_recientes else hrv_historico

    # ==========================================
    # GRUPO 1 — RECUPERACIÓN
    # ==========================================

    # HRV cayendo 3+ días consecutivos más de 15%
    if hrv_hoy and len(wellness_14d) >= 4:
        hrv_ultimos_4 = [w.get("hrv") for w in wellness_14d[-4:] if w.get("hrv")]
        if len(hrv_ultimos_4) >= 3:
            tendencia_baja = all(hrv_ultimos_4[i] >= hrv_ultimos_4[i+1] for i in range(len(hrv_ultimos_4)-1))
            caida_pct = ((hrv_historico - hrv_hoy) / hrv_historico) * 100
            ultima = get_ultima_alerta_tipo("hrv_bajo")
            ya_alertado_hoy = ultima == today_str
            if tendencia_baja and caida_pct > 12 and not ya_alertado_hoy:
                alertas.append({
                    "tipo": "hrv_bajo",
                    "prioridad": 1,
                    "mensaje": f"🔴 *HRV en caída sostenida*\nHRV hoy: {hrv_hoy} ms (histórico: {hrv_historico} ms, -{round(caida_pct)}%)\nLlevas {len(hrv_ultimos_4)} días consecutivos bajando.\nEsto puede indicar fatiga acumulada, estrés o inicio de enfermedad.\n💡 Considera hablar con tu coach antes de la sesión de hoy."
                })

    # FC reposo elevada 2+ días
    if fc_reposo_hoy and fc_reposo_hoy > fc_reposo_historico + 7:
        fc_ayer = wellness_ayer.get("restingHeartRate", 0) or 0
        ultima = get_ultima_alerta_tipo("fc_reposo_alta")
        ya_alertado_hoy = ultima == today_str
        if fc_ayer > fc_reposo_historico + 5 and not ya_alertado_hoy:
            alertas.append({
                "tipo": "fc_reposo_alta",
                "prioridad": 1,
                "mensaje": f"🔴 *FC reposo elevada 2 días seguidos*\nFC hoy: {fc_reposo_hoy} bpm (histórico: {fc_reposo_historico} bpm, +{fc_reposo_hoy - fc_reposo_historico} bpm)\nSeñal clásica de recuperación insuficiente.\n💡 Prioriza hidratación y sueño hoy."
            })

    # Sueño menor a 6h
    sleep_hoy = None
    if wellness_hoy.get("sleepSecs"):
        sleep_hoy = wellness_hoy["sleepSecs"] / 3600
    elif wellness_hoy.get("sleepHours"):
        sleep_hoy = float(wellness_hoy["sleepHours"])

    if sleep_hoy and sleep_hoy < 6.0:
        ultima = get_ultima_alerta_tipo("sueno_critico")
        ya_alertado_hoy = ultima == today_str
        if not ya_alertado_hoy:
            alertas.append({
                "tipo": "sueno_critico",
                "prioridad": 2,
                "mensaje": f"🟡 *Sueño muy corto anoche*\nDormiste {round(sleep_hoy, 1)}h (mínimo recomendado: 7-8h para triatleta en carga).\nCon {dias_hawaii} días para Hawaii, el sueño es parte del entrenamiento.\n💡 Si tienes sesión de calidad hoy, considera reducir intensidad."
            })

    # ==========================================
    # GRUPO 2 — CARGA
    # ==========================================

    # Ramp rate alto
    ramp_rate = calcular_ramp_rate(fitness_14d)
    if ramp_rate and ramp_rate > 1.4:
        ultima = get_ultima_alerta_tipo("ramp_rate_alto")
        ya_alertado = ultima and (datetime.now() - datetime.strptime(ultima, "%Y-%m-%d")).days < 3
        if not ya_alertado:
            alertas.append({
                "tipo": "ramp_rate_alto",
                "prioridad": 1,
                "mensaje": f"🔴 *Ramp rate alto esta semana*\nTu ATL subió {ramp_rate}x respecto a la semana pasada.\nEn tu historial, semanas con ramp rate >1.5 preceden lesiones o bajones.\nRecuerda: vienes de un Ironman hace menos de 4 semanas, tu cuerpo aún está adaptándose.\n💡 Habla con tu coach si sientes que la carga es demasiado."
            })

    # TSB muy negativo
    if tsb_hoy and tsb_hoy < -20:
        ultima = get_ultima_alerta_tipo("tsb_critico")
        ya_alertado_hoy = ultima == today_str
        if not ya_alertado_hoy:
            alertas.append({
                "tipo": "tsb_critico",
                "prioridad": 1,
                "mensaje": f"🔴 *Forma muy negativa*\nTSB: {tsb_hoy} (zona de riesgo de sobreentrenamiento)\nCTL: {ctl_hoy} / ATL: {atl_hoy}\nEn Texas llegaste con TSB -13.7. Estás más fatigado que el día de tu Ironman.\n💡 Día de descanso o recuperación activa muy suave."
            })

    # CTL cayendo más de 5 puntos en una semana
    if fitness_14d and len(fitness_14d) >= 8:
        ctl_hace_7 = fitness_14d[-8].get("ctl") if len(fitness_14d) >= 8 else None
        if ctl_hoy and ctl_hace_7 and (ctl_hace_7 - ctl_hoy) > 5:
            ultima = get_ultima_alerta_tipo("ctl_cayendo")
            ya_alertado = ultima and (datetime.now() - datetime.strptime(ultima, "%Y-%m-%d")).days < 5
            if not ya_alertado:
                alertas.append({
                    "tipo": "ctl_cayendo",
                    "prioridad": 2,
                    "mensaje": f"🟡 *CTL bajando esta semana*\nCTL hoy: {ctl_hoy} vs hace 7 días: {ctl_hace_7} (-{round(ctl_hace_7 - ctl_hoy, 1)} puntos)\nPuede ser semana de recuperación planificada (ok) o señal de enfermedad/estrés.\n💡 Si no es semana de descarga planificada, revisa con tu coach."
                })

    # ==========================================
    # GRUPO 3 — HAWAII COUNTDOWN
    # ==========================================

    # Hitos de countdown
    hitos = [120, 90, 60, 45, 30, 21, 14, 7]
    if dias_hawaii in hitos:
        ultima = get_ultima_alerta_tipo(f"hawaii_{dias_hawaii}")
        ya_alertado_hoy = ultima == today_str
        if not ya_alertado_hoy:
            alertas.append({
                "tipo": f"hawaii_{dias_hawaii}",
                "prioridad": 3,
                "mensaje": f"🌺 *{dias_hawaii} días para Hawaii*\nCTL actual: {ctl_hoy} | En Texas (tu mejor Ironman): CTL 135\nTe faltan {round(135 - (ctl_hoy or 96), 1)} puntos de CTL para estar en forma similar a Texas.\nTSB objetivo para Hawaii: entre +10 y +15 (en Texas llegaste con -13.7, el objetivo es llegar más fresco).\n💪 Vas en el camino correcto. Confía en el proceso."
            })

    # CTL milestone cada 5 puntos
    if ctl_hoy:
        for milestone in [100, 105, 110, 115, 120, 125, 130]:
            ctl_ayer = wellness_ayer.get("ctl", 0) or 0
            if ctl_hoy >= milestone > ctl_ayer:
                ultima = get_ultima_alerta_tipo(f"ctl_milestone_{milestone}")
                ya_alertado_hoy = ultima == today_str
                if not ya_alertado_hoy:
                    alertas.append({
                        "tipo": f"ctl_milestone_{milestone}",
                        "prioridad": 3,
                        "mensaje": f"💪 *CTL {milestone} alcanzado*\nFitness en {ctl_hoy} puntos. Recuperando forma post-Texas.\nEn Texas llegaste con CTL 135 — te faltan {135 - milestone} puntos.\nFaltan {dias_hawaii} días para Hawaii. Vas bien."
                    })

    # ==========================================
    # GRUPO 4 — POST-ENTRENO
    # ==========================================

    # Nuevo entreno detectado (últimas 2 horas)
    ahora = datetime.now()
    for actividad in activities_2d:
        start_str = actividad.get("start_date_local", "")
        if not start_str:
            continue
        try:
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00").replace("+00:00", ""))
            minutos_desde = (ahora - start_time).total_seconds() / 60
            if 10 < minutos_desde < 120:
                ultima = get_ultima_alerta_tipo(f"postentreno_{actividad.get('id')}")
                if not ultima:
                    nombre = actividad.get("name", "Entreno")
                    tipo = actividad.get("type", "")
                    tss = actividad.get("training_load", 0) or 0
                    duracion = round((actividad.get("moving_time", 0) or 0) / 60)
                    fc_avg = actividad.get("average_heartrate", 0) or 0
                    distancia = round((actividad.get("distance", 0) or 0) / 1000, 1)

                    alertas.append({
                        "tipo": f"postentrino_{actividad.get('id')}",
                        "prioridad": 2,
                        "mensaje": f"✅ *{nombre} completado*\n⏱ {duracion} min | 📍 {distancia} km | ❤️ FC {fc_avg} bpm | 📊 TSS {tss}\n\n{generar_analisis_postentrino(actividad, perfil, ctl_hoy, dias_hawaii)}"
                    })
        except:
            continue

    # ==========================================
    # GRUPO 5 — NUTRICIÓN
    # ==========================================

    # Recordatorio fueling antes de salida larga sábado
    if today.weekday() == 5:  # Sábado
        hora_actual = today.hour
        if 5 <= hora_actual <= 7:
            ultima = get_ultima_alerta_tipo("fueling_sabado")
            ya_alertado_hoy = ultima == today_str
            if not ya_alertado_hoy:
                alertas.append({
                    "tipo": "fueling_sabado",
                    "prioridad": 2,
                    "mensaje": f"🍌 *Recordatorio fueling — Salida larga hoy*\nHoy es tu bici larga. {dias_hawaii} días para Hawaii.\nEn Texas tuviste problemas GI severos. Cada salida larga es un test.\n\n*Checklist pre-salida:*\n- ¿Tienes suficientes geles/barras para la duración?\n- ¿Hidratación con sodio?\n- ¿Comiste bien las últimas 2 horas?\n\nDespués cuéntame cómo fue el estómago. Estamos construyendo tu protocolo Hawaii."
                })

    return alertas

def generar_analisis_postentrino(actividad, perfil, ctl_hoy, dias_hawaii):
    """Genera análisis rápido post-entreno con Claude"""
    tipo = actividad.get("type", "")
    ftp = float(perfil.get("ftp_watts", 317))
    threshold_run = perfil.get("threshold_pace_run", "3:26")

    prompt = f"""Analiza este entreno de un triatleta clasificado a Hawaii (en {dias_hawaii} días). 
Responde en máximo 3 líneas concisas. Sin saludos. Solo el análisis y una recomendación.

Entreno: {json.dumps(actividad, indent=2)}
FTP atleta: {ftp}w
Threshold run: {threshold_run}/km
CTL actual: {ctl_hoy}

Formato: análisis breve + emoji + recomendación concreta para las próximas horas."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except:
        return "Datos registrados. Buena recuperación."

def esta_en_horario_permitido():
    """Verifica si es horario permitido para alertas"""
    hora = datetime.now().hour
    return HORA_INICIO <= hora <= HORA_FIN

# === MAIN ===

def main():
    now = datetime.now()
    print(f"Verificando alertas — {now.strftime('%Y-%m-%d %H:%M')}")
    print("="*40)

    # Verificar horario
    if not esta_en_horario_permitido():
        print(f"Fuera de horario permitido ({HORA_INICIO}am-{HORA_FIN}pm). Sin alertas.")
        return

    # Verificar límite diario
    alertas_hoy = get_alertas_hoy()
    if alertas_hoy >= MAX_ALERTAS_DIA:
        print(f"Límite diario alcanzado ({alertas_hoy}/{MAX_ALERTAS_DIA}). Sin más alertas hoy.")
        return

    print("1. Leyendo datos...")
    wellness_14d = get_wellness(days=14)
    activities_2d = get_activities(days=2)
    fitness_14d = get_fitness(days=14)
    perfil = get_perfil()
    patrones = get_patrones()
    analisis_recientes = get_analisis_recientes(dias=7)
    print(f"   ✓ Wellness: {len(wellness_14d)} días | Actividades: {len(activities_2d)} | Perfil: {len(perfil)} campos")

    print("2. Detectando alertas...")
    todas_alertas = detectar_alertas(
        wellness_14d, activities_2d, fitness_14d,
        perfil, patrones, analisis_recientes
    )
    print(f"   ✓ {len(todas_alertas)} alertas detectadas")

    if not todas_alertas:
        print("   Sin alertas que enviar. Todo en orden.")
        return

    # Ordenar por prioridad y tomar las más importantes
    todas_alertas.sort(key=lambda x: x["prioridad"])
    disponibles = MAX_ALERTAS_DIA - alertas_hoy
    alertas_a_enviar = todas_alertas[:disponibles]

    print(f"3. Enviando {len(alertas_a_enviar)} alerta(s)...")
    for alerta in alertas_a_enviar:
        send_telegram(alerta["mensaje"])
        registrar_alerta(alerta["tipo"], alerta["mensaje"])
        print(f"   ✓ Enviada: {alerta['tipo']}")

    print(f"\n✅ Done. {len(alertas_a_enviar)} alerta(s) enviada(s).")

if __name__ == "__main__":
    main()
