# Coach Triatlón 🏊🚴🏃

Agente de coaching diario para triatletas. Cada mañana a las 5:50am (Santiago) lee tus datos de Intervals.icu, los analiza con Claude AI, y te manda un resumen personalizado a Telegram.

## Qué hace

- Lee tu sueño, HRV y FC reposo de la noche anterior
- Revisa tu CTL, ATL y TSB actuales
- Analiza tus entrenamientos de los últimos 7 días
- Detecta banderas (sobreentrenamiento, sueño deficiente, HRV bajo)
- Recomienda qué hacer hoy específicamente
- Te avisa cuántos días faltan para tu próxima carrera

## Setup (una sola vez)

### 1. Fork este repositorio
Click en "Fork" arriba a la derecha en GitHub.

### 2. Agregar los secrets
Ve a tu repositorio → Settings → Secrets and variables → Actions → New repository secret

Agrega estos 7 secrets:

| Secret | Valor |
|--------|-------|
| `INTERVALS_ATHLETE_ID` | Tu athlete ID de Intervals.icu (ej: i259597) |
| `INTERVALS_API_KEY` | Tu API key de Intervals.icu |
| `TELEGRAM_BOT_TOKEN` | Token de tu bot (de @BotFather) |
| `TELEGRAM_CHAT_ID` | Tu Chat ID (de @userinfobot) |
| `ANTHROPIC_API_KEY` | Tu API key de console.anthropic.com |
| `SUPABASE_URL` | URL del proyecto Supabase |
| `SUPABASE_KEY` | Service key de Supabase |

### 3. Habilitar GitHub Actions
Ve a la pestaña "Actions" en tu repositorio y click "Enable Actions" si aparece.

### 4. Probar manualmente
Ve a Actions → Coach Triatlón - Mensaje Diario → Run workflow → Run workflow

Si todo está bien, en 1-2 minutos te llega el mensaje en Telegram.

### 5. Listo
Desde ese momento corre automático cada mañana a las 5:50am. No necesitas hacer nada.

## Horario

GitHub Actions solo entiende UTC, así que cada workflow corre en los dos horarios posibles (verano UTC-3 e invierno UTC-4) y el script decide en hora de Santiago:
- **Mensaje diario:** 5:50am Santiago, una vez al día.
- **Alertas:** cada hora entre 6:00 y 22:59 Santiago (máx. 2 alertas/día + hasta 4 análisis post-entreno).
- **Resumen semanal:** domingo 20:00 Santiago.

No hay que tocar los cron al cambiar la hora en Chile.

## Fuentes de datos

- **Intervals.icu** es la única fuente de wellness, actividades, umbrales (FTP, LTHR, ritmo umbral, zonas FC) y calendario de carreras (eventos `RACE_A/B/C`).
- Para actualizar umbrales después de un test: cámbialos en Intervals → Settings. Los scripts los leen solos.
- Objetivo de CTL opcional: fila `ctl_objetivo` en la tabla `perfil_atleta` de Supabase.

## Costo estimado

- GitHub Actions: **gratis** (usa ~2 min/día de los 2000 min/mes gratis)
- Telegram: **gratis**
- Intervals.icu API: **gratis**
- Anthropic API: **~$1-3 USD/mes** con Claude Haiku

## Escalamiento futuro

Este repositorio está diseñado para crecer:
- [ ] Análisis post-entreno (trigger cuando llega actividad nueva)
- [ ] Alerta inmediata si HRV cae mucho
- [ ] Resumen semanal los domingos
- [ ] Protocolo pre-carrera automático
- [ ] Dashboard web
