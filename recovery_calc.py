import math


def _filtrar_outliers(valores, pct=0.10):
    if len(valores) <= 4:
        return valores
    n = len(valores)
    corte = max(1, int(n * pct))
    ordenados = sorted(valores)
    return ordenados[corte:-corte]


def _calcular_baselines(wellness_30d, wellness_60d):
    hrv_30 = [w["hrv"] for w in wellness_30d if w.get("hrv")]
    hrv_60 = [w["hrv"] for w in wellness_60d if w.get("hrv")]
    fc_30 = [w["restingHeartRate"] for w in wellness_30d if w.get("restingHeartRate")]
    fc_60 = [w["restingHeartRate"] for w in wellness_60d if w.get("restingHeartRate")]

    hrv_reciente = None
    if hrv_30:
        f = _filtrar_outliers(hrv_30)
        hrv_reciente = sum(f) / len(f) if f else None

    hrv_optimo = None
    if hrv_60:
        n = len(hrv_60)
        corte = max(1, int(n * 0.30))
        hrv_optimo = sum(sorted(hrv_60)[-corte:]) / corte

    fc_reciente = None
    if fc_30:
        f = _filtrar_outliers(fc_30)
        fc_reciente = sum(f) / len(f) if f else None

    fc_optimo = None
    if fc_60:
        n = len(fc_60)
        corte = max(1, int(n * 0.30))
        fc_optimo = sum(sorted(fc_60)[:corte]) / corte

    return {
        "hrv_reciente": hrv_reciente,
        "hrv_optimo": hrv_optimo,
        "fc_reciente": fc_reciente,
        "fc_optimo": fc_optimo,
    }


def _score_hrv(hrv_hoy, baselines, hrv_optimo_perfil=55.0):
    if hrv_hoy is None:
        return 50
    base = baselines.get("hrv_reciente") or (hrv_optimo_perfil * 0.85)
    optimo = baselines.get("hrv_optimo") or hrv_optimo_perfil
    if optimo <= base:
        return min(100, max(0, round((hrv_hoy / optimo) * 100))) if optimo > 0 else 50
    return min(100, max(0, round(((hrv_hoy - base) / (optimo - base)) * 100)))


def _score_fc_reposo(fc_hoy, baselines, fc_optima_perfil=49.0):
    if fc_hoy is None:
        return 50
    optimo = baselines.get("fc_optimo") or fc_optima_perfil
    if fc_hoy <= optimo:
        return 100
    return min(100, max(0, round(100 - (fc_hoy - optimo) * 5)))


def _get_horas_sueno(wellness_hoy):
    if wellness_hoy.get("sleepSecs"):
        return wellness_hoy["sleepSecs"] / 3600
    if wellness_hoy.get("sleepHours"):
        try:
            return float(wellness_hoy["sleepHours"])
        except (TypeError, ValueError):
            pass
    return None


def _score_sueno(wellness_hoy):
    horas = _get_horas_sueno(wellness_hoy)
    if horas is None:
        return 50
    return min(100, max(0, round((horas / 8.0) * 100)))


def _categoria_recovery(score):
    if score >= 67:
        return "verde", "🟢"
    if score >= 34:
        return "amarillo", "🟡"
    return "rojo", "🔴"


def _calcular_day_strain(actividades):
    tss_total = sum(a.get("training_load", 0) or 0 for a in actividades)
    strain_base = 4 * math.log(tss_total + 1) - 2

    suma_fc_t = total_t = 0
    for a in actividades:
        fc = a.get("average_heartrate", 0) or 0
        t = a.get("moving_time", 0) or 0
        if fc > 0 and t > 0:
            suma_fc_t += fc * t
            total_t += t

    ajuste = 0.0
    if total_t > 0:
        fc_avg = suma_fc_t / total_t
        if fc_avg > 165:
            ajuste = 2.0
        elif fc_avg > 150:
            ajuste = 1.0
        elif fc_avg > 135:
            ajuste = 0.5

    return max(0.0, min(21.0, round(strain_base + ajuste, 1)))


def _insight_accionable(hrv_score, fc_score, sleep_score, hrv_hoy, fc_hoy, horas_sueno):
    scores = {"hrv": hrv_score, "fc": fc_score, "sleep": sleep_score}
    debil = min(scores, key=scores.get)
    if debil == "hrv":
        if hrv_hoy and hrv_hoy < 40:
            return "HRV muy bajo: sistema nervioso bajo estrés. Prioriza Z1 o descanso activo hoy."
        return "HRV es tu componente más débil hoy. Mantén intensidad bajo umbral si hay sesión."
    if debil == "fc":
        if fc_hoy and fc_hoy > 55:
            return f"FC reposo elevada ({fc_hoy}bpm): hidratación con sodio antes de entrenar."
        return "FC reposo sobre óptimo. Hidrata bien y evalúa si la carga planificada es adecuada."
    h = f"{round(horas_sueno, 1)}h" if horas_sueno else "?"
    if horas_sueno and horas_sueno < 6:
        return f"Solo {h} de sueño: recuperación comprometida. Reduce intensidad y duerme siesta si puedes."
    return f"Sueño ({h}) por debajo del óptimo. Acuéstate 30min antes esta noche."


def detectar_alertas_recovery(recovery_score, recovery_30d):
    """Retorna (alerta_baja, celebracion) como strings o None."""
    alerta_baja = None
    celebracion = None
    if not recovery_30d:
        return alerta_baja, celebracion

    recientes = sorted(recovery_30d, key=lambda x: x.get("fecha", ""), reverse=True)

    # Recovery <50% por 3+ días consecutivos (más el de hoy = 4+)
    if recovery_score < 50 and len(recientes) >= 3:
        ultimos_3 = [r.get("recovery_score", 100) for r in recientes[:3]]
        if all(s < 50 for s in ultimos_3):
            alerta_baja = (
                "⚠️ *Recovery bajo 4+ días seguidos*: Prioriza sueño 8h+, "
                "hidratación con sodio, reduce cafeína después de las 2pm y cena con carbohidratos. "
                "Considera reducir intensidad hoy."
            )

    # Recovery >70% por primera vez en 5+ días
    if recovery_score > 70 and len(recientes) >= 5:
        ultimos_5 = [r.get("recovery_score", 100) for r in recientes[:5]]
        if all(s <= 70 for s in ultimos_5):
            celebracion = (
                "🎉 *Primera vez en 5+ días con Recovery alto* — "
                "Tu cuerpo recuperó bien. Hoy es buen día para calidad."
            )

    return alerta_baja, celebracion


def procesar_recovery(wellness_data, actividades_ayer, perfil):
    """
    Calcula Recovery Score y Day Strain estilo Whoop.

    wellness_data: lista cronológica (más antigua primero), idealmente 30+ días.
    actividades_ayer: actividades del día anterior (para Day Strain).
    perfil: dict del atleta (claves opcionales: hrv_optimo, fc_reposo_optima).

    Retorna dict con recovery_score, scores individuales, baselines y day_strain.
    """
    if not wellness_data:
        return {"error": "Sin datos de wellness"}

    wellness_hoy = wellness_data[-1]
    wellness_30d = wellness_data[-30:] if len(wellness_data) >= 30 else wellness_data
    baselines = _calcular_baselines(wellness_30d, wellness_data)

    hrv_hoy = wellness_hoy.get("hrv")
    fc_hoy = wellness_hoy.get("restingHeartRate")
    horas_sueno = _get_horas_sueno(wellness_hoy)

    hrv_optimo_perfil = float(perfil.get("hrv_optimo", 55))
    fc_optima_perfil = float(perfil.get("fc_reposo_optima", 49))

    hrv_score = _score_hrv(hrv_hoy, baselines, hrv_optimo_perfil)
    fc_score = _score_fc_reposo(fc_hoy, baselines, fc_optima_perfil)
    sleep_score = _score_sueno(wellness_hoy)

    recovery_score = min(100, max(0, round(hrv_score * 0.70 + fc_score * 0.20 + sleep_score * 0.10)))
    categoria, color_emoji = _categoria_recovery(recovery_score)
    day_strain = _calcular_day_strain(actividades_ayer)

    return {
        "recovery_score": recovery_score,
        "recovery_color": categoria,
        "color_emoji": color_emoji,
        "hrv_hoy": hrv_hoy,
        "hrv_baseline": baselines.get("hrv_reciente"),
        "hrv_score": hrv_score,
        "fc_reposo_hoy": fc_hoy,
        "fc_reposo_baseline": baselines.get("fc_optimo"),
        "fc_reposo_score": fc_score,
        "horas_sueno": horas_sueno,
        "sleep_score": sleep_score,
        "day_strain": day_strain,
    }


def formatear_recovery_bloque(resultado, recovery_30d=None, recovery_marzo=None):
    """Genera el bloque Recovery formateado para insertar en el mensaje de Telegram."""
    if "error" in resultado:
        return "Recovery: sin datos suficientes"

    rs = resultado["recovery_score"]
    emoji = resultado["color_emoji"]
    cat = resultado["recovery_color"]
    hrv_hoy = resultado["hrv_hoy"]
    hrv_base = resultado["hrv_baseline"]
    hrv_score = resultado["hrv_score"]
    fc_hoy = resultado["fc_reposo_hoy"]
    fc_base = resultado["fc_reposo_baseline"]
    fc_score = resultado["fc_reposo_score"]
    horas = resultado["horas_sueno"]
    sleep_score = resultado["sleep_score"]
    day_strain = resultado["day_strain"]

    hrv_txt = f"{hrv_hoy}ms" if hrv_hoy else "sin dato"
    hrv_base_txt = f"{round(hrv_base, 1)}ms" if hrv_base else "sin base"
    fc_txt = f"{fc_hoy}bpm" if fc_hoy else "sin dato"
    fc_base_txt = f"{round(fc_base, 1)}bpm" if fc_base else "sin base"
    sueno_txt = f"{round(horas, 1)}h" if horas else "sin dato"

    comp_7d = ""
    if recovery_30d:
        recientes = sorted(recovery_30d, key=lambda x: x.get("fecha", ""), reverse=True)
        scores_7 = [r.get("recovery_score") for r in recientes[:7] if r.get("recovery_score") is not None]
        if scores_7:
            comp_7d = f"7d: {round(sum(scores_7) / len(scores_7))}%"

    comp_marzo = ""
    if recovery_marzo:
        scores_m = [r.get("recovery_score") for r in recovery_marzo if r.get("recovery_score") is not None]
        if scores_m:
            comp_marzo = f"marzo pico: {round(sum(scores_m) / len(scores_m))}%"
    else:
        comp_marzo = "marzo pico: 81%"

    comparaciones = " | ".join(filter(None, [comp_7d, comp_marzo]))

    insight = _insight_accionable(hrv_score, fc_score, sleep_score, hrv_hoy, fc_hoy, horas)
    alerta_baja, celebracion = detectar_alertas_recovery(rs, recovery_30d or [])

    lineas = [
        f"*Recovery Score: {emoji} {rs}% ({cat})*",
        f"├ HRV: {hrv_txt} (base {hrv_base_txt}) → {hrv_score}%",
        f"├ FC reposo: {fc_txt} (óptimo {fc_base_txt}) → {fc_score}%",
        f"└ Sueño: {sueno_txt} → {sleep_score}%",
        f"Day Strain ayer: {day_strain}/21" + (f" | {comparaciones}" if comparaciones else ""),
        f"💡 {insight}",
    ]
    if alerta_baja:
        lineas.append(alerta_baja)
    if celebracion:
        lineas.append(celebracion)

    return "\n".join(lineas)
