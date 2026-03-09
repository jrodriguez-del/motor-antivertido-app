"""
Módulo 1: Cliente API PVGIS v5.3 + Corrección CAMS + Interpolador Solar
========================================================================
Obtiene la curva de generación teórica de PVGIS y la corrige con
irradiancia real de CAMS cuando el año de los datos difiere del año PVGIS.

Flujo para Ruta A (FusionSolar):
1. PVGIS: G_TEO del año más cercano al año real de datos
2. CAMS: GHI del año real + GHI del año PVGIS (si difieren)
3. Escalar: G_TEO_corregida[h] = G_TEO_pvgis[h] × (GHI_real[h] / GHI_pvgis[h])
4. Upsample a cuartohoraria con posición solar real
"""

import requests
import logging
from typing import List, Optional, Dict, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"


def obtener_curva_pvgis(
    lat: float,
    lon: float,
    peakpower: float,
    loss: float = 14.0,
    angle: Optional[float] = None,
    aspect: float = 0.0,
    pvtechchoice: str = "crystSi",
    mountingplace: str = "building",
    startyear: Optional[int] = None,
    endyear: Optional[int] = None,
) -> Tuple[List[float], dict]:
    """
    Llama a la API PVGIS v5.3 (seriescalc).

    Si startyear==endyear: devuelve ese año individual.
    Si no: promedia todos los años (TMY-like).

    Returns:
        (g_horario, info): lista 8.760 kWh/h + info de debug
    """
    params = {
        "lat": lat,
        "lon": lon,
        "peakpower": peakpower,
        "loss": loss,
        "aspect": aspect,
        "pvtechchoice": pvtechchoice,
        "mountingplace": mountingplace,
        "pvcalculation": 1,
        "outputformat": "json",
        "raddatabase": "PVGIS-SARAH3",
    }

    if angle is not None:
        params["angle"] = angle
    else:
        params["optimalangles"] = 1

    if startyear is not None:
        params["startyear"] = startyear
        params["endyear"] = endyear or startyear

    response = requests.get(PVGIS_URL, params=params, timeout=120)
    response.raise_for_status()

    data = response.json()
    hourly_data = data["outputs"]["hourly"]

    # Agrupar por (mes, día, hora) y promediar
    acumulador = defaultdict(list)
    anios = set()

    for entry in hourly_data:
        time_str = entry["time"]
        year = int(time_str[:4])
        month = int(time_str[4:6])
        day = int(time_str[6:8])
        hour = int(time_str[9:11])
        minute = int(time_str[11:13])

        anios.add(year)
        clave = (month, day, hour, minute)
        p_kwh = max(0.0, entry["P"] / 1000.0)
        acumulador[clave].append(p_kwh)

    claves_ordenadas = sorted(acumulador.keys())
    g_horario_promedio = []
    for clave in claves_ordenadas:
        valores = acumulador[clave]
        g_horario_promedio.append(sum(valores) / len(valores))

    total_anual = sum(g_horario_promedio)
    anios_sorted = sorted(anios)

    info = {
        "registros_pvgis": len(hourly_data),
        "anios_disponibles": f"{min(anios_sorted)}-{max(anios_sorted)}",
        "num_anios": len(anios_sorted),
        "horas_promediadas": len(g_horario_promedio),
        "total_anual_kwh": round(total_anual, 1),
        "kwh_kwp": round(total_anual / peakpower, 1) if peakpower > 0 else 0,
        "anios_lista": anios_sorted,
    }

    return g_horario_promedio, info


# ═══════════════════════════════════════════════════
# Corrección con CAMS
# ═══════════════════════════════════════════════════

def _obtener_ghi_anual_cams(
    lat: float, lon: float,
    anio: int,
    email: str = "jrodriguez@cavoenergias.com",
) -> List[float]:
    """
    Obtiene la GHI horaria de CAMS para un año completo.
    Returns: lista de ~8760 valores GHI (Wh/m²).
    """
    from cams_client import obtener_irradiancia_cams
    from datetime import datetime

    _, ghi_real, _ = obtener_irradiancia_cams(
        lat=lat, lon=lon,
        fecha_inicio=datetime(anio, 1, 1),
        fecha_fin=datetime(anio, 12, 31),
        email=email,
    )
    return ghi_real


def corregir_gteo_con_cams(
    g_teo_h: List[float],
    anio_real: int,
    anio_pvgis: int,
    lat: float,
    lon: float,
    email: str = "jrodriguez@cavoenergias.com",
) -> Tuple[List[float], dict]:
    """
    Corrige la curva G_TEO de PVGIS escalándola con el ratio de
    irradiancia CAMS entre el año real y el año PVGIS.

    G_TEO_corregida[h] = G_TEO_pvgis[h] × (GHI_real[h] / GHI_pvgis[h])

    Args:
        g_teo_h: curva horaria G_TEO de PVGIS (8.760 valores)
        anio_real: año de los datos reales (FusionSolar/CSV)
        anio_pvgis: año de la curva PVGIS usada
        lat, lon: coordenadas

    Returns:
        (g_teo_corregida, info_correccion)
    """
    if anio_real == anio_pvgis:
        return g_teo_h, {"correccion": "No necesaria (mismo año)"}

    logger.info(
        f"Corrigiendo G_TEO: PVGIS {anio_pvgis} → datos {anio_real} "
        f"vía CAMS ratio"
    )

    # Obtener GHI de ambos años
    ghi_real = _obtener_ghi_anual_cams(lat, lon, anio_real, email)
    ghi_pvgis = _obtener_ghi_anual_cams(lat, lon, anio_pvgis, email)

    # Alinear longitudes
    n = min(len(g_teo_h), len(ghi_real), len(ghi_pvgis))

    g_teo_corregida = []
    n_escalados = 0
    n_sin_cambio = 0

    for i in range(n):
        if ghi_pvgis[i] > 10 and g_teo_h[i] > 0:
            # Escalar por ratio de irradiancia
            ratio = ghi_real[i] / ghi_pvgis[i]
            # Capear ratio entre 0 y 3 para evitar valores extremos
            ratio = max(0.0, min(3.0, ratio))
            g_teo_corregida.append(g_teo_h[i] * ratio)
            n_escalados += 1
        else:
            # Noche o sin irradiancia en PVGIS: mantener tal cual
            g_teo_corregida.append(g_teo_h[i])
            if g_teo_h[i] > 0:
                n_sin_cambio += 1

    # Rellenar si hay diferencia de longitud
    while len(g_teo_corregida) < len(g_teo_h):
        g_teo_corregida.append(g_teo_h[len(g_teo_corregida)])

    total_original = sum(g_teo_h)
    total_corregida = sum(g_teo_corregida)
    ghi_real_total = sum(ghi_real[:n])
    ghi_pvgis_total = sum(ghi_pvgis[:n])

    info = {
        "correccion": "Aplicada",
        "anio_real": anio_real,
        "anio_pvgis": anio_pvgis,
        "ghi_real_total_kwh_m2": round(ghi_real_total / 1000, 1),
        "ghi_pvgis_total_kwh_m2": round(ghi_pvgis_total / 1000, 1),
        "ratio_ghi_global": round(ghi_real_total / max(1, ghi_pvgis_total), 3),
        "gteo_original_kwh": round(total_original, 1),
        "gteo_corregida_kwh": round(total_corregida, 1),
        "diferencia_kwh": round(total_corregida - total_original, 1),
        "diferencia_pct": round((total_corregida - total_original) / max(1, total_original) * 100, 1),
        "horas_escaladas": n_escalados,
    }

    return g_teo_corregida, info


def calibrar_gteo_con_fv_real(
    g_teo_h: List[float],
    fv_real_h: List[float],
    timestamps_h,
    umbral_kwh: float = 1.0,
) -> Tuple[List[float], dict]:
    """
    Calibra G_TEO horaria con doble ponderación por (mes, hora del día).

    Para cada (mes m, hora h):
      ratio_exc  = Σ FV_REAL / Σ G_TEO  en días donde FV_REAL > G_TEO a esa hora
      peso_freq  = n_dias_exceso / n_dias_solares  (¿es común o puntual?)
      peso_ener  = Σ FV_REAL_exceso / Σ G_TEO_exceso  a esa (m,h)
                   (cuánta energía representan esos excesos)
      factor[m,h] = 1 + peso_freq × peso_ener × (ratio_exc - 1)

    Paso final: G_TEO[i] = max(G_TEO_cal[i], FV_REAL[i])

    Returns:
        (g_teo_calibrada_h, info)
    """
    from collections import defaultdict

    n = min(len(g_teo_h), len(fv_real_h), len(timestamps_h))

    # Paso 1: Agrupar por (mes, hora_del_dia)
    # Clave = (mes, hora)
    n_solar = defaultdict(int)       # horas solares con G_TEO >= umbral
    n_exceso = defaultdict(int)      # horas con FV > G_TEO
    fv_exceso = defaultdict(float)   # Σ FV_REAL en horas exceso
    gteo_exceso = defaultdict(float) # Σ G_TEO en horas exceso
    gteo_total_hora = defaultdict(float)  # G_TEO total por (mes, hora)

    for i in range(n):
        mes = timestamps_h[i].month
        hora = timestamps_h[i].hour
        clave = (mes, hora)

        if g_teo_h[i] >= umbral_kwh:
            n_solar[clave] += 1
            gteo_total_hora[clave] += g_teo_h[i]

            if fv_real_h[i] > g_teo_h[i]:
                n_exceso[clave] += 1
                fv_exceso[clave] += fv_real_h[i]
                gteo_exceso[clave] += g_teo_h[i]

    if not n_exceso:
        return list(g_teo_h[:n]), {"calibracion": "No necesaria (sin excesos)"}

    # Paso 2: Calcular factor por (mes, hora)
    factores = {}   # (mes, hora) → factor
    detalles = {}

    for clave in n_solar:
        mes, hora = clave
        if clave in n_exceso and n_solar[clave] > 0 and gteo_total_hora[clave] > 0:
            ratio_exc = fv_exceso[clave] / gteo_exceso[clave]
            peso_freq = n_exceso[clave] / n_solar[clave]
            peso_ener = fv_exceso[clave] / gteo_total_hora[clave]
            factor = 1.0 + peso_freq * peso_ener * (ratio_exc - 1.0)
            factores[clave] = factor
            detalles[clave] = {
                "ratio": round(ratio_exc, 3),
                "p_freq": round(peso_freq, 3),
                "p_ener": round(peso_ener, 4),
                "factor": round(factor, 4),
            }
        else:
            factores[clave] = 1.0

    # Paso 3: Aplicar factores por (mes, hora)
    g_calibrada = []
    for i in range(n):
        clave = (timestamps_h[i].month, timestamps_h[i].hour)
        g_calibrada.append(g_teo_h[i] * factores.get(clave, 1.0))

    # Paso 4: Imponer G_TEO >= FV_REAL donde FV_REAL > G_TEO_calibrada
    n_forzadas = 0
    for i in range(n):
        if fv_real_h[i] > g_calibrada[i]:
            g_calibrada[i] = fv_real_h[i]
            n_forzadas += 1

    total_original = sum(g_teo_h[:n])
    total_calibrada = sum(g_calibrada)
    auto_check = sum(min(fv_real_h[i], g_calibrada[i]) for i in range(n))

    # Resumen mensual para mostrar en UI
    factores_mes = {}
    for mes in range(1, 13):
        facs_hora = [factores.get((mes, h), 1.0) for h in range(24)
                     if (mes, h) in n_solar]
        if facs_hora:
            factores_mes[mes] = round(sum(facs_hora) / len(facs_hora), 4)
        else:
            factores_mes[mes] = 1.0

    info = {
        "calibracion": "Aplicada",
        "metodo": "doble_ponderacion_mes_hora",
        "umbral_kwh": umbral_kwh,
        "n_horas_calibracion": sum(n_exceso.values()),
        "n_horas_forzadas": n_forzadas,
        "n_slots_con_exceso": len([k for k in n_exceso if n_exceso[k] > 0]),
        "factores_mensuales": factores_mes,
        "factores_mes_hora": {f"{m}_{h}": round(factores.get((m, h), 1.0), 4)
                              for m in range(1, 13) for h in range(24)
                              if (m, h) in n_solar},
        "horas_afectadas_mes_hora": {f"{m}_{h}": n_solar[(m, h)] - n_exceso.get((m, h), 0)
                                     for m in range(1, 13) for h in range(24)
                                     if (m, h) in n_solar},
        "gteo_pre_cal_kwh": round(total_original, 1),
        "gteo_post_cal_kwh": round(total_calibrada, 1),
        "autoconsumo_check_kwh": round(auto_check, 1),
        "diferencia_pct": round(
            (total_calibrada - total_original) / max(1, total_original) * 100, 1
        ),
    }

    return g_calibrada, info


# ═══════════════════════════════════════════════════
# Upsampling horario → cuartohorario
# ═══════════════════════════════════════════════════

def upsample_a_cuartohorario(array_horario: List[float]) -> List[float]:
    """
    Transforma el array horario a cuartohorario con suavizado parabólico.
    Fallback cuando no hay pvlib.
    """
    array_15m = []
    longitud = len(array_horario)

    for i in range(longitud):
        e_curr = array_horario[i]

        if e_curr <= 0:
            array_15m.extend([0.0, 0.0, 0.0, 0.0])
            continue

        e_prev = array_horario[i - 1] if i > 0 else 0.0
        e_next = array_horario[i + 1] if i < longitud - 1 else 0.0

        w1 = (e_prev * 0.35) + (e_curr * 0.65)
        w2 = (e_prev * 0.10) + (e_curr * 0.90)
        w3 = (e_next * 0.10) + (e_curr * 0.90)
        w4 = (e_next * 0.35) + (e_curr * 0.65)

        pesos = [max(0.001, w1), max(0.001, w2), max(0.001, w3), max(0.001, w4)]
        suma_pesos = sum(pesos)

        array_15m.extend([
            e_curr * (pesos[0] / suma_pesos),
            e_curr * (pesos[1] / suma_pesos),
            e_curr * (pesos[2] / suma_pesos),
            e_curr * (pesos[3] / suma_pesos),
        ])

    return array_15m


def upsample_solar_cuartohorario(
    array_horario: List[float],
    lat: float,
    lon: float,
    anio: int = 2025,
) -> List[float]:
    """
    Transforma el array horario a cuartohorario usando posición solar
    real como peso (sin(elevación)). Conserva energía por hora.
    """
    import pandas as pd
    import math

    try:
        from pvlib.solarposition import get_solarposition
    except ImportError:
        return upsample_a_cuartohorario(array_horario)

    n_horas = len(array_horario)
    ts_inicio = pd.Timestamp(f"{anio}-01-01 00:00", tz="UTC")
    ts_15m = pd.date_range(ts_inicio, periods=n_horas * 4, freq="15min")

    solpos = get_solarposition(ts_15m, lat, lon)
    elevaciones = solpos["apparent_elevation"].values

    array_15m = []  

    for i in range(n_horas):
        e_curr = array_horario[i]

        if e_curr <= 0:
            array_15m.extend([0.0, 0.0, 0.0, 0.0])
            continue

        idx_base = i * 4
        elev = elevaciones[idx_base: idx_base + 4]

        pesos = [max(0.001, math.sin(math.radians(max(0, e)))) for e in elev]
        suma_pesos = sum(pesos)

        for w in pesos:
            array_15m.append(e_curr * (w / suma_pesos))

    return array_15m


# ═══════════════════════════════════════════════════
# Función principal
# ═══════════════════════════════════════════════════

def obtener_curva_solar_15m(
    lat: float,
    lon: float,
    peakpower: float,
    loss: float = 14.0,
    angle: Optional[float] = None,
    aspect: float = 0.0,
    pvtechchoice: str = "crystSi",
    mountingplace: str = "building",
    startyear: Optional[int] = None,
    endyear: Optional[int] = None,
    anio_objetivo: Optional[int] = None,
    cams_email: str = "jrodriguez@cavoenergias.com",
) -> Tuple[List[float], dict]:
    """
    Función principal: obtiene curva PVGIS y la convierte a cuartohoraria.

    Si anio_objetivo se proporciona (Ruta A FusionSolar):
    1. Obtiene G_TEO de PVGIS del año más cercano al objetivo
    2. Si los años difieren, corrige con ratio CAMS
    3. Upsample a cuartohoraria

    Returns:
        (G_TEO_15M, info)
    """
    # Determinar qué año PVGIS usar
    pvgis_max_year = 2023  # SARAH-3 máximo
    pvgis_min_year = 2005

    if anio_objetivo is not None:
        # Intentar usar el año objetivo; si no disponible, el más cercano
        if pvgis_min_year <= anio_objetivo <= pvgis_max_year:
            anio_pvgis = anio_objetivo
        else:
            # Año más cercano disponible
            anio_pvgis = min(pvgis_max_year, max(pvgis_min_year, anio_objetivo))
        sy = anio_pvgis
        ey = anio_pvgis
    else:
        sy = startyear
        ey = endyear
        anio_pvgis = None

    g_horario, info = obtener_curva_pvgis(
        lat=lat, lon=lon, peakpower=peakpower, loss=loss,
        angle=angle, aspect=aspect, pvtechchoice=pvtechchoice,
        mountingplace=mountingplace,
        startyear=sy, endyear=ey,
    )

    # Si hay año objetivo y difiere del PVGIS, corregir con CAMS
    if anio_objetivo is not None and anio_pvgis is not None:
        info["anio_pvgis_usado"] = anio_pvgis

        if anio_objetivo != anio_pvgis:
            try:
                g_horario, info_cams = corregir_gteo_con_cams(
                    g_horario, anio_objetivo, anio_pvgis,
                    lat, lon, email=cams_email,
                )
                info["correccion_cams"] = info_cams
                info["total_anual_kwh"] = info_cams["gteo_corregida_kwh"]
                info["kwh_kwp"] = round(
                    info_cams["gteo_corregida_kwh"] / peakpower, 1
                ) if peakpower > 0 else 0
            except Exception as e:
                logger.warning(f"CAMS correction failed: {e}")
                info["correccion_cams"] = {"correccion": f"Fallida: {e}"}
        else:
            info["correccion_cams"] = {"correccion": "No necesaria (mismo año)"}

    info["g_teo_horario"] = list(g_horario)  # guardar horario para calibración
    g_teo_15m = upsample_solar_cuartohorario(g_horario, lat, lon)

    return g_teo_15m, info
