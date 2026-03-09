"""
Módulo 0: Procesador de CSVs de consumo de red
================================================
Lee CSVs de distribuidora (raw o procesados) y devuelve arrays
cuartohorarios de consumo de red (C_RED_15M) + timestamps.

Reutiliza la lógica probada de periodos.py de calculadora-facturas.
"""

import csv
import io
import calendar
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional


# ── Parseo flexible ──────────────────────────────────

def _saltar_lineas_vacias(f):
    """Avanza el cursor hasta la primera línea no vacía (cabecera)."""
    for linea in f:
        if linea.strip():
            return linea.strip()
    return ""


def _parsear_fecha(fecha_str: str) -> datetime:
    """Parsea fecha en formatos comunes de distribuidora."""
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(fecha_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Formato de fecha no reconocido: {fecha_str}")


def _parsear_numero(valor_str: str) -> float:
    """Parsea número con coma o punto decimal."""
    return float(valor_str.strip().replace(",", "."))


# ── Lectura de CSVs ──────────────────────────────────

def leer_csv_distribuidora(contenido: str) -> List[Dict]:
    """
    Lee CSV RAW de distribuidora (12 columnas, separador ;).
    Extrae: fecha, activa (kWh), vertida (kWh).
    """
    registros = []
    f = io.StringIO(contenido)
    cabecera = _saltar_lineas_vacias(f)
    if not cabecera:
        return registros

    reader = csv.reader(f, delimiter=";")
    for fila in reader:
        if len(fila) < 12:
            continue
        try:
            fecha = _parsear_fecha(fila[1])
            activa = _parsear_numero(fila[3])
            vertida = _parsear_numero(fila[9])
            registros.append({
                "fecha": fecha,
                "activa": activa,
                "vertida": vertida,
            })
        except (ValueError, IndexError):
            continue
    return registros


def leer_csv_procesada(contenido: str) -> List[Dict]:
    """
    Lee CSV ya procesado (Fecha;Activa_kWh;Reactiva_kVArh;Vertida_kWh;Periodo).
    """
    registros = []
    f = io.StringIO(contenido)
    cabecera = _saltar_lineas_vacias(f)
    if not cabecera:
        return registros

    reader = csv.reader(f, delimiter=";")
    for fila in reader:
        if len(fila) < 5:
            continue
        try:
            fecha = _parsear_fecha(fila[0])
            activa = _parsear_numero(fila[1])
            vertida = _parsear_numero(fila[3])
            registros.append({
                "fecha": fecha,
                "activa": activa,
                "vertida": vertida,
            })
        except (ValueError, IndexError):
            continue
    return registros


# ── Detección de formato ───────────────────────────────

def detectar_resolucion(registros: List[Dict]) -> str:
    """Detecta si la curva es horaria o cuartohoraria."""
    if len(registros) < 2:
        return "horaria"
    delta = registros[1]["fecha"] - registros[0]["fecha"]
    minutos = delta.total_seconds() / 60
    return "cuartohoraria" if minutos <= 15 else "horaria"


def detectar_formato(contenido: str) -> str:
    """Detecta si el CSV es RAW (distribuidora, 12 cols) o procesado (5 cols)."""
    f = io.StringIO(contenido)
    cabecera = _saltar_lineas_vacias(f)
    if not cabecera:
        return "desconocido"

    # Leer primera línea de datos
    reader = csv.reader(f, delimiter=";")
    for fila in reader:
        if len(fila) >= 12:
            return "raw"
        elif len(fila) >= 5:
            return "procesada"
        break
    return "desconocido"


# ── Normalización a cuartohoraria ──────────────────────

def expandir_horaria_a_cuartohoraria(registros: List[Dict]) -> List[Dict]:
    """
    Expande curva horaria a cuartohoraria.
    Cada registro horario se divide en 4 cuartos con kWh/4.
    """
    expandidos = []
    for reg in registros:
        hora_fin = reg["fecha"]
        kwh_cuarto = reg["activa"] / 4.0
        vert_cuarto = reg["vertida"] / 4.0

        for offset in (45, 30, 15, 0):
            ts = hora_fin - timedelta(minutes=offset)
            expandidos.append({
                "fecha": ts,
                "activa": kwh_cuarto,
                "vertida": vert_cuarto,
            })

    expandidos.sort(key=lambda x: x["fecha"])
    return expandidos


# ── Filtrado temporal ────────────────────────────────

def filtrar_12_meses(registros: List[Dict]) -> Tuple[List[Dict], str, str]:
    """
    Filtra a los últimos 12 meses naturales completos.
    Retorna (registros_filtrados, str_inicio, str_fin).
    """
    if not registros:
        raise ValueError("No hay registros para filtrar")

    fechas = [r["fecha"] for r in registros]
    fecha_max = max(fechas)

    ultimo_mes = fecha_max.month
    ultimo_anio = fecha_max.year

    ultimo_dia_mes = calendar.monthrange(ultimo_anio, ultimo_mes)[1]
    if fecha_max.day < ultimo_dia_mes or (
        fecha_max.day == ultimo_dia_mes and fecha_max.hour < 23
    ):
        if ultimo_mes == 1:
            ultimo_mes = 12
            ultimo_anio -= 1
        else:
            ultimo_mes -= 1

    if ultimo_mes == 12:
        fecha_fin = datetime(ultimo_anio + 1, 1, 1)
    else:
        fecha_fin = datetime(ultimo_anio, ultimo_mes + 1, 1)

    mes_inicio = ultimo_mes + 1
    anio_inicio = ultimo_anio - 1
    if mes_inicio > 12:
        mes_inicio -= 12
        anio_inicio += 1

    fecha_inicio = datetime(anio_inicio, mes_inicio, 1)

    filtrados = [
        r for r in registros
        if fecha_inicio < r["fecha"] <= fecha_fin
    ]

    str_inicio = f"{mes_inicio:02d}/{anio_inicio}"
    str_fin = f"{ultimo_mes:02d}/{ultimo_anio}"

    return filtrados, str_inicio, str_fin


# ── Función principal ────────────────────────────────

def procesar_csv_consumo(contenido: str) -> Tuple[List[float], List[datetime]]:
    """
    Función principal. Recibe contenido de CSV, detecta formato,
    filtra 12 meses, normaliza a cuartohoraria.

    Returns:
        (c_red_15m, timestamps) - arrays de 35.040 valores
    """
    formato = detectar_formato(contenido)

    if formato == "raw":
        registros = leer_csv_distribuidora(contenido)
    elif formato == "procesada":
        registros = leer_csv_procesada(contenido)
    else:
        raise ValueError("Formato de CSV no reconocido. Se esperan 12 columnas (distribuidora) o 5 columnas (procesada).")

    if not registros:
        raise ValueError("El CSV no contiene datos válidos.")

    # Filtrar a 12 meses
    registros, rango_ini, rango_fin = filtrar_12_meses(registros)

    # Normalizar a cuartohoraria si es horaria
    resolucion = detectar_resolucion(registros)
    if resolucion == "horaria":
        registros = expandir_horaria_a_cuartohoraria(registros)

    # Ordenar por fecha
    registros.sort(key=lambda x: x["fecha"])

    # Extraer arrays
    c_red_15m = [r["activa"] for r in registros]
    timestamps = [r["fecha"] for r in registros]

    return c_red_15m, timestamps
