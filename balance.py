"""
Módulo 4: Balance Final y Exportación
=======================================
Calcula el balance energético del edificio:

  autoconsumo = G_TEO - excedente_capado
  demanda_real = C_RED + autoconsumo
"""

import csv
import io
from typing import List
from datetime import datetime


def calcular_autoconsumo(
    g_teo_15m: List[float],
    curva_excedente: List[float],
) -> List[float]:
    """
    Calcula la curva de autoconsumo solar cuartohoraria.
    autoconsumo = G_TEO - excedente_capado
    """
    return [max(0.0, g - e) for g, e in zip(g_teo_15m, curva_excedente)]


def generar_perfil_demanda_real(
    c_red_15m: List[float],
    g_teo_15m: List[float],
    curva_excedente: List[float],
) -> List[float]:
    """
    Calcula el consumo real del edificio (demanda real).
    demanda_real = C_RED + autoconsumo = C_RED + (G_TEO - excedente)
    """
    consumo_real = []
    for c_red, g_teo, excedente in zip(c_red_15m, g_teo_15m, curva_excedente):
        autoconsumo = max(0.0, g_teo - excedente)
        consumo_real.append(max(0.0, c_red + autoconsumo))
    return consumo_real


def generar_csv_salida(
    timestamps: List[datetime],
    consumo_real: List[float],
    excedente_capado: List[float],
    autoconsumo: List[float],
    c_red_15m: List[float],
    g_teo_15m: List[float],
) -> str:
    """Genera CSV con todas las curvas cuartohorarias."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Fecha",
        "Consumo_Red_kWh",
        "Generacion_Teorica_kWh",
        "Autoconsumo_Solar_kWh",
        "Excedente_Capado_kWh",
        "Demanda_Real_kWh",
    ])

    for i in range(len(timestamps)):
        fecha_str = timestamps[i].strftime("%d/%m/%Y %H:%M")
        writer.writerow([
            fecha_str,
            f"{c_red_15m[i]:.4f}".replace(".", ","),
            f"{g_teo_15m[i]:.4f}".replace(".", ","),
            f"{autoconsumo[i]:.4f}".replace(".", ","),
            f"{excedente_capado[i]:.4f}".replace(".", ","),
            f"{consumo_real[i]:.4f}".replace(".", ","),
        ])

    return output.getvalue()


def calcular_resumen(
    c_red_15m: List[float],
    g_teo_15m: List[float],
    excedente_capado: List[float],
    autoconsumo: List[float],
    consumo_real: List[float],
    timestamps: List[datetime],
) -> dict:
    """Calcula métricas resumen anuales."""
    total_red = sum(c_red_15m)
    total_gen_teo = sum(g_teo_15m)
    total_excedente = sum(excedente_capado)
    total_autoconsumo = sum(autoconsumo)
    total_demanda_real = sum(consumo_real)

    cuartos_sol = sum(1 for g in g_teo_15m if g > 0)

    pct_perdida = (total_excedente / total_gen_teo * 100) if total_gen_teo > 0 else 0
    pct_autoconsumo = (total_autoconsumo / total_gen_teo * 100) if total_gen_teo > 0 else 0
    pct_cobertura_solar = (total_autoconsumo / total_demanda_real * 100) if total_demanda_real > 0 else 0

    from collections import defaultdict
    mensual = defaultdict(lambda: {"red": 0, "gen": 0, "exc": 0, "auto": 0, "demanda": 0})
    for i, ts in enumerate(timestamps):
        clave = ts.strftime("%Y-%m")
        mensual[clave]["red"] += c_red_15m[i]
        mensual[clave]["gen"] += g_teo_15m[i]
        mensual[clave]["exc"] += excedente_capado[i]
        mensual[clave]["auto"] += autoconsumo[i]
        mensual[clave]["demanda"] += consumo_real[i]

    return {
        "total_consumo_red_kwh": round(total_red, 2),
        "total_generacion_teorica_kwh": round(total_gen_teo, 2),
        "total_excedente_capado_kwh": round(total_excedente, 2),
        "total_autoconsumo_kwh": round(total_autoconsumo, 2),
        "total_demanda_real_kwh": round(total_demanda_real, 2),
        "pct_energia_perdida": round(pct_perdida, 1),
        "pct_autoconsumo_solar": round(pct_autoconsumo, 1),
        "pct_cobertura_solar": round(pct_cobertura_solar, 1),
        "cuartos_sol": cuartos_sol,
        "mensual": dict(mensual),
        "num_registros": len(timestamps),
    }
