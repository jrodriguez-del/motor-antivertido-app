"""
Módulo 2: Enrutador de Ingesta v2
====================================
Decide qué ruta de cálculo seguir según los datos disponibles:
- Ruta A: CSV del inversor (curva horaria de generación real)
- Ruta B: Dato agregado de autoconsumo (mensual o anual)
- Ruta C: Sin datos → perfil de demanda estimado automáticamente

v2: Integra detección automática de umbral (U), consumo basal
    (K_basal) y datos CAMS para Rutas B y C.
"""

import csv
import io
from typing import List, Optional, Tuple
from datetime import datetime

from pvgis_client import upsample_a_cuartohorario, upsample_solar_cuartohorario
from fusionsolar_reader import leer_xlsx_fusionsolar_uploads, resumen_fusionsolar
from motor_deduccion import (
    deducir_perdida_anual,
    calcular_ruta_c,
    detectar_umbral_antivertido,
    detectar_consumo_basal,
)


# ── Lectura de CSV de inversor ───────────────────────

def _parsear_fecha_inversor(fecha_str: str) -> datetime:
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(fecha_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Formato de fecha no reconocido: {fecha_str}")


def _parsear_numero(valor_str: str) -> float:
    return float(valor_str.strip().replace(",", "."))


def leer_csv_inversor(contenido: str) -> Tuple[List[float], str]:
    registros = []
    f = io.StringIO(contenido)

    linea = ""
    for linea in f:
        if linea.strip():
            break

    separador = ";" if ";" in linea else ","

    reader = csv.reader(f, delimiter=separador)
    for fila in reader:
        if len(fila) < 2:
            continue
        try:
            fecha = _parsear_fecha_inversor(fila[0])
            valor = _parsear_numero(fila[1])
            registros.append({"fecha": fecha, "valor": abs(valor)})
        except (ValueError, IndexError):
            continue

    if len(registros) < 2:
        return [r["valor"] for r in registros], "horaria"

    delta = registros[1]["fecha"] - registros[0]["fecha"]
    minutos = delta.total_seconds() / 60
    resolucion = "cuartohoraria" if minutos <= 15 else "horaria"

    valores = [r["valor"] for r in registros]
    return valores, resolucion


# ── Router principal ─────────────────────────────

def enrutar(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
    csv_inversor: Optional[str] = None,
    xlsx_fusionsolar: Optional[list] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    autoconsumo_mensual: Optional[List[float]] = None,
    autoconsumo_anual: Optional[float] = None,
    kt_15m: Optional[List[float]] = None,
) -> Tuple[List[float], str, dict]:
    """
    Decide la ruta y calcula la curva de excedentes capados.

    Args:
        xlsx_fusionsolar: lista de BytesIO con Excels mensuales de FusionSolar
        kt_15m: índice de claridad cuartohorario (de CAMS), opcional

    Returns:
        (curva_excedente_15m, ruta_usada, info)
    """
    info = {}

    # ── RUTA A: Excels FusionSolar (mensuales) ──
    if xlsx_fusionsolar:
        fv_valores_h, fs_timestamps = leer_xlsx_fusionsolar_uploads(xlsx_fusionsolar)
        fs_info = resumen_fusionsolar(fv_valores_h, fs_timestamps)
        info["fusionsolar_total_kwh"] = fs_info["total_kwh"]
        info["fusionsolar_meses"] = len(fs_info["mensual_kwh"])
        info["fusionsolar_horas"] = fs_info["n_horas"]

        # Upsample de horaria a cuartohoraria (con posición solar real)
        if lat is not None and lon is not None:
            fv_valores = upsample_solar_cuartohorario(fv_valores_h, lat, lon)
        else:
            fv_valores = upsample_a_cuartohorario(fv_valores_h)

        n = min(len(g_teo_15m), len(fv_valores))
        curva_excedente = []
        total_autoconsumo = 0
        for i in range(n):
            excedente = max(0.0, g_teo_15m[i] - fv_valores[i])
            curva_excedente.append(excedente)
            total_autoconsumo += fv_valores[i]

        while len(curva_excedente) < len(g_teo_15m):
            curva_excedente.append(0.0)

        # Curva de autoconsumo real (FusionSolar), padded
        autoconsumo_real = list(fv_valores[:n])
        while len(autoconsumo_real) < len(g_teo_15m):
            autoconsumo_real.append(0.0)

        info["autoconsumo_fusionsolar_kwh"] = round(total_autoconsumo, 1)
        info["autoconsumo_curva_15m"] = autoconsumo_real
        info["autoconsumo_curva_h"] = list(fv_valores_h)  # horario nativo
        return (
            curva_excedente,
            f"Ruta A: FusionSolar Excel ({fs_info['n_horas']} h → "
            f"{fs_info['total_kwh']:,.0f} kWh autoconsumo)",
            info,
        )

    # ── RUTA A: CSV del inversor ──
    if csv_inversor:
        fv_valores, resolucion = leer_csv_inversor(csv_inversor)

        if resolucion == "horaria":
            fv_valores = upsample_a_cuartohorario(fv_valores)

        n = min(len(g_teo_15m), len(fv_valores))
        curva_excedente = []
        total_inversor = 0
        for i in range(n):
            excedente = max(0.0, g_teo_15m[i] - fv_valores[i])
            curva_excedente.append(excedente)
            total_inversor += fv_valores[i]

        while len(curva_excedente) < len(g_teo_15m):
            curva_excedente.append(0.0)

        info["produccion_inversor_kwh"] = round(total_inversor, 1)
        return curva_excedente, "Ruta A: CSV inversor (resta directa)", info

    # ── Auto-detectar U y K_basal (para Rutas B y C) ──
    umbral, info_umbral = detectar_umbral_antivertido(
        g_teo_15m, c_red_15m, timestamps
    )
    k_basal, arquetipo = detectar_consumo_basal(
        c_red_15m, g_teo_15m, timestamps
    )
    info.update(info_umbral)
    info["k_basal_kwh_15min"] = round(k_basal, 4)
    info["k_basal_kw"] = round(k_basal * 4, 2)
    info["arquetipo"] = arquetipo

    # ── RUTA B: Dato agregado de autoconsumo ──
    if autoconsumo_mensual or (autoconsumo_anual is not None and autoconsumo_anual > 0):
        curva_excedente, deduccion_info = deducir_perdida_anual(
            g_teo_15m=g_teo_15m,
            c_red_15m=c_red_15m,
            timestamps=timestamps,
            autoconsumo_mensual=autoconsumo_mensual,
            autoconsumo_anual=autoconsumo_anual,
            umbral=umbral,
            k_basal=k_basal,
            kt_15m=kt_15m,
        )
        info.update(deduccion_info)

        if autoconsumo_mensual:
            ruta_str = "Ruta B: Autoconsumo mensual (bisección ×12)"
        else:
            ruta_str = (
                f"Ruta B: Autoconsumo anual (bisección, "
                f"factor={info.get('factor_escala', '?')})"
            )
        return curva_excedente, ruta_str, info

    # ── RUTA C: Sin datos → perfil de demanda estimado ──
    curva_excedente, ruta_c_info = calcular_ruta_c(
        g_teo_15m, c_red_15m, timestamps,
        umbral=umbral, k_basal=k_basal, kt_15m=kt_15m,
    )
    info.update(ruta_c_info)
    pct_av = info.get('pct_cuartos_posible_av', 0)
    return (
        curva_excedente,
        f"Ruta C: Perfil de demanda estimado "
        f"({pct_av:.0f}% cuartos con posible antivertido)",
        info,
    )
