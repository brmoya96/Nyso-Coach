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

client = Anthropic(api_key=ANTHROPIC_API_KEY)

def intervals_get(endpoint):
    """Llamada autenticada a la API de Intervals.icu"""
    credentials = base64.b64encode(f"API_KEY:{INTERVALS_API_KEY}".encode()).decode()
    response = requests.get(
        f"https://intervals.icu/api/v1/athlete/{INTERVALS_ATHLETE_ID}/{endpoint}",
        headers={"Authorization": f"Basic {credentials}"}
    )
    response.raise_for_status()
    return response.json()

def get_wellness_last_7_days():
    """Obtiene datos de wellness de los últimos 7 días"""
    today = datetime.now()
    oldest = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    return intervals_get(f"wellness?oldest={oldest}&newest={newest}")

def get_activities_last_7_days():
    """Obtiene actividades de los últimos 7 días"""
    today = datetime.now()
    oldest = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    return intervals_get(f"activities?oldest={oldest}&newest={newest}")

def get_fitness_last_90_days():
    """Obtiene CTL/ATL/TSB de los últimos 90 días"""
    today = datetime.now()
    oldest = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    newest = today.strftime("%Y-%m-%d")
    data = intervals_get(f"wellness?oldest={oldest}&newest={newest}")
    # Retornar solo los últimos 14 días para no sobrecargar el prompt
    return data[-14:] if len(data) > 14 else data

def get_events_next_60_days():
    """Obtiene eventos/carreras planificadas en los próximos 60 días"""
    today = datetime.now()
    oldest = today.strftime("%Y-%m-%d")
    newest = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        return intervals_get(f"events?oldest={oldest}&newest={newest}")
    except:
        return []

def send_telegram(message):
    """Envía mensaje a Telegram con formato Markdown"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram tiene límite de 4096 chars por mensaje
    # Si es más largo, cortamos en dos
    if len(message) <= 4096:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        })
    else:
        # Dividir en dos mensajes
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

def analizar_con_claude(wellness_7d, activities_7d, fitness_14d, events):
    """Llama a Claude con todos los datos y obtiene el análisis"""
    
    today = datetime.now().strftime("%A %d de %B %Y")
    
    # Preparar datos de hoy y ayer
    wellness_hoy = wellness_7d[-1] if wellness_7d else {}
    wellness_ayer = wellness_7d[-2] if len(wellness_7d) >= 2 else {}
    
    # Actividades de hoy
    today_str = datetime.now().strftime("%Y-%m-%d")
    actividades_hoy = [a for a in activities_7d if a.get("start_date_local", "").startswith(today_str)]
    actividades_semana = activities_7d
    
    prompt = f"""Eres un coach de triatlón experto. Analiza los siguientes datos del atleta y genera un mensaje de coaching para Telegram.

FECHA HOY: {today}

## WELLNESS HOY
{json.dumps(wellness_hoy, indent=2)}

## WELLNESS AYER
{json.dumps(wellness_ayer, indent=2)}

## WELLNESS ÚLTIMOS 7 DÍAS
{json.dumps(wellness_7d, indent=2)}

## FITNESS ÚLTIMOS 14 DÍAS (CTL/ATL/TSB via ctl, atl, tsb fields)
{json.dumps(fitness_14d, indent=2)}

## ACTIVIDADES ÚLTIMOS 7 DÍAS
{json.dumps(actividades_semana, indent=2)}

## PRÓXIMAS CARRERAS (60 días)
{json.dumps(events, indent=2)}

## INSTRUCCIONES PARA EL MENSAJE

Genera un mensaje de coaching en español para Telegram. Usa emojis y formato Markdown de Telegram (* para negrita, _ para cursiva).

El mensaje debe tener estas secciones en orden:

1. **Saludo** con fecha
2. **Sueño de anoche** — horas, score, calidad, comparación con promedio semanal
3. **HRV y FC reposo** — valor de hoy, tendencia últimos 3-5 días, interpretación
4. **Estado de forma** — CTL, ATL, TSB de hoy, qué significa para el atleta
5. **Semana en resumen** — qué entrenó, carga acumulada, distribución por deporte
6. **Recomendación para HOY** — basada en todos los datos, concreta y accionable
7. **Banderas** — máximo 3, solo si hay algo que realmente amerite atención. Si todo está bien, di que está bien.
8. **Próxima carrera** — si hay una en los próximos 60 días, cuántos días faltan y una frase de contexto

Reglas:
- Sé directo y concreto, nada de frases genéricas
- Si el HRV está bien, dilo. Si está mal, dilo claro
- Las recomendaciones deben ser específicas (ej: "Z2 45 min máximo" no "entrena suave")
- Máximo 400 palabras en total
- Usa los datos reales, no inventes valores
- Si no hay dato para algo (null), omite esa métrica sin mencionar que falta
- Termina siempre con una frase motivacional corta y auténtica, no cursi
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text

def main():
    print("Obteniendo datos de Intervals.icu...")
    
    wellness_7d = get_wellness_last_7_days()
    print(f"  ✓ Wellness: {len(wellness_7d)} días")
    
    activities_7d = get_activities_last_7_days()
    print(f"  ✓ Actividades: {len(activities_7d)} actividades")
    
    fitness_14d = get_fitness_last_90_days()
    print(f"  ✓ Fitness: {len(fitness_14d)} días")
    
    events = get_events_next_60_days()
    print(f"  ✓ Eventos próximos: {len(events)}")
    
    print("Analizando con Claude...")
    mensaje = analizar_con_claude(wellness_7d, activities_7d, fitness_14d, events)
    print(f"  ✓ Análisis generado ({len(mensaje)} chars)")
    
    print("Enviando a Telegram...")
    send_telegram(mensaje)
    print("  ✓ Mensaje enviado!")
    
    print("\nDone. El coach habló.")

if __name__ == "__main__":
    main()
