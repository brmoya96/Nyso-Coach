import os
import json
from datetime import timedelta

import requests
from anthropic import Anthropic

from nyso_common import (
    ahora, hoy_str, es_ejecucion_programada,
    get_wellness, get_activities, get_events, get_umbrales, texto_umbrales, get_proxima_carrera,
    supabase_get, supabase_post, supabase_patch, send_telegram, get_perfil,
    carga, fc_media, fc_reposo, horas_sueno, tsb, respiracion, spo2, actividad_es_hoy,
)
from recovery_calc import procesar_recovery, formatear_recovery_bloque

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
client = Anthropic(api_key=ANTHROPIC_API_KEY)

HORA_ENVIO = 5  # El cron corre 2 veces (invierno/verano); solo se envía desde las 5am de Santiago


def ya_enviado_hoy():
    try:
        return bool(supabase_get("analisis_diarios", f"fecha=eq.{hoy_str()}&select=fecha"))
    except Exception:
        return False


def get_memoria():
    """Lee toda la memoria relevante de Supabase"""
    memoria = {}

    # Últimos 30 análisis diarios
    try:
        memoria["analisis_recientes"] = supabase_get(
            "analisis_diarios",
            "order=fecha.desc&limit=30"
        )
    except:
        memoria["analisis_recientes"] = []

    # Patrones activos
    try:
        memoria["patrones_activos"] = supabase_get(
            "patrones",
            "activo=eq.true&order=ultima_actualizacion.desc"
        )
    except:
        memoria["patrones_activos"] = []

    # Perfil del atleta
    memoria["perfil"] = get_perfil()

    # Últimas 5 carreras
    try:
        memoria["carreras_recientes"] = supabase_get(
            "carreras",
            "order=fecha.desc&limit=5"
        )
    except:
        memoria["carreras_recientes"] = []

    # Historia del atleta
    try:
        memoria["historia"] = supabase_get(
            "historia_atleta",
            "order=importancia.desc&limit=20"
        )
    except:
        memoria["historia"] = []

    # Recovery últimos 30 días
    try:
        memoria["recovery_30d"] = supabase_get(
            "recovery_metrics",
            "order=fecha.desc&limit=30"
        )
    except:
        memoria["recovery_30d"] = []

    # Recovery mes peak — marzo 2026 (referencia: promedio 81%)
    try:
        memoria["recovery_peak_marzo"] = supabase_get(
            "recovery_metrics",
            "fecha=gte.2026-03-01&fecha=lte.2026-03-31&order=fecha.asc"
        )
    except:
        memoria["recovery_peak_marzo"] = []

    # Últimos 3 exámenes de sangre
    try:
        memoria["examenes_sangre"] = supabase_get(
            "examenes_sangre",
            "order=fecha.desc&limit=3"
        )
    except:
        memoria["examenes_sangre"] = []

    # Últimos 5 tests de rendimiento
    try:
        memoria["tests_rendimiento"] = supabase_get(
            "tests_rendimiento",
            "order=fecha.desc&limit=5"
        )
    except:
        memoria["tests_rendimiento"] = []

    return memoria

def guardar_analisis(fecha, wellness_hoy, actividades_hoy, tss_dia, mensaje, banderas, recomendacion):
    """Guarda el análisis de hoy en Supabase"""

    def to_int(v):
        try: return int(float(v)) if v is not None else None
        except: return None

    def to_float(v):
        try: return round(float(v), 2) if v is not None else None
        except: return None

    # Calcular horas de sueño
    hs = horas_sueno(wellness_hoy)
    hs = round(hs, 2) if hs is not None else None

    # Limpiar actividades
    actividades_limpias = []
    for a in actividades_hoy:
        try:
            dur = to_int(a.get("moving_time", 0) // 60 if a.get("moving_time") else 0)
            dist = to_float(a.get("distance", 0) / 1000 if a.get("distance") else 0)
            actividades_limpias.append({
                "id": str(a.get("id", "")),
                "name": str(a.get("name", "")),
                "type": str(a.get("type", "")),
                "duration_minutes": dur,
                "distance_km": dist,
                "tss": to_float(carga(a)),
                "avg_hr": to_int(fc_media(a))
            })
        except:
            pass

    data = {
        "fecha": str(fecha),
        "hrv": to_int(wellness_hoy.get("hrv")),
        "fc_reposo": to_int(fc_reposo(wellness_hoy)),
        "horas_sueno": hs,
        "sleep_score": to_int(wellness_hoy.get("sleepScore")),
        "ctl": to_float(wellness_hoy.get("ctl")),
        "atl": to_float(wellness_hoy.get("atl")),
        "tsb": to_float(tsb(wellness_hoy)),
        "actividades_hoy": actividades_limpias,
        "tss_dia": to_int(tss_dia),
        "mensaje_telegram": str(mensaje),
        "banderas": banderas if isinstance(banderas, list) else [],
        "recomendacion": str(recomendacion) if recomendacion else ""
    }
    # Eliminar nulos
    data = {k: v for k, v in data.items() if v is not None}
    print(f"   DEBUG campos enviados: {list(data.keys())}")
    supabase_post("analisis_diarios", data)

def guardar_recovery(fecha, resultado):
    """Guarda el Recovery Score de hoy en la tabla recovery_metrics de Supabase"""
    if "error" in resultado:
        return

    def to_int(v):
        try: return int(float(v)) if v is not None else None
        except: return None

    def to_float(v):
        try: return round(float(v), 2) if v is not None else None
        except: return None

    data = {
        "fecha": str(fecha),
        "recovery_score": to_int(resultado.get("recovery_score")),
        "recovery_color": str(resultado.get("recovery_color", "")),
        "hrv_hoy": to_int(resultado.get("hrv_hoy")),
        "hrv_baseline": to_float(resultado.get("hrv_baseline")),
        "hrv_score": to_int(resultado.get("hrv_score")),
        "fc_reposo_hoy": to_int(resultado.get("fc_reposo_hoy")),
        "fc_reposo_baseline": to_float(resultado.get("fc_reposo_baseline")),
        "fc_reposo_score": to_int(resultado.get("fc_reposo_score")),
        "horas_sueno": to_float(resultado.get("horas_sueno")),
        "sleep_score": to_int(resultado.get("sleep_score")),
        "day_strain": to_float(resultado.get("day_strain")),
    }
    data = {k: v for k, v in data.items() if v is not None}
    supabase_post("recovery_metrics", data)


def actualizar_patron(categoria, descripcion, severidad="info"):
    """Crea o actualiza un patrón detectado"""
    today = hoy_str()

    # Buscar si existe el patrón
    existing = supabase_get(
        "patrones",
        f"categoria=eq.{categoria}&descripcion=eq.{requests.utils.quote(descripcion)}&activo=eq.true"
    )
    
    if existing:
        # Actualizar contador
        patron_id = existing[0]["id"]
        supabase_patch("patrones", "id", patron_id, {
            "ultima_actualizacion": today,
            "veces_detectado": existing[0]["veces_detectado"] + 1,
            "severidad": severidad
        })
    else:
        # Crear nuevo patrón
        supabase_post("patrones", {
            "categoria": categoria,
            "descripcion": descripcion,
            "primera_deteccion": today,
            "ultima_actualizacion": today,
            "activo": True,
            "severidad": severidad,
            "veces_detectado": 1
        })

# === ANÁLISIS CON CLAUDE ===

def analizar_con_claude(wellness_30d, activities_7d, events, memoria, recovery_resultado=None, recovery_bloque="", umbrales=None, proxima_carrera=None):
    """Llama a Claude con datos frescos + recovery score + memoria histórica"""

    DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
    n = ahora()
    today = f"{DIAS[n.weekday()]} {n.day} de {MESES[n.month - 1]} {n.year}"
    today_str = hoy_str()

    wellness_14d = wellness_30d[-14:] if len(wellness_30d) >= 14 else wellness_30d
    wellness_hoy = wellness_14d[-1] if wellness_14d else {}
    wellness_ayer = wellness_14d[-2] if len(wellness_14d) >= 2 else {}

    actividades_hoy = [a for a in activities_7d if actividad_es_hoy(a, today_str)]
    tss_dia = sum(carga(a) for a in actividades_hoy)

    resp_hoy = respiracion(wellness_hoy)
    spo2_hoy = spo2(wellness_hoy)
    tsb_hoy = tsb(wellness_hoy)
    if proxima_carrera:
        carrera_txt = f"{proxima_carrera['nombre']} ({proxima_carrera['categoria']}) el {proxima_carrera['fecha']} — faltan {proxima_carrera['dias']} días"
    else:
        carrera_txt = "No hay carrera (RACE) en el calendario de Intervals"

    # Promedios históricos desde memoria
    analisis_previos = memoria.get("analisis_recientes", [])
    hrv_historico = [a["hrv"] for a in analisis_previos if a.get("hrv")]
    sueno_historico = [a["horas_sueno"] for a in analisis_previos if a.get("horas_sueno")]
    hrv_promedio = round(sum(hrv_historico) / len(hrv_historico), 1) if hrv_historico else "sin datos"
    sueno_promedio = round(sum(sueno_historico) / len(sueno_historico), 1) if sueno_historico else "sin datos"

    # Recovery promedio últimos 7 días para contexto de Claude
    recovery_30d = memoria.get("recovery_30d", [])
    recovery_7d_scores = [
        r.get("recovery_score") for r in
        sorted(recovery_30d, key=lambda x: x.get("fecha", ""), reverse=True)[:7]
        if r.get("recovery_score") is not None
    ]
    recovery_7d_avg = round(sum(recovery_7d_scores) / len(recovery_7d_scores)) if recovery_7d_scores else None

    prompt = f"""Eres un coach de triatlón experto con memoria de este atleta. Analiza los datos frescos de hoy junto con el historial completo.

FECHA HOY: {today}

## DATOS FRESCOS DE HOY
### Wellness hoy:
{json.dumps(wellness_hoy, indent=2)}

### Wellness ayer:
{json.dumps(wellness_ayer, indent=2)}

### Wellness últimos 14 días (para tendencias):
{json.dumps(wellness_14d, indent=2)}

### Actividades últimos 7 días:
{json.dumps(activities_7d, indent=2)}

### Resumen wellness de hoy (ya interpretado):
- TSB (forma = CTL - ATL): {tsb_hoy}
- Frecuencia respiratoria nocturna: {resp_hoy if resp_hoy else "sin dato"}
- SpO2 nocturna: {spo2_hoy if spo2_hoy else "sin dato"}

### Umbrales ACTUALES del atleta (configuración de Intervals — fuente de verdad):
{texto_umbrales(umbrales or {})}

### Próxima carrera:
{carrera_txt}

### Eventos próximos 60 días (calendario Intervals):
{json.dumps(events, indent=2)}

### Recovery Score de hoy (pre-calculado — inserta este bloque literalmente en la sección Recovery):
{recovery_bloque if recovery_bloque else "Sin datos de recovery disponibles"}

## MEMORIA HISTÓRICA (lo que has observado antes de este atleta)

### Perfil del atleta:
{json.dumps(memoria.get("perfil", {}), indent=2)}

### Últimos 30 análisis diarios (fecha, hrv, sueño, ctl, atl, tsb, banderas):
{json.dumps(memoria.get("analisis_recientes", []), indent=2)}

### Patrones activos detectados previamente:
{json.dumps(memoria.get("patrones_activos", []), indent=2)}

### Carreras recientes:
{json.dumps(memoria.get("carreras_recientes", []), indent=2)}

### Historia del atleta (contexto profundo):
{json.dumps(memoria.get("historia", []), indent=2)}

### Recovery últimos 30 días:
{json.dumps(memoria.get("recovery_30d", []), indent=2)}

### Recovery mes peak — marzo 2026 (referencia: promedio 81%):
{json.dumps(memoria.get("recovery_peak_marzo", []), indent=2)}

## EXÁMENES DE SANGRE (últimos registros)
{json.dumps(memoria.get("examenes_sangre", []), indent=2)}

## TESTS DE RENDIMIENTO (lactato, FTP, etc)
{json.dumps(memoria.get("tests_rendimiento", []), indent=2)}

### Promedios históricos calculados:
- HRV promedio histórico: {hrv_promedio}
- Sueño promedio histórico: {sueno_promedio}h
- Recovery promedio últimos 7 días: {recovery_7d_avg}% (referencia: marzo pico 81%)

## INSTRUCCIONES

Genera DOS cosas:

### PARTE 1: Mensaje para Telegram
Formato Markdown de Telegram (* para negrita, _ para cursiva).
Máximo 450 palabras. Secciones:

1. Saludo con fecha
2. Recovery Score — inserta el bloque pre-calculado tal como viene en "Recovery Score de hoy". No lo reformatees.
3. Forma actual — CTL/ATL/TSB, qué significa HOY para él
4. Recomendación de HOY — concreta y específica (ej: "Z2 45 min máximo", no "entrena suave")
5. Banderas — máximo 3, solo las importantes. Menciona si son patrones repetidos
6. Próxima carrera — si hay una, días que faltan y una frase de contexto. Si no hay, omite esta sección
7. Frase final — corta, auténtica, no cursi

Reglas especiales Recovery:
- Si Recovery <50% por 3+ días consecutivos: el bloque ya incluye la alerta ⚠️, refuérzala mencionando qué acción concreta tomar HOY (sueño, hidratación con sodio, reducir cafeína post-2pm, cena con carbohidratos)
- Si Recovery >70% por primera vez en 5+ días: el bloque ya incluye la celebración 🎉, úsala para validar que es buen día para calidad
- FC reposo óptima histórica del atleta: ~49 bpm | HRV óptimo histórico: ~55ms (logrado en marzo 2026)
- Cuando el recovery difiere mucho del promedio de marzo (81%), mencionarlo como referencia motivacional, no como crítica

Reglas especiales Exámenes de sangre y Tests:
- Si han pasado más de 4 meses desde el último examen de sangre, recuérdalo como bandera
- Si la testosterona del último examen fue menor a 450 ng/dL, es señal de estrés fisiológico alto — menciónalo con contexto de carga de entrenamiento
- Si la vitamina D fue menor a 35, mencionar que puede seguir cayendo en invierno y recomendar suplementación
- Al analizar runs y bici, compara FC, ritmo y potencia contra los "Umbrales ACTUALES" de arriba (vienen de Intervals). Si el último test de rendimiento es más reciente y no calza con Intervals, menciónalo
- Si la frecuencia respiratoria o la SpO2 están fuera de lo normal respecto a días anteriores, inclúyelo como bandera (puede anticipar enfermedad)

Reglas generales: usa datos reales, compara siempre con histórico cuando tengas datos, sé directo.

### PARTE 2: JSON de metadata (para guardar en base de datos)
Responde con este JSON exacto al final, después del mensaje, separado por ---JSON---:

{{
  "banderas": ["bandera1", "bandera2"],
  "recomendacion": "recomendación concreta de una línea",
  "patrones_nuevos": [
    {{"categoria": "hrv", "descripcion": "descripción del patrón", "severidad": "info|warning|critical"}}
  ],
  "patrones_resolver": []
}}

Si no hay banderas, patrones nuevos o a resolver, usa listas vacías [].
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    full_response = message.content[0].text

    # Separar mensaje de metadata
    if "---JSON---" in full_response:
        parts = full_response.split("---JSON---")
        mensaje_telegram = parts[0].strip()
        try:
            metadata = json.loads(parts[1].strip())
        except:
            metadata = {"banderas": [], "recomendacion": "", "patrones_nuevos": [], "patrones_resolver": []}
    else:
        mensaje_telegram = full_response
        metadata = {"banderas": [], "recomendacion": "", "patrones_nuevos": [], "patrones_resolver": []}

    return mensaje_telegram, metadata, actividades_hoy, tss_dia, wellness_hoy

# === MAIN ===

def main():
    n = ahora()
    today_str = hoy_str()
    yesterday_str = (n - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Coach Triatlón - {today_str} {n.strftime('%H:%M')} (Santiago)")
    print("="*40)

    if es_ejecucion_programada():
        if n.hour < HORA_ENVIO:
            print(f"Son las {n.strftime('%H:%M')} en Santiago, antes de las {HORA_ENVIO}am. La otra corrida del cron envía.")
            return
        if ya_enviado_hoy():
            print("Ya se envió el mensaje de hoy. Nada que hacer.")
            return

    print("1. Leyendo datos de Intervals.icu...")
    wellness_30d = get_wellness(days=30)
    activities_7d = get_activities(days=7)
    events = get_events(days=60)
    umbrales = get_umbrales()
    proxima_carrera = get_proxima_carrera()
    print(f"   ✓ Wellness: {len(wellness_30d)} días")
    print(f"   ✓ Actividades: {len(activities_7d)}")
    print(f"   ✓ Eventos próximos: {len(events)}")
    print(f"   ✓ Umbrales: {list(umbrales.keys())}")
    print(f"   ✓ Próxima carrera: {proxima_carrera['nombre'] + ' en ' + str(proxima_carrera['dias']) + ' días' if proxima_carrera else 'ninguna'}")

    print("2. Leyendo memoria histórica de Supabase...")
    memoria = get_memoria()
    for k in ("analisis_recientes", "patrones_activos", "perfil", "carreras_recientes", "historia",
              "recovery_30d", "examenes_sangre", "tests_rendimiento"):
        print(f"   ✓ {k}: {len(memoria.get(k, []))}")

    print("2b. Calculando Recovery Score...")
    actividades_ayer = [a for a in activities_7d if actividad_es_hoy(a, yesterday_str)]
    recovery_resultado = procesar_recovery(wellness_30d, actividades_ayer, memoria["perfil"])
    recovery_bloque = formatear_recovery_bloque(
        recovery_resultado,
        recovery_30d=memoria.get("recovery_30d", []),
        recovery_marzo=memoria.get("recovery_peak_marzo", [])
    )
    print(f"   ✓ Recovery: {recovery_resultado.get('recovery_score', '?')}% {recovery_resultado.get('color_emoji', '')}")

    print("3. Analizando con Claude...")
    mensaje, metadata, actividades_hoy, tss_dia, wellness_hoy = analizar_con_claude(
        wellness_30d, activities_7d, events, memoria, recovery_resultado, recovery_bloque,
        umbrales=umbrales, proxima_carrera=proxima_carrera
    )
    print(f"   ✓ Mensaje generado ({len(mensaje)} chars)")
    print(f"   ✓ Banderas: {metadata.get('banderas', [])}")
    print(f"   ✓ Patrones nuevos: {len(metadata.get('patrones_nuevos', []))}")

    print("4. Enviando a Telegram...")
    if send_telegram(mensaje):
        print("   ✓ Mensaje enviado!")
    else:
        print("   ✗ No se pudo enviar a Telegram")

    print("5. Guardando en Supabase...")
    try:
        guardar_analisis(
            fecha=today_str,
            wellness_hoy=wellness_hoy,
            actividades_hoy=actividades_hoy,
            tss_dia=tss_dia,
            mensaje=mensaje,
            banderas=metadata.get("banderas", []),
            recomendacion=metadata.get("recomendacion", "")
        )
        print("   ✓ Análisis guardado en base de datos")
    except Exception as e:
        print(f"   ⚠ Error guardando análisis: {e}")

    try:
        guardar_recovery(today_str, recovery_resultado)
        print("   ✓ Recovery guardado en base de datos")
    except Exception as e:
        print(f"   ⚠ Error guardando recovery: {e}")

    for patron in metadata.get("patrones_nuevos", []):
        try:
            actualizar_patron(
                patron.get("categoria", "general"),
                patron.get("descripcion", ""),
                patron.get("severidad", "info")
            )
            print(f"   ✓ Patrón guardado: {patron.get('descripcion', '')[:50]}")
        except Exception as e:
            print(f"   ⚠ Error guardando patrón: {e}")

    print("\n✅ Done. El coach habló.")

if __name__ == "__main__":
    main()
