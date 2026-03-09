"""
Lector de Excels mensuales de FusionSolar
==========================================
Lee los 12 archivos .xlsx que exporta FusionSolar ("Informe de plantas")
y extrae la curva horaria de autoconsumo para usar en Ruta A.

Fórmula: Autoconsumo = Rendimiento FV − Energía exportada
  - Rendimiento FV: generación total DC de todos los inversores
  - Energía exportada: energía vertida/inyectada a red
  - La diferencia es la energía solar realmente autoconsumida in situ

Al restar este autoconsumo de G_TEO (PVGIS) obtenemos la curva de
excedentes capados por el antivertido.
"""

import os
import io
from typing import List, Tuple, Optional, Union
from datetime import datetime

import pandas as pd


# ── Constantes ───────────────────────────────────────
COL_FECHA = "Período estadístico"
COL_REND_FV = "Rendimiento FV (kWh)"
COL_EXPORTADA = "Energía exportada (kWh)"
HEADER_ROW = 1  # Fila 0 es el título del informe, fila 1 son los headers


def leer_un_xlsx_fusionsolar(
    fuente: Union[str, io.BytesIO],
) -> pd.DataFrame:
    """
    Lee UN archivo .xlsx de FusionSolar y devuelve un DataFrame
    con columnas ['fecha', 'kwh_autoconsumo'] ordenado por fecha.
    Autoconsumo = Rendimiento FV − Energía exportada.
    """
    df = pd.read_excel(fuente, header=HEADER_ROW)

    if COL_FECHA not in df.columns:
        raise ValueError(
            f"No se encontró la columna '{COL_FECHA}' en el Excel. "
            f"Columnas disponibles: {df.columns.tolist()}"
        )
    if COL_REND_FV not in df.columns:
        raise ValueError(
            f"No se encontró la columna '{COL_REND_FV}' en el Excel. "
            f"Columnas disponibles: {df.columns.tolist()}"
        )
    if COL_EXPORTADA not in df.columns:
        raise ValueError(
            f"No se encontró la columna '{COL_EXPORTADA}' en el Excel. "
            f"Columnas disponibles: {df.columns.tolist()}"
        )

    # Limpiar timestamps: FusionSolar añade " DST" en horario de verano
    fechas_raw = df[COL_FECHA].astype(str).str.replace(r"\s*DST\s*$", "", regex=True)
    rend_fv = pd.to_numeric(df[COL_REND_FV], errors="coerce").fillna(0.0)
    exportada = pd.to_numeric(df[COL_EXPORTADA], errors="coerce").fillna(0.0)

    resultado = pd.DataFrame({
        "fecha": pd.to_datetime(fechas_raw, format="mixed", dayfirst=False),
        "kwh_autoconsumo": (rend_fv - exportada).clip(lower=0.0),
    })

    # Eliminar filas sin fecha válida
    resultado = resultado.dropna(subset=["fecha"])
    resultado = resultado.sort_values("fecha").reset_index(drop=True)

    return resultado


def leer_xlsx_fusionsolar_directorio(
    directorio: str,
) -> Tuple[List[float], List[datetime]]:
    """
    Lee TODOS los .xlsx de un directorio FusionSolar,
    los concatena en orden cronológico y devuelve:
      - valores: lista de kWh horarios de autoconsumo solar
      - timestamps: lista de datetimes correspondientes
    """
    archivos = sorted([
        os.path.join(directorio, f)
        for f in os.listdir(directorio)
        if f.endswith(".xlsx")
    ])

    if not archivos:
        raise ValueError(f"No se encontraron archivos .xlsx en {directorio}")

    dfs = []
    for archivo in archivos:
        df = leer_un_xlsx_fusionsolar(archivo)
        dfs.append(df)

    # Concatenar y ordenar
    df_total = pd.concat(dfs, ignore_index=True)
    df_total = df_total.sort_values("fecha").reset_index(drop=True)

    # Eliminar duplicados por fecha (por si hay solapamiento entre meses)
    df_total = df_total.drop_duplicates(subset=["fecha"], keep="first")
    df_total = df_total.sort_values("fecha").reset_index(drop=True)

    valores = df_total["kwh_autoconsumo"].tolist()
    timestamps = df_total["fecha"].tolist()

    return valores, timestamps


def leer_xlsx_fusionsolar_uploads(
    archivos: List[io.BytesIO],
) -> Tuple[List[float], List[datetime]]:
    """
    Lee múltiples archivos .xlsx subidos via Streamlit (file_uploader).
    Cada archivo es un BytesIO con los bytes del Excel.

    Returns:
      - valores: lista de kWh horarios de autoconsumo solar
      - timestamps: lista de datetimes correspondientes
    """
    if not archivos:
        raise ValueError("No se proporcionaron archivos Excel")

    dfs = []
    for archivo in archivos:
        df = leer_un_xlsx_fusionsolar(archivo)
        dfs.append(df)

    df_total = pd.concat(dfs, ignore_index=True)
    df_total = df_total.sort_values("fecha").reset_index(drop=True)
    df_total = df_total.drop_duplicates(subset=["fecha"], keep="first")
    df_total = df_total.sort_values("fecha").reset_index(drop=True)

    valores = df_total["kwh_autoconsumo"].tolist()
    timestamps = df_total["fecha"].tolist()

    return valores, timestamps


# ── Resumen rápido ───────────────────────────────────

def resumen_fusionsolar(valores: List[float], timestamps: List[datetime]) -> dict:
    """Genera un resumen del dataset FusionSolar leído."""
    total = sum(valores)
    n_horas = len(valores)
    n_dias = n_horas / 24 if n_horas > 0 else 0

    # Agrupar por mes
    mensual = {}
    for ts, v in zip(timestamps, valores):
        if isinstance(ts, datetime):
            clave = f"{ts.year}-{ts.month:02d}"
        else:
            clave = str(ts)[:7]
        mensual[clave] = mensual.get(clave, 0) + v

    return {
        "total_kwh": round(total, 1),
        "n_horas": n_horas,
        "n_dias": round(n_dias, 1),
        "mensual_kwh": {k: round(v, 1) for k, v in sorted(mensual.items())},
        "fecha_inicio": timestamps[0] if timestamps else None,
        "fecha_fin": timestamps[-1] if timestamps else None,
    }


if __name__ == "__main__":
    # Test rápido con el directorio local
    directorio = os.path.join(os.path.dirname(__file__), "xlx fusion solar")
    valores, timestamps = leer_xlsx_fusionsolar_directorio(directorio)
    info = resumen_fusionsolar(valores, timestamps)

    print(f"Registros horarios: {info['n_horas']}")
    print(f"Días: {info['n_dias']}")
    print(f"Total anual autoconsumo: {info['total_kwh']:,.1f} kWh")
    print(f"Período: {info['fecha_inicio']} → {info['fecha_fin']}")
    print("\nPor mes:")
    for mes, kwh in info["mensual_kwh"].items():
        print(f"  {mes}: {kwh:,.1f} kWh")
