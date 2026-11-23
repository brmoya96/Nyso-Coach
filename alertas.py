import os
import json
from datetime import datetime, timedelta

from anthropic import Anthropic

from nyso_common import (
    ahora, hoy_str,
    get_wellness, get_activities, get_umbrales, get_proxima_carrera, clasificar_deporte,
    supabase_get, supabase_post, send_telegram, get_perfil,
    carga, fc_media, fc_reposo, horas_sueno, tsb, respiracion,
)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
client = Anthropic(api_key=ANTHROPIC_API_KEY)

MAX_ALERTAS_DIA = 2          # Alertas de recuperación/carga/countdown por día
MAX_POSTENTRENO_DIA = 4      # Análisis post-entreno: cupo propio, no compiten con las alertas
VENTANA_POSTENTRENO_MIN = 180  # Avisa entrenos terminados en las últimas 3 h (el cron corre cada hora)
HORA_INICIO = 6   # No alertas antes de las 6am (hora Santiago)
HORA_FIN = 22     # No alertas después de las 10pm (hora Santiago)
HITOS_CARRERA = [120, 90, 60, 45, 30, 21, 14, 7]


# === SUPABASE: LOG DE ALERTAS ===

def get_log_hoy():
    try:
        return supabase_get("alertas_log", f"fecha=eq.{hoy_str()}&select=tipo")
    except Exception:
        return []


def registrar_alerta(tipo, mensaje):
    try:
        supabase_post("alertas_log", {
            "fecha": hoy_str(),
            "hora": ahora().strftime("%H:%M"),
            "tipo": tipo,
            "mensaje": mensaje[:200]
        })
    except Exception as e:
        print(f"   ⚠ No se pudo registrar alerta {tipo}: {e}")


def get_ultima_alerta_tipo(tipo):
    try:
        result = supabase_get("alertas_log", f"tipo=eq.{tipo}&order=fecha.desc&limit=1")
        if result:
            return result[0].get("fecha")
    except Exception:
        pass
    return None


def dias_desde_alerta(tipo):
    ultima = get_ultima_alerta_tipo(tipo)
    if not ultima:
        return None
    return (ahora().date() - datetime.strptime(ultima, "%Y-%m-%d").date()).days


def get_analisis_recientes(dias=7):
    try:
        return supabase_get("analisis_diarios", f"order=fecha.desc&limit={dias}")
    except Exception:
        return []


# === LÓGICA DE ALERTAS ===

def calcular_ramp_rate(wellness):
    """ATL hoy vs hace 7 días"""
    if len(wellness) < 7:
        return None
    atl_hoy = wellness[-1].get("atl") or 0
    atl_hace_7 = wellness[-7].get("atl") or 0
    if atl_hace_7 == 0:
        return None
    return round(atl_hoy / atl_hace_7, 2)


def detectar_alertas(wellness_14d, perfil, analisis_recientes, proxima_carrera):
    alertas = []
    today = ahora()

    wellness_hoy = wellness_14d[-1] if wellness_14d else {}
    wellness_ayer = wellness_14d[-2] if len(wellness_14d) >= 2 else {}

    hrv_hoy = wellness_hoy.get("hrv")
    fc_reposo_hoy = fc_reposo(wellness_hoy)
    ctl_hoy = wellness_hoy.get("ctl")
    atl_hoy = wellness_hoy.get("atl")
    tsb_hoy = tsb(wellness_hoy)

    hrv_historico = float(perfil.get("hrv_promedio_historico", 43))
    fc_reposo_historico = float(perfil.get("fc_reposo_promedio", 53))
    ctl_objetivo = perfil.get("ctl_objetivo")

    carrera_txt = ""
    if proxima_carrera and proxima_carrera["dias"] >= 0:
        carrera_txt = f"\nFaltan {proxima_carrera['dias']} días para {proxima_carrera['nombre']}."

    # ==========================================
    # GRUPO 1 — RECUPERACIÓN
    # ==========================================

    # HRV cayendo 3+ días consecutivos y >12% bajo el histórico
    if hrv_hoy and len(wellness_14d) >= 4:
        hrv_ultimos_4 = [w.get("hrv") for w in wellness_14d[-4:] if w.get("hrv")]
        if len(hrv_ultimos_4) >= 3:
            tendencia_baja = all(hrv_ultimos_4[i] >= hrv_ultimos_4[i+1] for i in range(len(hrv_ultimos_4)-1))
            caida_pct = ((hrv_historico - hrv_hoy) / hrv_historico) * 100
            if tendencia_baja and caida_pct > 12 and dias_desde_alerta("hrv_bajo") != 0:
                alertas.append({
                    "tipo": "hrv_bajo",
                    "prioridad": 1,
                    "mensaje": f"🔴 *HRV en caída sostenida*\nHRV hoy: {hrv_hoy} ms (histórico: {hrv_historico} ms, -{round(caida_pct)}%)\nLlevas {len(hrv_ultimos_4)} días consecutivos bajando.\nEsto puede indicar fatiga acumulada, estrés o inicio de enfermedad.\n💡 Considera hablar con tu coach antes de la sesión de hoy."
                })

    # FC reposo elevada 2+ días
    if fc_reposo_hoy and fc_reposo_hoy > fc_reposo_historico + 7:
        fc_ayer = fc_reposo(wellness_ayer) or 0
        if fc_ayer > fc_reposo_historico + 5 and dias_desde_alerta("fc_reposo_alta") != 0:
            alertas.append({
                "tipo": "fc_reposo_alta",
                "prioridad": 1,
                "mensaje": f"🔴 *FC reposo elevada 2 días seguidos*\nFC hoy: {fc_reposo_hoy} bpm (histórico: {fc_reposo_historico} bpm, +{round(fc_reposo_hoy - fc_reposo_historico)} bpm)\nSeñal clásica de recuperación insuficiente.\n💡 Prioriza hidratación y sueño hoy."
            })

    # Frecuencia respiratoria elevada (anticipa enfermedad)
    resp_hoy = respiracion(wellness_hoy)
    resp_previas = [respiracion(w) for w in wellness_14d[:-1]]
    resp_previas = [r for r in resp_previas if r]
    if resp_hoy and len(resp_previas) >= 5:
        resp_base = sum(resp_previas) / len(resp_previas)
        if resp_hoy >= resp_base + 1.5 and dias_desde_alerta("respiracion_alta") != 0:
            alertas.append({
                "tipo": "respiracion_alta",
                "prioridad": 1,
                "mensaje": f"🔴 *Frecuencia respiratoria alta anoche*\n{resp_hoy} rpm vs tu base de {round(resp_base, 1)} rpm.\nSuele adelantarse 1-2 días a un resfrío o a fatiga profunda.\n💡 Si además te sientes raro, hoy suave."
            })

    # Sueño menor a 6h
    sleep_hoy = horas_sueno(wellness_hoy)
    if sleep_hoy and sleep_hoy < 6.0 and dias_desde_alerta("sueno_critico") != 0:
        alertas.append({
            "tipo": "sueno_critico",
            "prioridad": 2,
            "mensaje": f"🟡 *Sueño muy corto anoche*\nDormiste {round(sleep_hoy, 1)}h (mínimo recomendado: 7-8h para triatleta en carga).{carrera_txt}\n💡 Si tienes sesión de calidad hoy, considera reducir intensidad."
        })

    # ==========================================
    # GRUPO 2 — CARGA
    # ==========================================

    ramp_rate = calcular_ramp_rate(wellness_14d)
    if ramp_rate and ramp_rate > 1.4:
        d = dias_desde_alerta("ramp_rate_alto")
        if d is None or d >= 3:
            alertas.append({
                "tipo": "ramp_rate_alto",
                "prioridad": 1,
                "mensaje": f"🔴 *Ramp rate alto esta semana*\nTu ATL subió {ramp_rate}x respecto a la semana pasada.\nEn tu historial, semanas con ramp rate >1.5 preceden lesiones o bajones.\n💡 Habla con tu coach si sientes que la carga es demasiado."
            })

    if tsb_hoy is not None and tsb_hoy < -20 and dias_desde_alerta("tsb_critico") != 0:
        alertas.append({
            "tipo": "tsb_critico",
            "prioridad": 1,
            "mensaje": f"🔴 *Forma muy negativa*\nTSB: {tsb_hoy} (zona de riesgo de sobreentrenamiento)\nCTL: {round(ctl_hoy or 0, 1)} / ATL: {round(atl_hoy or 0, 1)}\n💡 Día de descanso o recuperación activa muy suave."
        })

    if len(wellness_14d) >= 8:
        ctl_hace_7 = wellness_14d[-8].get("ctl")
        if ctl_hoy and ctl_hace_7 and (ctl_hace_7 - ctl_hoy) > 5:
            d = dias_desde_alerta("ctl_cayendo")
            if d is None or d >= 5:
                alertas.append({
                    "tipo": "ctl_cayendo",
                    "prioridad": 2,
                    "mensaje": f"🟡 *CTL bajando esta semana*\nCTL hoy: {round(ctl_hoy, 1)} vs hace 7 días: {round(ctl_hace_7, 1)} (-{round(ctl_hace_7 - ctl_hoy, 1)} puntos)\nPuede ser semana de recuperación planificada (ok) o señal de enfermedad/estrés.\n💡 Si no es semana de descarga planificada, revisa con tu coach."
                })

    # ==========================================
    # GRUPO 3 — COUNTDOWN PRÓXIMA CARRERA (desde calendario Intervals)
    # ==========================================

    if proxima_carrera and proxima_carrera["dias"] in HITOS_CARRERA:
        dias = proxima_carrera["dias"]
        tipo = f"carrera_{proxima_carrera['fecha']}_{dias}"
        if not get_ultima_alerta_tipo(tipo):
            linea_ctl = f"CTL actual: {round(ctl_hoy or 0, 1)}"
            if ctl_objetivo:
                linea_ctl += f" | Objetivo: {ctl_objetivo}"
            alertas.append({
                "tipo": tipo,
                "prioridad": 3,
                "mensaje": f"🏁 *{dias} días para {proxima_carrera['nombre']}*\n{linea_ctl} | TSB: {tsb_hoy}\n💪 Confía en el proceso."
            })

    # CTL milestone cada 5 puntos
    if ctl_hoy:
        ctl_ayer = wellness_ayer.get("ctl") or 0
        for milestone in range(70, 151, 5):
            if ctl_hoy >= milestone > ctl_ayer and dias_desde_alerta(f"ctl_milestone_{milestone}") != 0:
                extra = ""
                if ctl_objetivo:
                    try:
                        extra = f"\nTe faltan {round(float(ctl_objetivo) - milestone)} puntos para tu objetivo de {ctl_objetivo}."
                    except ValueError:
                        pass
                alertas.append({
                    "tipo": f"ctl_milestone_{milestone}",
                    "prioridad": 3,
                    "mensaje": f"💪 *CTL {milestone} alcanzado*\nFitness en {round(ctl_hoy, 1)} puntos.{extra}{carrera_txt}"
                })

    # ==========================================
    # GRUPO 5 — NUTRICIÓN
    # ==========================================

    # Recordatorio fueling antes de la salida larga del sábado (6-8am Santiago)
    if today.weekday() == 5 and 6 <= today.hour <= 8 and dias_desde_alerta("fueling_sabado") != 0:
        alertas.append({
            "tipo": "fueling_sabado",
            "prioridad": 2,
            "mensaje": f"🍌 *Recordatorio fueling — Salida larga hoy*{carrera_txt}\nCada salida larga es un test de tu protocolo GI.\n\n*Checklist pre-salida:*\n- ¿Tienes suficientes geles/barras para la duración?\n- ¿Hidratación con sodio?\n- ¿Comiste bien las últimas 2 horas?\n\nDespués cuéntame cómo fue el estómago."
        })

    return alertas


def detectar_postentrenos(activities_2d, perfil, umbrales, ctl_hoy, proxima_carrera):
    """Entrenos de swim/bike/run terminados en las últimas VENTANA_POSTENTRENO_MIN que aún no se avisaron."""
    alertas = []
    n = ahora()
    for actividad in activities_2d:
        if clasificar_deporte(actividad.get("type")) == "otro":
            continue  # yoga, fuerza, etc.
        start_str = actividad.get("start_date_local", "")
        if not start_str:
            continue
        try:
            inicio = datetime.fromisoformat(start_str[:19])
        except ValueError:
            continue
        duracion_s = actividad.get("elapsed_time") or actividad.get("moving_time") or 0
        fin = inicio + timedelta(seconds=duracion_s)
        minutos_desde_fin = (n - fin).total_seconds() / 60
        if not (0 <= minutos_desde_fin <= VENTANA_POSTENTRENO_MIN):
            continue
        tipo = f"postentreno_{actividad.get('id')}"
        if get_ultima_alerta_tipo(tipo):
            continue

        nombre = actividad.get("name", "Entreno")
        tss = round(carga(actividad))
        duracion = round((actividad.get("moving_time", 0) or 0) / 60)
        fc_avg = round(fc_media(actividad))
        distancia = round((actividad.get("distance", 0) or 0) / 1000, 1)
        alertas.append({
            "tipo": tipo,
            "prioridad": 2,
            "mensaje": f"✅ *{nombre} completado*\n⏱ {duracion} min | 📍 {distancia} km | ❤️ FC {fc_avg} bpm | 📊 TSS {tss}\n\n{generar_analisis_postentreno(actividad, umbrales, ctl_hoy, proxima_carrera)}"
        })
    return alertas


def generar_analisis_postentreno(actividad, umbrales, ctl_hoy, proxima_carrera):
    deporte = clasificar_deporte(actividad.get("type"))
    cfg = umbrales.get(deporte, {})
    contexto_carrera = (
        f"Próxima carrera: {proxima_carrera['nombre']} en {proxima_carrera['dias']} días."
        if proxima_carrera else "Sin carrera próxima en el calendario."
    )

    prompt = f"""Analiza este entreno de un triatleta de Ironman. {contexto_carrera}
Responde en máximo 3 líneas concisas. Sin saludos. Solo el análisis y una recomendación.

Entreno: {json.dumps(actividad, indent=2)}
Umbrales actuales de {deporte} (Intervals): {json.dumps(cfg, ensure_ascii=False)}
CTL actual: {ctl_hoy}

Formato: análisis breve + emoji + recomendación concreta para las próximas horas."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception:
        return "Datos registrados. Buena recuperación."


# === MAIN ===

def main():
    n = ahora()
    print(f"Verificando alertas — {n.strftime('%Y-%m-%d %H:%M')} (Santiago)")
    print("="*40)

    if not (HORA_INICIO <= n.hour <= HORA_FIN):
        print(f"Fuera de horario permitido ({HORA_INICIO}:00-{HORA_FIN}:59). Sin alertas.")
        return

    log_hoy = get_log_hoy()
    post_hoy = sum(1 for r in log_hoy if str(r.get("tipo", "")).startswith("postentreno_"))
    alertas_hoy = len(log_hoy) - post_hoy
    print(f"   Hoy: {alertas_hoy}/{MAX_ALERTAS_DIA} alertas | {post_hoy}/{MAX_POSTENTRENO_DIA} post-entreno")

    print("1. Leyendo datos...")
    wellness_14d = get_wellness(days=14)
    activities_2d = get_activities(days=2)
    perfil = get_perfil()
    analisis_recientes = get_analisis_recientes(dias=7)
    umbrales = get_umbrales()
    proxima_carrera = get_proxima_carrera()
    ctl_hoy = wellness_14d[-1].get("ctl") if wellness_14d else None
    print(f"   ✓ Wellness: {len(wellness_14d)} días | Actividades: {len(activities_2d)} | Perfil: {len(perfil)} campos")

    print("2. Detectando alertas...")
    a_enviar = []
    if alertas_hoy < MAX_ALERTAS_DIA:
        alertas = detectar_alertas(wellness_14d, perfil, analisis_recientes, proxima_carrera)
        alertas.sort(key=lambda x: x["prioridad"])
        a_enviar += alertas[:MAX_ALERTAS_DIA - alertas_hoy]
        print(f"   ✓ {len(alertas)} alertas detectadas")
    if post_hoy < MAX_POSTENTRENO_DIA:
        post = detectar_postentrenos(activities_2d, perfil, umbrales, ctl_hoy, proxima_carrera)
        a_enviar += post[:MAX_POSTENTRENO_DIA - post_hoy]
        print(f"   ✓ {len(post)} entrenos nuevos para analizar")

    if not a_enviar:
        print("   Sin alertas que enviar. Todo en orden.")
        return

    print(f"3. Enviando {len(a_enviar)} mensaje(s)...")
    for alerta in a_enviar:
        if send_telegram(alerta["mensaje"]):
            registrar_alerta(alerta["tipo"], alerta["mensaje"])
            print(f"   ✓ Enviada: {alerta['tipo']}")

    print(f"\n✅ Done. {len(a_enviar)} mensaje(s).")


if __name__ == "__main__":
    main()
