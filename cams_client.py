"""
Módulo 1B: Cliente CAMS (Copernicus Atmosphere Monitoring Service)
==================================================================
Obtiene irradiancia REAL histórica (GHI, DNI, DHI, clear-sky) para
las fechas exactas del CSV de consumo de red.

Se usa para calcular el índice de claridad kt = GHI_real / GHI_clear,
que permite identificar horas nubladas donde C_RED revela la demanda
real del edificio (porque el antivertido apenas actúa).

Fuente: SoDa/CAMS vía pvlib.iotools.get_cams
Cobertura: Europa, África, Asia central (Meteosat)
Resolución: ~3-5 km, horaria, desde 2004-02-01 hasta hoy-2d
Límite: 100 peticiones/día
"""

from typing import List, Tuple, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def obtener_irradiancia_cams(
    lat: float,
    lon: float,
    fecha_inicio: datetime,
    fecha_fin: datetime,
    email: str = "jrodriguez@cavoenergias.com",
) -> Tuple[List[float], List[float], dict]:
    """
    Obtiene GHI real y GHI clear-sky horarios del periodo indicado.
    Calcula kt = GHI_real / GHI_clear para cada hora.

    Args:
        lat, lon: coordenadas de la instalación
        fecha_inicio, fecha_fin: rango del CSV de consumo (se ajusta a lo
            disponible en CAMS: desde 2004-02-01 hasta hace 2 días)
        email: email registrado en SoDa

    Returns:
        (kt_horario, ghi_real_horario, info)
        - kt_horario: lista de ~8760 valores kt (0.0 = noche/cubierto, 1.0 = cielo limpio)
        - ghi_real_horario: lista de ~8760 valores GHI real en Wh/m²
        - info: metadatos (fechas, registros, etc.)
    """
    try:
        import pvlib
    except ImportError:
        raise ImportError(
            "Se necesita pvlib para usar CAMS. Instala con: pip install pvlib"
        )

    # Ajustar fechas a lo disponible en CAMS
    cams_inicio_min = datetime(2004, 2, 1)
    if fecha_inicio < cams_inicio_min:
        fecha_inicio = cams_inicio_min

    # CAMS no tiene datos de los últimos 2 días
    from datetime import timedelta
    hoy = datetime.now()
    cams_fin_max = hoy - timedelta(days=3)  # margen de seguridad
    if fecha_fin > cams_fin_max:
        fecha_fin = cams_fin_max

    logger.info(
        f"Consultando CAMS: ({lat:.4f}, {lon:.4f}) "
        f"del {fecha_inicio:%Y-%m-%d} al {fecha_fin:%Y-%m-%d}"
    )

    data, meta = pvlib.iotools.get_cams(
        latitude=lat,
        longitude=lon,
        start=fecha_inicio,
        end=fecha_fin,
        email=email,
        identifier='cams_radiation',
        time_step='1h',
        time_ref='UT',
        integrated=True,       # Wh/m² por hora
        map_variables=True,    # nombres estándar: ghi, dni, dhi, etc.
    )

    # Extraer arrays
    ghi_real = data['ghi'].tolist()
    ghi_clear = data['ghi_clear'].tolist()

    # Calcular kt (índice de claridad)
    kt_horario = []
    for real, clear in zip(ghi_real, ghi_clear):
        if clear > 10:  # evitar divisiones por valores nocturnos
            kt = min(1.5, max(0.0, real / clear))
        else:
            kt = 0.0  # noche
        kt_horario.append(kt)

    # Info de debug
    dias_totales = (fecha_fin - fecha_inicio).days
    horas_nubladas = sum(1 for k in kt_horario if 0 < k < 0.4)
    horas_sol = sum(1 for k in kt_horario if k > 0)

    info = {
        "cams_registros": len(ghi_real),
        "cams_periodo": f"{fecha_inicio:%Y-%m-%d} → {fecha_fin:%Y-%m-%d}",
        "cams_dias": dias_totales,
        "horas_sol_cams": horas_sol,
        "horas_nubladas_kt04": horas_nubladas,
        "pct_nublado": round(horas_nubladas / max(1, horas_sol) * 100, 1),
        "kt_medio": round(sum(kt_horario) / max(1, len(kt_horario)), 3),
    }

    return kt_horario, ghi_real, info


def upsample_kt_a_cuartohorario(kt_horario: List[float]) -> List[float]:
    """
    Upsample de kt horario a cuartohorario.
    A diferencia del upsampling solar (parabólico), kt se replica
    porque es un ratio que no varía mucho intra-hora.
    """
    kt_15m = []
    for k in kt_horario:
        kt_15m.extend([k, k, k, k])
    return kt_15m
