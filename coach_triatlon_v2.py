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

def get_events_next_60_days():
    today = datetime.now()
    oldest = today.strftime("%Y-%m-%d")
    newest = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        return intervals_get(f"events?oldest={oldest}&newest={newest}")
    except:
        return []

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

def supabase_patch(table, match_field, match_value, data):
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_field}=eq.{match_value}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        },
        json=data
    )
    response.raise_for_status()
    return response

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
    try:
        perfil_raw = supabase_get("perfil_atleta", "")
        memoria["perfil"] = {r["clave"]: r["valor"] for r in perfil_raw if r.get("valor")}
    except:
        memoria["perfil"] = {}

    # Últimas 5 carreras
    try:
        memoria["carreras_recientes"] = supabase_get(
            "carreras",
            "order=fecha.desc&limit=5"
        )
    except:
        memoria["carreras_recientes"] = []

    return memoria

def guardar_analisis(fecha, wellness_hoy, actividades_hoy, tss_dia, mensaje, banderas, recomendacion):
    """Guarda el análisis de hoy en Supabase"""
    data = {
        "fecha": fecha,
        "hrv": wellness_hoy.get("hrv"),
        "fc_reposo": wellness_hoy.get("restingHeartRate"),
        "horas_sueno": wellness_hoy.get("sleepSecs", 0) / 3600 if wellness_hoy.get("sleepSecs") else wellness_hoy.get("sleepHours"),
        "sleep_score": wellness_hoy.get("sleepScore"),
        "ctl": wellness_hoy.get("ctl"),
        "atl": wellness_hoy.get("atl"),
        "tsb": wellness_hoy.get("tsb"),
        "actividades_hoy": json.dumps(actividades_hoy),
        "tss_dia": tss_dia,
        "mensaje_telegram": mensaje,
        "banderas": json.dumps(banderas),
        "recomendacion": recomendacion
    }
    # Eliminar nulos para no sobreescribir con null
    data = {k: v for k, v in data.items() if v is not None}
    supabase_post("analisis_diarios", data)

def actualizar_patron(categoria, descripcion, severidad="info"):
    """Crea o actualiza un patrón detectado"""
    today = datetime.now().strftime("%Y-%m-%d")
    
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

def analizar_con_claude(wellness_14d, activities_7d, events, memoria):
    """Llama a Claude con datos frescos + memoria histórica"""

    today = datetime.now().strftime("%A %d de %B %Y")
    today_str = datetime.now().strftime("%Y-%m-%d")

    wellness_hoy = wellness_14d[-1] if wellness_14d else {}
    wellness_ayer = wellness_14d[-2] if len(wellness_14d) >= 2 else {}

    actividades_hoy = [
        a for a in activities_7d
        if a.get("start_date_local", "").startswith(today_str)
    ]
    tss_dia = sum(a.get("training_load", 0) or 0 for a in actividades_hoy)

    # Calcular promedios históricos desde memoria
    analisis_previos = memoria.get("analisis_recientes", [])
    hrv_historico = [a["hrv"] for a in analisis_previos if a.get("hrv")]
    sueno_historico = [a["horas_sueno"] for a in analisis_previos if a.get("horas_sueno")]
    hrv_promedio = round(sum(hrv_historico) / len(hrv_historico), 1) if hrv_historico else "sin datos"
    sueno_promedio = round(sum(sueno_historico) / len(sueno_historico), 1) if sueno_historico else "sin datos"

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

### Próximas carreras (60 días):
{json.dumps(events, indent=2)}

## MEMORIA HISTÓRICA (lo que has observado antes de este atleta)

### Perfil del atleta:
{json.dumps(memoria.get("perfil", {}), indent=2)}

### Últimos 30 análisis diarios (fecha, hrv, sueño, ctl, atl, tsb, banderas):
{json.dumps(memoria.get("analisis_recientes", []), indent=2)}

### Patrones activos detectados previamente:
{json.dumps(memoria.get("patrones_activos", []), indent=2)}

### Carreras recientes:
{json.dumps(memoria.get("carreras_recientes", []), indent=2)}

### Promedios históricos calculados:
- HRV promedio histórico: {hrv_promedio}
- Sueño promedio histórico: {sueno_promedio}h

## INSTRUCCIONES

Genera DOS cosas:

### PARTE 1: Mensaje para Telegram
Formato Markdown de Telegram (* para negrita, _ para cursiva).
Máximo 400 palabras. Secciones:

1. Saludo con fecha
2. Sueño — horas, score, comparación con su promedio histórico ({sueno_promedio}h)
3. HRV y FC reposo — valor hoy vs promedio histórico ({hrv_promedio}), tendencia últimos 5 días
4. Forma actual — CTL/ATL/TSB, qué significa HOY para él
5. Recomendación de HOY — concreta y específica (ej: "Z2 45 min máximo", no "entrena suave")
6. Banderas — máximo 3, solo las importantes. Menciona si son patrones repetidos (ej: "tercera semana con HRV bajo")
7. Próxima carrera — si hay una, días que faltan y una frase de contexto
8. Frase final — corta, auténtica, no cursi

Reglas: usa datos reales, compara siempre con histórico cuando tengas datos, sé directo.

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
    print(f"Coach Triatlón - {today_str}")
    print("="*40)

    print("1. Leyendo datos de Intervals.icu...")
    wellness_14d = get_wellness(days=14)
    activities_7d = get_activities(days=7)
    events = get_events_next_60_days()
    print(f"   ✓ Wellness: {len(wellness_14d)} días")
    print(f"   ✓ Actividades: {len(activities_7d)}")
    print(f"   ✓ Eventos próximos: {len(events)}")

    print("2. Leyendo memoria histórica de Supabase...")
    memoria = get_memoria()
    print(f"   ✓ Análisis previos: {len(memoria['analisis_recientes'])}")
    print(f"   ✓ Patrones activos: {len(memoria['patrones_activos'])}")
    print(f"   ✓ Perfil: {len(memoria['perfil'])} campos")
    print(f"   ✓ Carreras: {len(memoria['carreras_recientes'])}")

    print("3. Analizando con Claude...")
    mensaje, metadata, actividades_hoy, tss_dia, wellness_hoy = analizar_con_claude(
        wellness_14d, activities_7d, events, memoria
    )
    print(f"   ✓ Mensaje generado ({len(mensaje)} chars)")
    print(f"   ✓ Banderas: {metadata.get('banderas', [])}")
    print(f"   ✓ Patrones nuevos: {len(metadata.get('patrones_nuevos', []))}")

    print("4. Guardando en Supabase...")
    guardar_analisis(
        fecha=today_str,
        wellness_hoy=wellness_hoy,
        actividades_hoy=actividades_hoy,
        tss_dia=tss_dia,
        mensaje=mensaje,
        banderas=metadata.get("banderas", []),
        recomendacion=metadata.get("recomendacion", "")
    )
    # Guardar patrones nuevos detectados
    for patron in metadata.get("patrones_nuevos", []):
        try:
            actualizar_patron(
                patron.get("categoria", "general"),
                patron.get("descripcion", ""),
                patron.get("severidad", "info")
            )
        except Exception as e:
            print(f"   ⚠ Error guardando patrón: {e}")
    print("   ✓ Guardado en base de datos")

    print("5. Enviando a Telegram...")
    send_telegram(mensaje)
    print("   ✓ Mensaje enviado!")

    print("\n✅ Done. El coach habló.")

if __name__ == "__main__":
    main()
