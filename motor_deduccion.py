"""
Módulo 3: Motor de Deducción con Perfil de Demanda v6
========================================================
Modelo que trabaja SOLO con energía (kWh), sin conversiones a kW.

FÓRMULAS FUNDAMENTALES (deben cumplirse SIEMPRE):
  1. Demanda_edificio = C_RED + Autoconsumo_FV
  2. G_TEO = Autoconsumo_FV + Excedente
  3. C_RED >= 0 (esto ES el antivertido)

De (1) y (2):
  Autoconsumo = min(Demanda, G_TEO)
  Excedente   = max(0, G_TEO - Demanda)
  C_RED       = Demanda - Autoconsumo = max(0, Demanda - G_TEO)

v6: Detección automática de umbral (U) por mesetas,
    consumo basal (K_basal) por datos nocturnos,
    perfil de demanda mejorado con índice kt (CAMS).
"""

from typing import List, Optional, Tuple
from datetime import datetime
from collections import defaultdict
import math
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# DETECCIÓN AUTOMÁTICA DEL UMBRAL DEL INSTALADOR (U)
# ═══════════════════════════════════════════════════════

def detectar_umbral_antivertido(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
) -> Tuple[float, dict]:
    """
    Detecta automáticamente el umbral U del instalador con algoritmo
    combinado: moda horaria (primario) + mesetas (diagnóstico).

    PRIMARIO — Moda horaria:
    1. Agrupa G_TEO y C_RED a resolución horaria (suma 4 cuartos)
    2. Identifica top 30% días con mayor irradiancia en horas centrales
    3. Moda de C_RED redondeado a 0.1 kWh en esas horas → umbral U

    DIAGNÓSTICO — Mesetas:
    Detecta secuencias planas en C_RED a 15min para informar al usuario
    cuándo y cuánto dura el antivertido. Incluye C_RED de hora previa
    para validar que toda G_TEO se absorbía antes de la meseta.

    Returns:
        (umbral_kwh_15min, info_deteccion)
    """
    from collections import Counter

    # ═══ PARTE 1: MODA HORARIA (detector primario de U) ═══

    # 1a. Agrupar a resolución horaria (sumar los cuartos de hora)
    horas_dict = defaultdict(lambda: {'g': 0.0, 'c': 0.0, 'count': 0})
    for g, c, ts in zip(g_teo_15m, c_red_15m, timestamps):
        clave = (ts.year, ts.month, ts.day, ts.hour)
        horas_dict[clave]['g'] += g
        horas_dict[clave]['c'] += c
        horas_dict[clave]['count'] += 1

    # Filtrar solo horas completas (4 cuartos)
    horas_validas = {k: v for k, v in horas_dict.items() if v['count'] == 4}

    # 1b. Encontrar días con mayor irradiancia en el centro del día
    g_diario_central = defaultdict(float)
    for (y, m, d, h), vals in horas_validas.items():
        if 11 <= h <= 15:
            g_diario_central[(y, m, d)] += vals['g']

    moda_horaria = 0.0
    muestras_horarias = 0

    if g_diario_central:
        # Top 30% de los días con mayor irradiancia
        dias_ordenados = sorted(g_diario_central.keys(),
                                key=lambda k: g_diario_central[k], reverse=True)
        top_n = max(1, int(len(dias_ordenados) * 0.30))
        dias_top = set(dias_ordenados[:top_n])

        # 1c. Filtrar consumos en esas horas centrales de los días más soleados
        c_candidatos = []
        for (y, m, d, h), vals in horas_validas.items():
            if (y, m, d) in dias_top and 11 <= h <= 15:
                c = vals['c']
                # Consumo <= 25 kWh/h y > 0 (descartamos ruido muy bajo)
                if 0.05 < c <= 25.0:
                    c_candidatos.append(c)

        muestras_horarias = len(c_candidatos)

        if c_candidatos:
            # Redondear a 1 decimal para que la moda agrupe valores cercanos
            c_redondeados = [round(c, 1) for c in c_candidatos]
            contador = Counter(c_redondeados)

            # Tomar la moda (valor más repetido). Empate → el menor consumo.
            moda_redondeada = sorted(contador.most_common(),
                                     key=lambda x: (-x[1], x[0]))[0][0]

            # Promediar los valores reales en esa moda para mayor precisión
            valores_moda = [c for c in c_candidatos
                            if round(c, 1) == moda_redondeada]
            moda_horaria = sum(valores_moda) / len(valores_moda)

    # Umbral en kWh/15min (dividir entre 4)
    umbral_moda_15m = moda_horaria / 4.0

    # ═══ PARTE 2: MESETAS (diagnóstico + validación pre-meseta) ═══

    dias_idx = defaultdict(list)
    for i, ts in enumerate(timestamps):
        dia = ts.date()
        dias_idx[dia].append(i)

    # C_RED nocturno promedio (para clasificación de mesetas)
    c_nocturnos = [
        c_red_15m[i] for i, ts in enumerate(timestamps)
        if g_teo_15m[i] <= 0 and c_red_15m[i] > 0
    ]
    c_nocturno_medio = (sum(c_nocturnos) / len(c_nocturnos)) if c_nocturnos else 1.0

    mesetas_antivertido = []
    mesetas_pausa = []

    for dia, indices in dias_idx.items():
        if len(indices) < 10:
            continue

        g_dia_pos = [g_teo_15m[i] for i in indices if g_teo_15m[i] > 0]
        if len(g_dia_pos) < 4:
            continue

        g_dia_pos.sort()
        p70 = g_dia_pos[min(int(len(g_dia_pos) * 0.70), len(g_dia_pos) - 1)]
        pico_indices = [i for i in indices if g_teo_15m[i] >= p70]
        if len(pico_indices) < 3:
            continue

        secuencia = []
        for idx in pico_indices:
            c = c_red_15m[idx]
            if not secuencia:
                secuencia = [idx]
                continue

            ts_prev = timestamps[secuencia[-1]]
            ts_curr = timestamps[idx]
            delta_min = (ts_curr - ts_prev).total_seconds() / 60
            if delta_min > 20:
                _procesar_meseta(secuencia, c_red_15m, timestamps, g_teo_15m,
                                mesetas_antivertido, mesetas_pausa,
                                c_nocturno_medio)
                secuencia = [idx]
                continue

            valores_seq = [c_red_15m[j] for j in secuencia]
            mediana = sorted(valores_seq)[len(valores_seq) // 2]
            if mediana > 0.001:
                variacion = abs(c - mediana) / mediana
            else:
                variacion = abs(c - mediana)

            if variacion < 0.15 or abs(c - mediana) < 0.01:
                secuencia.append(idx)
            else:
                _procesar_meseta(secuencia, c_red_15m, timestamps, g_teo_15m,
                                mesetas_antivertido, mesetas_pausa,
                                c_nocturno_medio)
                secuencia = [idx]

        _procesar_meseta(secuencia, c_red_15m, timestamps, g_teo_15m,
                        mesetas_antivertido, mesetas_pausa,
                        c_nocturno_medio)

    # Detalle de mesetas para debug (con validación pre-meseta)
    detalle_mesetas = []
    for m in mesetas_antivertido:
        # Buscar C_RED y G_TEO de la hora previa a la meseta
        idx_inicio = m.get("idx_inicio", 0)
        c_pre = c_red_15m[idx_inicio - 1] if idx_inicio > 0 else None
        g_pre = g_teo_15m[idx_inicio - 1] if idx_inicio > 0 else None
        detalle_mesetas.append({
            "tipo": "ANTIVERTIDO",
            "mes": m["mes"],
            "hora": f"{m['hora_inicio']:.1f}-{m['hora_fin']:.1f}",
            "valor_kwh": round(m["valor"], 4),
            "valor_kw": round(m["valor"] * 4, 2),
            "cuartos": m["duracion_cuartos"],
            "c_pre_kw": round(c_pre * 4, 2) if c_pre is not None else None,
            "g_pre_kw": round(g_pre * 4, 2) if g_pre is not None else None,
            "pre_absorbia_todo": (c_pre > umbral_moda_15m) if c_pre is not None else None,
        })
    for m in mesetas_pausa:
        detalle_mesetas.append({
            "tipo": "PAUSA_COMIDA",
            "mes": m["mes"],
            "hora": f"{m['hora_inicio']:.1f}-{m['hora_fin']:.1f}",
            "valor_kwh": round(m["valor"], 4),
            "valor_kw": round(m["valor"] * 4, 2),
            "cuartos": m["duracion_cuartos"],
        })

    # ═══ RESULTADO: Usar moda horaria como U primario ═══
    info = {
        "umbral_kwh_15min": round(umbral_moda_15m, 4),
        "umbral_detectado_kw": round(moda_horaria, 2),
        "metodo": "moda_horaria_horas_centrales",
        "muestras_horarias_validas": muestras_horarias,
        # Diagnóstico de mesetas (compatibilidad + validación)
        "mesetas_antivertido": len(mesetas_antivertido),
        "mesetas_pausa_comida": len(mesetas_pausa),
        "c_nocturno_medio_kw": round(c_nocturno_medio * 4, 2),
        "detalle_mesetas": detalle_mesetas,
    }

    logger.info(
        f"Umbral detectado (moda horaria): {moda_horaria:.2f} kW "
        f"(de {muestras_horarias} horas analizadas, "
        f"{len(mesetas_antivertido)} mesetas diagnóstico)"
    )

    return umbral_moda_15m, info


def _procesar_meseta(
    secuencia: List[int],
    c_red_15m: List[float],
    timestamps: List[datetime],
    g_teo_15m: List[float],
    mesetas_antivertido: list,
    mesetas_pausa: list,
    c_nocturno_medio: float = 1.0,
):
    """Clasifica una secuencia de cuartos planos como antivertido o pausa."""
    if len(secuencia) < 3:
        return

    valores = [c_red_15m[i] for i in secuencia]
    valor_medio = sum(valores) / len(valores)
    hora_inicio = timestamps[secuencia[0]].hour + timestamps[secuencia[0]].minute / 60
    hora_fin = timestamps[secuencia[-1]].hour + timestamps[secuencia[-1]].minute / 60
    mes = timestamps[secuencia[0]].month
    duracion_cuartos = len(secuencia)
    idx_inicio = secuencia[0]  # guardar para validación pre-meseta

    g_meseta = sum(g_teo_15m[i] for i in secuencia)

    meseta_info = {
        "valor": valor_medio,
        "hora_inicio": hora_inicio,
        "hora_fin": hora_fin,
        "mes": mes,
        "duracion_cuartos": duracion_cuartos,
        "g_teo_meseta": g_meseta,
        "idx_inicio": idx_inicio,
    }

    es_pausa = (
        valor_medio < 0.02 and
        12 <= hora_inicio <= 15 and
        duracion_cuartos <= 6 and
        duracion_cuartos >= 3
    )

    if es_pausa:
        mesetas_pausa.append(meseta_info)
    elif g_meseta > 0:
        mesetas_antivertido.append(meseta_info)



# ═══════════════════════════════════════════════════════
# DETECCIÓN DEL CONSUMO BASAL (K_basal)
# ═══════════════════════════════════════════════════════

def detectar_consumo_basal(
    c_red_15m: List[float],
    g_teo_15m: List[float],
    timestamps: List[datetime],
) -> Tuple[float, str]:
    """
    Detecta K_basal usando EXCLUSIVAMENTE datos nocturnos (G_TEO = 0).
    De noche no hay solar → D = C_RED con certeza → revela el suelo de demanda.

    Clasifica el cliente en arquetipos según la variabilidad nocturna:
    - "24/7 CONTINUO": variabilidad < 15% (hospital, data center)
    - "TURNOS": variabilidad 15-50% (fábrica con turnos)
    - "OFICINA/COMERCIO": variabilidad > 50% con patrón laboral
    - "RESIDENCIAL": variabilidad > 50% sin patrón claro

    Returns: (k_basal_kwh_15min, arquetipo)
    """
    # Recopilar C_RED nocturno por (mes, hora, cuarto)
    nocturnos = defaultdict(list)
    for i, ts in enumerate(timestamps):
        if g_teo_15m[i] <= 0 and c_red_15m[i] >= 0:
            clave = (ts.month, ts.hour, ts.minute // 15)
            nocturnos[clave].append(c_red_15m[i])

    if not nocturnos:
        return 0.0, "DESCONOCIDO"

    # Percentil 10 por (mes, hora, cuarto)
    p10_por_clave = {}
    for clave, valores in nocturnos.items():
        valores.sort()
        idx = max(0, int(len(valores) * 0.10))
        p10_por_clave[clave] = valores[min(idx, len(valores) - 1)]

    todos_p10 = list(p10_por_clave.values())
    if not todos_p10:
        return 0.0, "DESCONOCIDO"

    media_p10 = sum(todos_p10) / len(todos_p10)

    # Calcular variabilidad
    if media_p10 > 0.001:
        varianzas = [(v - media_p10) ** 2 for v in todos_p10]
        desviacion = math.sqrt(sum(varianzas) / len(varianzas))
        variabilidad = desviacion / media_p10
    else:
        variabilidad = 1.0  # si la media es ~0, alta variabilidad

    # Clasificar arquetipo
    if variabilidad < 0.15:
        arquetipo = "24/7 CONTINUO"
        k_basal = media_p10
    elif variabilidad < 0.50:
        arquetipo = "TURNOS"
        todos_p10.sort()
        k_basal = todos_p10[max(0, int(len(todos_p10) * 0.10))]
    else:
        # Distinguir oficina/comercio de residencial
        # Si hay patrón laboral (L-V vs S-D diferente)
        laborables = []
        fines_semana = []
        for i, ts in enumerate(timestamps):
            if g_teo_15m[i] <= 0 and c_red_15m[i] >= 0:
                if ts.weekday() < 5:
                    laborables.append(c_red_15m[i])
                else:
                    fines_semana.append(c_red_15m[i])

        if laborables and fines_semana:
            media_lab = sum(laborables) / len(laborables)
            media_fds = sum(fines_semana) / len(fines_semana)
            ratio = media_lab / max(0.001, media_fds)
            if ratio > 1.3:
                arquetipo = "OFICINA/COMERCIO"
            else:
                arquetipo = "RESIDENCIAL"
        else:
            arquetipo = "RESIDENCIAL"

        todos_p10.sort()
        k_basal = todos_p10[max(0, int(len(todos_p10) * 0.05))]

    logger.info(
        f"K_basal: {k_basal:.4f} kWh/15min ({k_basal * 4:.2f} kW) · "
        f"Arquetipo: {arquetipo} (variabilidad: {variabilidad:.2f})"
    )

    return k_basal, arquetipo


# ═══════════════════════════════════════════════════════
# PERFIL DE DEMANDA DEL EDIFICIO
# ═══════════════════════════════════════════════════════

def construir_perfil_demanda(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
    k_basal: float = 0.0,
    kt_15m: Optional[List[float]] = None,
) -> Tuple[dict, dict]:
    """
    Construye perfil de demanda del edificio por (mes, hora, cuarto).

    Fuentes FIABLES:
    1. G_TEO = 0 y C_RED > 0 → demanda = C_RED (noche, EXACTA)
    2. C_RED > G_TEO y G_TEO > 0 → demanda = C_RED + G_TEO (EXACTA)
    3. kt < 0.4 (CAMS, día nublado) → demanda ≈ C_RED + G_TEO×kt (BUENA)

    Slots SIN datos medidos durante horas solares se dejan en K_basal
    (estimación conservadora). NO se interpolan desde datos nocturnos
    para evitar inflar la demanda solar y sobreestimar autoconsumo.

    Returns:
        (perfil, info_perfil)
    """
    datos = defaultdict(list)
    fuente = defaultdict(str)  # para debug: qué fuente usó cada slot

    n_noche = 0
    n_cred_gt_gteo = 0
    n_cams_nublado = 0

    for i, ts in enumerate(timestamps):
        g = g_teo_15m[i]
        c = c_red_15m[i]
        clave = (ts.month, ts.hour, ts.minute // 15)

        if g <= 0 and c > 0:
            datos[clave].append(c)
            fuente[clave] = "NOCHE"
            n_noche += 1
        elif g > 0 and c > g:
            datos[clave].append(c + g)
            fuente[clave] = "CRED>GTEO"
            n_cred_gt_gteo += 1
        elif kt_15m is not None and g > 0 and i < len(kt_15m):
            kt = kt_15m[i]
            if 0 < kt < 0.4:
                g_real_aprox = g * kt
                datos[clave].append(c + g_real_aprox)
                fuente[clave] = "CAMS_NUBLADO"
                n_cams_nublado += 1

    # Promediar slots con datos medidos
    perfil = {}
    for clave, valores in datos.items():
        perfil[clave] = sum(valores) / len(valores)

    # Rellenar huecos SOLO dentro de la misma franja (solar con solar,
    # nocturna con nocturna) — NUNCA copiar datos nocturnos a horas solares
    meses_presentes = set(ts.month for ts in timestamps)
    n_interpolados = 0
    n_k_basal_fallback = 0

    for mes in meses_presentes:
        for hora in range(24):
            for cuarto in range(4):
                clave = (mes, hora, cuarto)
                if clave in perfil:
                    continue  # ya tiene dato medido

                es_hora_solar = 7 <= hora <= 20

                if es_hora_solar:
                    # Para horas solares: solo interpolar desde OTRAS
                    # horas solares del MISMO mes (nunca noche)
                    valor = _interpolar_solo_solar(perfil, mes, hora, cuarto)
                    if valor is not None:
                        perfil[clave] = valor
                        n_interpolados += 1
                    else:
                        # Sin datos solares → usar K_basal (conservador)
                        perfil[clave] = k_basal
                        n_k_basal_fallback += 1
                else:
                    # Para horas nocturnas: se puede interpolar libremente
                    valor = _interpolar_nocturna(perfil, mes, hora, cuarto)
                    perfil[clave] = valor if valor is not None else k_basal

    # Aplicar K_basal como cota inferior
    if k_basal > 0:
        for clave in perfil:
            perfil[clave] = max(k_basal, perfil[clave])

    info_perfil = {
        "slots_noche": n_noche,
        "slots_cred_gt_gteo": n_cred_gt_gteo,
        "slots_cams_nublado": n_cams_nublado,
        "slots_interpolados_solar": n_interpolados,
        "slots_k_basal_fallback": n_k_basal_fallback,
        "total_slots_perfil": len(perfil),
    }

    return perfil, info_perfil


def _interpolar_solo_solar(
    perfil: dict, mes: int, hora: int, cuarto: int
) -> Optional[float]:
    """Interpola SOLO desde horas solares (7-20) del mismo mes."""
    for delta in range(1, 6):
        for d in [delta, -delta]:
            h = (hora + d)
            if h < 7 or h > 20:
                continue  # no usar datos nocturnos
            clave = (mes, h, cuarto)
            if clave in perfil:
                return perfil[clave]
    # Probar meses adyacentes (solo horas solares cercanas)
    for d_mes in [1, -1]:
        m = ((mes - 1 + d_mes) % 12) + 1
        for d_h in range(0, 3):
            for sign in [d_h, -d_h]:
                h = hora + sign
                if h < 7 or h > 20:
                    continue
                clave = (m, h, cuarto)
                if clave in perfil:
                    return perfil[clave]
    return None  # no hay datos solares → signal to use K_basal


def _interpolar_nocturna(
    perfil: dict, mes: int, hora: int, cuarto: int
) -> Optional[float]:
    """Interpola desde horas nocturnas adyacentes."""
    for delta in range(1, 8):
        for d in [delta, -delta]:
            h = (hora + d) % 24
            clave = (mes, h, cuarto)
            if clave in perfil:
                return perfil[clave]
    return None


# ═══════════════════════════════════════════════════════
# SIMULACIÓN DEL ANTIVERTIDO
# ═══════════════════════════════════════════════════════

def simular_antivertido(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
    perfil_demanda: dict,
    factor_escala: float = 1.0,
    umbral: float = 0.0,
    solo_perfil: bool = False,
) -> Tuple[float, float, List[float]]:
    """
    Simula el antivertido para cada cuarto de hora.

    Para cada cuarto con G_TEO > 0:
      D_est = perfil(mes, hora, cuarto) × factor_escala

      Si solo_perfil=False (Rutas B/C):
        Ley física: si C_RED > (umbral + tolerancia), el inversor
        NO estaba limitando → autoconsumo = G_TEO, excedente = 0.
        Si C_RED <= (umbral + tol) → usar demanda estimada.

      Si solo_perfil=True:
        → SIEMPRE usar demanda estimada.

    Args:
        umbral: umbral del instalador en kWh/15min (auto-detectado)
        solo_perfil: si True, no usar la ley física C_RED > U

    Returns:
        (autoconsumo_total, excedente_total, curva_excedente)
    """


    autoconsumo_total = 0.0
    excedente_total = 0.0
    curva_excedente = []

    for i, ts in enumerate(timestamps):
        g = g_teo_15m[i]
        c = c_red_15m[i]

        if g <= 0:
            curva_excedente.append(0.0)
            continue

        if not solo_perfil and c > g:
            # Ley física: C_RED > G_TEO → la demanda del edificio supera
            # la generación solar → NO hay curtailment → excedente = 0
            autoconsumo_total += g
            curva_excedente.append(0.0)
        else:
            # Usar demanda estimada del perfil
            clave = (ts.month, ts.hour, ts.minute // 15)
            d_est = perfil_demanda.get(clave, 0.0) * factor_escala
            a_max = max(0.0, d_est - umbral)
            autoconsumo_q = min(g, a_max)
            excedente_q = g - autoconsumo_q

            autoconsumo_total += autoconsumo_q
            excedente_total += excedente_q
            curva_excedente.append(excedente_q)

    return autoconsumo_total, excedente_total, curva_excedente


# ═══════════════════════════════════════════════════════
# RUTA C: Sin datos de autoconsumo
# ═══════════════════════════════════════════════════════

def calcular_ruta_c(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
    umbral: float = 0.0,
    k_basal: float = 0.0,
    kt_15m: Optional[List[float]] = None,
) -> Tuple[List[float], dict]:
    """
    Ruta C: Sin dato de autoconsumo del usuario.
    Usa perfil de demanda (factor = 1.0) con U y K_basal auto-detectados.
    """
    perfil, info_perfil = construir_perfil_demanda(
        g_teo_15m, c_red_15m, timestamps,
        k_basal=k_basal, kt_15m=kt_15m,
    )

    auto_total, exc_total, curva = simular_antivertido(
        g_teo_15m, c_red_15m, timestamps, perfil, 1.0,
        umbral=umbral,
    )

    gen_total = sum(g for g in g_teo_15m if g > 0)
    cuartos_sol = sum(1 for g in g_teo_15m if g > 0)
    cuartos_posible_av = sum(
        1 for g, c in zip(g_teo_15m, c_red_15m) if g > 0 and c <= g
    )

    info = {
        "autoconsumo_estimado_kwh": round(auto_total, 1),
        "excedente_estimado_kwh": round(exc_total, 1),
        "generacion_total_kwh": round(gen_total, 1),
        "pct_autoconsumo": round(auto_total / gen_total * 100, 1) if gen_total > 0 else 0,
        "cuartos_sol": cuartos_sol,
        "cuartos_posible_antivertido": cuartos_posible_av,
        "pct_cuartos_posible_av": round(cuartos_posible_av / cuartos_sol * 100, 1) if cuartos_sol > 0 else 0,
        "info_perfil": info_perfil,
    }

    return curva, info


# ═══════════════════════════════════════════════════════
# RUTA B: Con dato de autoconsumo (bisección)
# ═══════════════════════════════════════════════════════

def _indices_por_mes(timestamps: List[datetime]) -> dict:
    meses = {}
    for i, ts in enumerate(timestamps):
        clave = (ts.year, ts.month)
        if clave not in meses:
            meses[clave] = []
        meses[clave].append(i)
    return meses


def deducir_perdida_anual(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
    autoconsumo_mensual: Optional[List[float]] = None,
    autoconsumo_anual: Optional[float] = None,
    umbral: float = 0.0,
    k_basal: float = 0.0,
    kt_15m: Optional[List[float]] = None,
) -> Tuple[List[float], dict]:
    """
    Calcula la curva de excedente capado anual.
    Construye el perfil de demanda UNA SOLA VEZ con todos los datos del año.
    La bisección solo varía factor_escala sobre ese perfil compartido.
    """
    info = {}
    meses_idx = _indices_por_mes(timestamps)
    claves_ordenadas = sorted(meses_idx.keys())

    # Construir perfil GLOBAL (todo el año) para máxima calidad de datos
    perfil, info_perfil = construir_perfil_demanda(
        g_teo_15m, c_red_15m, timestamps,
        k_basal=k_basal, kt_15m=kt_15m,
    )
    info["info_perfil"] = info_perfil

    if autoconsumo_mensual and len(autoconsumo_mensual) >= len(claves_ordenadas):
        curva_excedente = [0.0] * len(g_teo_15m)
        factores = []
        debug_biseccion = []

        for i, clave in enumerate(claves_ordenadas):
            indices = meses_idx[clave]
            g_mes = [g_teo_15m[j] for j in indices]
            c_mes = [c_red_15m[j] for j in indices]
            ts_mes = [timestamps[j] for j in indices]
            target_kwh = autoconsumo_mensual[i]
            gen_mes = sum(g for g in g_mes if g > 0)

            # Bisección usando el perfil GLOBAL (no reconstruir por mes)
            perdida_mes, factor = _biseccion_con_perfil(
                g_mes, c_mes, ts_mes, target_kwh,
                perfil=perfil, umbral=umbral, solo_perfil=True,  # Ruta B: usuario es verdad
            )

            # Verificar autoconsumo conseguido
            auto_conseguido = sum(
                max(0.0, g - e) for g, e in zip(g_mes, perdida_mes)
            )

            factores.append(round(factor, 3))
            capado = target_kwh > gen_mes
            debug_biseccion.append({
                "mes": f"{clave[0]}-{clave[1]:02d}",
                "target_kwh": round(target_kwh, 0),
                "auto_conseguido_kwh": round(auto_conseguido, 0),
                "gen_mes_kwh": round(gen_mes, 0),
                "factor": round(factor, 3),
                "capado": capado,
                "deficit_kwh": round(target_kwh - auto_conseguido, 0) if capado else 0,
            })

            for j, idx in enumerate(indices):
                curva_excedente[idx] = perdida_mes[j]

            if capado:
                logger.warning(
                    f"Mes {clave}: target={target_kwh:.0f} > gen={gen_mes:.0f} "
                    f"→ autoconsumo capado a {auto_conseguido:.0f} kWh "
                    f"(déficit: {target_kwh - auto_conseguido:.0f} kWh)"
                )
            else:
                print(
                    f"Ruta B mes {clave}: target={target_kwh:.0f} → "
                    f"conseguido={auto_conseguido:.0f} kWh (factor={factor:.3f}, "
                    f"gen={gen_mes:.0f})"
                )

        info["factores_mensuales"] = factores
        info["factor_medio"] = round(sum(factores) / len(factores), 3)
        info["debug_biseccion"] = debug_biseccion
        return curva_excedente, info

    elif autoconsumo_anual is not None and autoconsumo_anual > 0:
        curva, factor = _biseccion_con_perfil(
            g_teo_15m, c_red_15m, timestamps, autoconsumo_anual,
            perfil=perfil, umbral=umbral, solo_perfil=True,  # Ruta B: usuario es verdad
        )
        info["factor_escala"] = round(factor, 3)
        return curva, info

    # Sin dato → Ruta C
    curva, info_c = calcular_ruta_c(
        g_teo_15m, c_red_15m, timestamps,
        umbral=umbral, k_basal=k_basal, kt_15m=kt_15m,
    )
    info.update(info_c)
    return curva, info


def _biseccion_con_perfil(
    g_teo_15m: List[float],
    c_red_15m: List[float],
    timestamps: List[datetime],
    autoconsumo_objetivo: float,
    perfil: dict,
    umbral: float = 0.0,
    solo_perfil: bool = False,
) -> Tuple[List[float], float]:
    """
    Bisección sobre factor_escala del perfil de demanda GLOBAL.
    Busca factor tal que autoconsumo_simulado ≈ autoconsumo_objetivo.

    Cuando solo_perfil=False, aplica "ley física":
      Si C_RED > G_TEO → autoconsumo = G_TEO (inevitable).
      Si el autoconsumo objetivo del usuario es menor que este
      autoconsumo inevitable, activa salvavidas → solo_perfil=True.
    """
    gen_total = sum(g for g in g_teo_15m if g > 0)

    if not solo_perfil:
        # Autoconsumo fijo inevitable: cuartos donde C_RED > G_TEO
        auto_fijo = sum(
            g for g, c in zip(g_teo_15m, c_red_15m)
            if g > 0 and c > g
        )
        if autoconsumo_objetivo <= auto_fijo:
            # Salvavidas: target < auto inevitable → fallback a solo perfil
            logger.warning(
                f"Salvavidas: autoconsumo objetivo ({autoconsumo_objetivo:.0f}) "
                f"< auto inevitable ({auto_fijo:.0f}). Usando solo_perfil=True."
            )
            solo_perfil = True

    if autoconsumo_objetivo >= gen_total:
        _, _, curva = simular_antivertido(
            g_teo_15m, c_red_15m, timestamps, perfil, 1000.0,
            umbral=umbral, solo_perfil=solo_perfil,
        )
        return curva, 1000.0

    # Bisección
    f_min = 0.0
    f_max = 10.0
    tolerancia = 1.0  # kWh

    auto_max, _, _ = simular_antivertido(
        g_teo_15m, c_red_15m, timestamps, perfil, f_max,
        umbral=umbral, solo_perfil=solo_perfil,
    )
    while auto_max < autoconsumo_objetivo and f_max < 1000:
        f_max *= 2
        auto_max, _, _ = simular_antivertido(
            g_teo_15m, c_red_15m, timestamps, perfil, f_max,
            umbral=umbral, solo_perfil=solo_perfil,
        )

    factor_final = 1.0
    mejor_curva = []

    for _ in range(60):
        f_mid = (f_min + f_max) / 2.0
        auto_total, _, curva = simular_antivertido(
            g_teo_15m, c_red_15m, timestamps, perfil, f_mid,
            umbral=umbral, solo_perfil=solo_perfil,
        )
        error = auto_total - autoconsumo_objetivo

        if abs(error) <= tolerancia:
            factor_final = f_mid
            mejor_curva = curva
            break
        elif error > 0:
            f_max = f_mid
        else:
            f_min = f_mid

        factor_final = f_mid
        mejor_curva = curva

    return mejor_curva, factor_final
