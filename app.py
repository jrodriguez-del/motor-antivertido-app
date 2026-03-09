"""
Motor de Cálculo Antivertido y Perfilado v2
=============================================
Aplicación Streamlit para calcular excedentes capados por antivertido
en instalaciones fotovoltaicas.

v2: CAMS para irradiancia real, PVGIS año específico,
    detección automática de U y K_basal.
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime

# ── Configuración de página ──
st.set_page_config(
    page_title="Motor Antivertido FV",
    page_icon="☀️",
    layout="wide",
)

# ── CSS personalizado ──
st.markdown("""
<style>
    .main .block-container { max-width: 1100px; padding-top: 2rem; }
    .stMetric { background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
                padding: 1rem; border-radius: 10px; color: white; }
    div[data-testid="stMetricValue"] { color: white; font-size: 1.8rem; }
    div[data-testid="stMetricLabel"] { color: #b8d4e8; }
    .success-box { background-color: #1a3a2a; border-left: 4px solid #4caf50;
                   padding: 1rem; border-radius: 0 8px 8px 0; margin: 1rem 0; }
    .warning-box { background-color: #3a2a1a; border-left: 4px solid #ff9800;
                   padding: 1rem; border-radius: 0 8px 8px 0; margin: 1rem 0; }
    h1 { color: #f0a500; }
    .stTabs [data-baseweb="tab-list"] { gap: 2rem; }
</style>
""", unsafe_allow_html=True)


# ── Título ──
st.title("☀️ Motor de Cálculo Antivertido y Perfilado")
st.caption("Calcula excedentes capados por el sistema antivertido en instalaciones FV")

# ══════════════════════════════════════════════════════
# SIDEBAR: Inputs del usuario
# ══════════════════════════════════════════════════════

with st.sidebar:
    st.header("📋 Datos de la Instalación")

    st.subheader("1. Curva de consumo de red")
    csv_file = st.file_uploader(
        "CSV distribuidora (consumo de red)",
        type=["csv", "txt"],
        help="Formato distribuidora (12 cols) o procesado (5 cols). Separado por ;",
    )

    st.divider()
    st.subheader("2. Ubicación y planta FV")

    col1, col2 = st.columns(2)
    with col1:
        latitud = st.number_input("Latitud", value=38.8298, format="%.4f",
                                  help="Grados decimales (N positivo)")
    with col2:
        longitud = st.number_input("Longitud", value=-0.5014, format="%.4f",
                                   help="Grados decimales (W negativo)")

    kwp = st.number_input("Potencia pico (kWp)", value=108.0, min_value=0.1, step=0.5)

    with st.expander("⚙️ Parámetros avanzados PVGIS"):
        st.caption(
            "Estos parámetros controlan cómo PVGIS calcula la generación "
            "solar teórica para tu instalación."
        )
        loss = st.number_input(
            "Pérdidas sistema (%)", value=14.0, min_value=0.0,
            max_value=50.0, step=1.0,
            help="Pérdidas por cableado, inversor, suciedad, temperatura, etc. "
                 "14% es el valor estándar de la industria."
        )
        angle = st.number_input(
            "Inclinación (°)", value=10.0, min_value=0.0, max_value=90.0, step=1.0,
            help="Ángulo de inclinación de los paneles respecto a la horizontal. "
                 "10° es el valor típico en instalaciones sobre cubierta plana. "
                 "0° = PVGIS optimiza automáticamente (ángulo óptimo ~35° en España)."
        )
        aspect = st.number_input(
            "Azimut (°)", value=90, min_value=-180, max_value=180,
            help="Orientación de los paneles. 0°=Sur (óptimo en España), "
                 "90°=Oeste, -90°=Este, ±180°=Norte. "
                 "El azimut sur maximiza la producción anual."
        )
        tech = st.selectbox(
            "Tecnología módulos",
            ["crystSi", "CIS", "CdTe", "Unknown"],
            help="Tipo de célula fotovoltaica. "
                 "crystSi (silicio cristalino) es la más común (~95% del mercado). "
                 "CIS y CdTe son tecnologías de capa fina (thin-film)."
        )
        mount = st.selectbox(
            "Tipo de montaje",
            ["building", "free"],
            help="building = montaje en cubierta/techo (más común). "
                 "Peor ventilación → más pérdidas térmicas (~3-5% menos). "
                 "free = estructura independiente al aire libre (suelo). "
                 "Mejor ventilación → menor pérdida por temperatura."
        )

    st.divider()
    st.subheader("3. Dato de autoconsumo FV")
    st.caption("Elige la opción según los datos que tengas disponibles")

    ruta = st.radio(
        "Selecciona ruta de cálculo",
        [
            "Ruta A: Excels FusionSolar (mensuales)",
            "Ruta B: Autoconsumo anual (1 valor)",
        ],
        help="Ruta A es la más precisa (requiere Excels FusionSolar). Ruta B usa un único dato anual.",
    )

    csv_inversor_file = None
    xlsx_fusionsolar_files = None
    autoconsumo_anual = None

    if "FusionSolar" in ruta:
        xlsx_fusionsolar_files = st.file_uploader(
            "Excels mensuales FusionSolar (.xlsx)",
            type=["xlsx"],
            key="fusionsolar",
            accept_multiple_files=True,
            help="Sube los 12 archivos mensuales de FusionSolar "
                 "(Informe de plantas). Se calculará: Rendimiento FV − Energía exportada.",
        )
        if xlsx_fusionsolar_files:
            st.caption(f"📂 {len(xlsx_fusionsolar_files)} archivo(s) cargado(s)")

    elif "anual" in ruta:
        autoconsumo_anual_mwh = st.number_input(
            "Autoconsumo FV anual total (MWh)", value=0.0,
            min_value=0.0, step=1.0,
            help="1 MWh = 1.000 kWh. Ej: 87 MWh, 120 MWh...",
        )
        autoconsumo_anual = autoconsumo_anual_mwh * 1000  # MWh → kWh
        if autoconsumo_anual > 0:
            st.caption(f"= {autoconsumo_anual:,.0f} kWh")

    st.divider()
    st.subheader("4. Irradiancia real (CAMS)")
    usar_cams = st.checkbox(
        "Usar CAMS para irradiancia real",
        value=True,
        help="Mejora la estimación de demanda usando la irradiancia "
             "real del periodo del CSV (identifica días nublados)",
    )
    cams_email = st.text_input(
        "Email SoDa/CAMS",
        value="jrodriguez@cavoenergias.com",
        help="Email registrado en soda-pro.com",
    )

    st.divider()
    calcular = st.button("🚀 CALCULAR", use_container_width=True, type="primary")


# ══════════════════════════════════════════════════════
# CUERPO PRINCIPAL: Cálculos y resultados
# ══════════════════════════════════════════════════════

if calcular:
    if not csv_file:
        st.error("⚠️ Debes subir el CSV de consumo de red.")
        st.stop()

    # ── Importaciones ──
    from csv_processor import procesar_csv_consumo
    from pvgis_client import obtener_curva_solar_15m, calibrar_gteo_con_fv_real
    from ingesta import enrutar
    from balance import (
        generar_perfil_demanda_real,
        calcular_autoconsumo,
        generar_csv_salida,
        calcular_resumen,
    )

    try:
        # ══════════════════════════════════════
        # PASO 1: Procesar CSV de consumo de red
        # ══════════════════════════════════════
        with st.spinner("📊 Procesando CSV de consumo de red..."):
            contenido_csv = csv_file.getvalue().decode("utf-8-sig")
            c_red_15m, timestamps = procesar_csv_consumo(contenido_csv)
            st.success(f"✅ CSV procesado: **{len(c_red_15m):,} registros** cuartohorarios "
                       f"({len(c_red_15m)//96} días)")

        # Detectar año de los datos para PVGIS año específico
        anio_csv = timestamps[len(timestamps) // 2].year  # año central del CSV
        pvgis_startyear = None
        pvgis_endyear = None
        if 2005 <= anio_csv <= 2023:
            pvgis_startyear = anio_csv
            pvgis_endyear = anio_csv

        # ══════════════════════════════════════
        # PASO 2: Obtener curva solar PVGIS
        # ══════════════════════════════════════
        with st.spinner("🌤️ Consultando PVGIS..."):
            angle_val = angle if angle > 0 else None

            if pvgis_startyear:
                st.info(f"📅 Usando PVGIS año específico: **{pvgis_startyear}**")

            # Si es Ruta A FusionSolar, corregir G_TEO con CAMS
            anio_obj = None
            if xlsx_fusionsolar_files:
                anio_obj = 2025  # Año de los datos FusionSolar
                st.info(f"🔍 PVGIS año más cercano a **{anio_obj}**, corrigiendo con CAMS...")
            elif anio_csv and anio_csv > 2023:
                # Rutas B/C: corregir G_TEO con CAMS si el año del CSV > PVGIS max
                anio_obj = anio_csv
                st.info(f"🔍 CSV año {anio_csv} > PVGIS máximo (2023), corrigiendo G_TEO con CAMS...")

            g_teo_15m, pvgis_info = obtener_curva_solar_15m(
                lat=latitud, lon=longitud, peakpower=kwp,
                loss=loss, angle=angle_val, aspect=aspect,
                pvtechchoice=tech, mountingplace=mount,
                startyear=pvgis_startyear, endyear=pvgis_endyear,
                anio_objetivo=anio_obj,
                cams_email=cams_email if cams_email else "jrodriguez@cavoenergias.com",
            )

            # Mostrar info PVGIS + corrección CAMS
            st.success(
                f"✅ PVGIS: **{pvgis_info['total_anual_kwh']:,.0f} kWh/año** "
                f"({pvgis_info['kwh_kwp']:,.0f} kWh/kWp) · "
                f"**{pvgis_info['num_anios']} año(s)** "
                f"({pvgis_info['anios_disponibles']})"
            )
            if "correccion_cams" in pvgis_info:
                cc = pvgis_info["correccion_cams"]
                if cc.get("correccion") == "Aplicada":
                    st.caption(
                        f"📊 Corrección CAMS horaria: PVGIS {cc['anio_pvgis']} → "
                        f"datos {cc['anio_real']} · "
                        f"{cc['horas_escaladas']:,} horas escaladas individualmente · "
                        f"GHI ratio medio: {cc['ratio_ghi_global']:.3f} · "
                        f"G_TEO: {cc['gteo_original_kwh']:,.0f} → "
                        f"**{cc['gteo_corregida_kwh']:,.0f} kWh** "
                        f"({cc['diferencia_pct']:+.1f}%)"
                    )
                elif "Fallida" in str(cc.get("correccion", "")):
                    st.warning(f"⚠️ {cc['correccion']}")

        # ── Ajustar longitudes ──
        n = min(len(c_red_15m), len(g_teo_15m))
        c_red_15m = c_red_15m[:n]
        g_teo_15m = g_teo_15m[:n]
        timestamps = timestamps[:n]

        # ══════════════════════════════════════
        # PASO 2B: Obtener CAMS (irradiancia real)
        # ══════════════════════════════════════
        kt_15m = None
        if usar_cams and "Ruta A" not in ruta:
            with st.spinner("🛰️ Consultando CAMS (irradiancia real)..."):
                try:
                    from cams_client import obtener_irradiancia_cams, upsample_kt_a_cuartohorario

                    fecha_inicio = timestamps[0]
                    fecha_fin = timestamps[-1]

                    kt_horario, ghi_real, cams_info = obtener_irradiancia_cams(
                        lat=latitud, lon=longitud,
                        fecha_inicio=fecha_inicio,
                        fecha_fin=fecha_fin,
                        email=cams_email,
                    )

                    kt_15m_raw = upsample_kt_a_cuartohorario(kt_horario)
                    kt_15m = kt_15m_raw[:n]  # alinear con los demás arrays

                    st.success(
                        f"✅ CAMS: **{cams_info['cams_registros']:,} registros** · "
                        f"kt medio: **{cams_info['kt_medio']:.2f}** · "
                        f"**{cams_info['pct_nublado']:.0f}%** horas nubladas (kt<0.4)"
                    )
                except ImportError:
                    st.warning("⚠️ pvlib no instalado. Ejecuta: `pip install pvlib`")
                    kt_15m = None
                except Exception as e:
                    st.warning(f"⚠️ CAMS no disponible: {e}. Continuando sin irradiancia real.")
                    kt_15m = None

        # ══════════════════════════════════════
        # PASO 3: Enrutar y calcular excedentes
        # ══════════════════════════════════════
        with st.spinner("⚡ Calculando excedentes capados..."):
            csv_inv_contenido = None
            if csv_inversor_file:
                csv_inv_contenido = csv_inversor_file.getvalue().decode("utf-8-sig")

            curva_perdida, ruta_usada, ruta_info = enrutar(
                g_teo_15m=g_teo_15m,
                c_red_15m=c_red_15m,
                timestamps=timestamps,
                csv_inversor=csv_inv_contenido,
                xlsx_fusionsolar=xlsx_fusionsolar_files if xlsx_fusionsolar_files else None,
                lat=latitud,
                lon=longitud,
                autoconsumo_anual=autoconsumo_anual,
                kt_15m=kt_15m,
            )
            st.info(f"📍 **{ruta_usada}**")

            # Mostrar detección automática
            if "umbral_detectado_kw" in ruta_info:
                metodo = ruta_info.get('metodo', 'mesetas')
                st.caption(
                    f"🎯 Umbral antivertido detectado: **{ruta_info['umbral_detectado_kw']:.1f} kW** "
                    f"(método: {metodo}, "
                    f"{ruta_info.get('muestras_horarias_validas', '?')} muestras horarias, "
                    f"{ruta_info.get('mesetas_antivertido', 0)} mesetas diagnóstico)"
                )
            if "arquetipo" in ruta_info:
                st.caption(
                    f"🏢 Arquetipo: **{ruta_info['arquetipo']}** · "
                    f"K_basal: **{ruta_info.get('k_basal_kw', 0):.2f} kW**"
                )
            if "factor_escala" in ruta_info:
                st.caption(
                    f"Factor de escala del perfil: {ruta_info['factor_escala']}"
                )
            if "factor_medio" in ruta_info:
                st.caption(
                    f"Factor medio mensual: {ruta_info['factor_medio']} "
                    f"(factores: {ruta_info.get('factores_mensuales', [])})"
                )

        # ══════════════════════════════════════
        # PASO 4: Balance final
        # ══════════════════════════════════════
        with st.spinner("📐 Calculando balance energético..."):
            # Si hay datos FusionSolar, calibrar G_TEO con excesos reales
            if "autoconsumo_curva_15m" in ruta_info:
                fv_real = ruta_info["autoconsumo_curva_15m"]
                n_bal = min(len(g_teo_15m), len(fv_real), len(timestamps))

                # Calibración a resolución HORARIA (nativa FusionSolar)
                fv_real_h = ruta_info.get("autoconsumo_curva_h")
                g_teo_h = pvgis_info.get("g_teo_horario")

                if fv_real_h and g_teo_h:
                    import pandas as pd
                    ts_h = pd.date_range("2025-01-01", periods=min(len(g_teo_h), len(fv_real_h)), freq="1h")
                    g_teo_h_cal, info_cal = calibrar_gteo_con_fv_real(
                        g_teo_h, fv_real_h, ts_h,
                    )
                    # Re-upsample la G_TEO calibrada a 15min
                    from pvgis_client import upsample_solar_cuartohorario
                    g_teo_15m = upsample_solar_cuartohorario(g_teo_h_cal, latitud, longitud)
                    n_bal = min(len(g_teo_15m), len(fv_real), len(timestamps))
                else:
                    # Fallback: calibrar con datos 15min
                    import pandas as pd
                    ts_15 = pd.date_range("2025-01-01", periods=n_bal, freq="15min")
                    g_teo_15m_cal, info_cal = calibrar_gteo_con_fv_real(
                        g_teo_15m[:n_bal], fv_real[:n_bal], ts_15, umbral_kwh=0.75,
                    )
                    g_teo_15m = list(g_teo_15m_cal)

                if info_cal.get("calibracion") == "Aplicada":
                    st.caption(
                        f"🔧 Calibración (mes×hora): {info_cal['n_horas_calibracion']:,} horas referencia · "
                        f"{info_cal.get('n_horas_forzadas', 0):,} forzadas · "
                        f"G_TEO: {info_cal['gteo_pre_cal_kwh']:,.0f} → {info_cal['gteo_post_cal_kwh']:,.0f} kWh "
                        f"({info_cal['diferencia_pct']:+.1f}%) · "
                        f"Autoconsumo: {info_cal['autoconsumo_check_kwh']:,.0f} kWh"
                    )
                    # Tabla de factores mes×hora
                    import pandas as pd
                    meses_nombre = {1:'Ene',2:'Feb',3:'Mar',4:'Abr',5:'May',6:'Jun',
                                    7:'Jul',8:'Ago',9:'Sep',10:'Oct',11:'Nov',12:'Dic'}
                    fmh = info_cal.get('factores_mes_hora', {})
                    hamh = info_cal.get('horas_afectadas_mes_hora', {})
                    tabla = {}
                    for m in range(1, 13):
                        fila = {}
                        for h in range(24):
                            key = f"{m}_{h}"
                            if key in fmh:
                                val = fmh[key]
                                n_af = hamh.get(key, 0)
                                if val != 1.0:
                                    fila[f"h{h:02d}"] = f"{val:.3f} ({n_af}h)"
                                else:
                                    fila[f"h{h:02d}"] = "-"
                        if fila:
                            tabla[meses_nombre[m]] = fila
                    if tabla:
                        df_factores = pd.DataFrame(tabla).T.fillna("-")
                        cols_activas = [c for c in df_factores.columns
                                        if any(v != "-" for v in df_factores[c])]
                        if cols_activas:
                            with st.expander("📊 Factores por mes × hora (factor · horas afectadas)", expanded=False):
                                st.dataframe(df_factores[cols_activas], use_container_width=True)

                # Ahora autoconsumo = min(FV_REAL, G_TEO_calibrada)
                autoconsumo = [min(fv, g) for fv, g in zip(fv_real[:n_bal], g_teo_15m[:n_bal])]
                curva_perdida = [g - a for g, a in zip(g_teo_15m[:n_bal], autoconsumo)]
                consumo_real = [max(0.0, c + a) for c, a in zip(c_red_15m[:n_bal], autoconsumo)]
                # Recalcular n tras calibración (g_teo_15m puede haber cambiado longitud)
                n = n_bal
            else:
                consumo_real = generar_perfil_demanda_real(c_red_15m, g_teo_15m, curva_perdida)
                autoconsumo = calcular_autoconsumo(g_teo_15m, curva_perdida)



            # Asegurar todas las listas tienen longitud n
            n = min(n, len(c_red_15m), len(g_teo_15m), len(curva_perdida),
                    len(autoconsumo), len(consumo_real), len(timestamps))

            resumen = calcular_resumen(c_red_15m[:n], g_teo_15m[:n], curva_perdida[:n],
                                       autoconsumo[:n], consumo_real[:n], timestamps[:n])

            # Guardar en session_state para que persista al descargar CSV
            csv_salida = generar_csv_salida(
                timestamps[:n], consumo_real[:n], curva_perdida[:n],
                autoconsumo[:n], c_red_15m[:n], g_teo_15m[:n],
            )
            st.session_state["resultados"] = {
                "n": n,
                "c_red_15m": c_red_15m[:n],
                "g_teo_15m": g_teo_15m[:n],
                "curva_perdida": curva_perdida[:n],
                "autoconsumo": autoconsumo[:n],
                "consumo_real": consumo_real[:n],
                "timestamps": timestamps[:n],
                "resumen": resumen,
                "ruta_usada": ruta_usada,
                "ruta_info": ruta_info,
                "csv_salida": csv_salida,
            }

    except requests.exceptions.RequestException as e:
        st.error(f"❌ Error al conectar con PVGIS: {e}")
    except ValueError as e:
        st.error(f"❌ Error en los datos: {e}")
    except Exception as e:
        st.error(f"❌ Error inesperado: {e}")
        st.exception(e)

# ══════════════════════════════════════════════════════
# RESULTADOS (se muestran siempre que existan en session_state)
# ══════════════════════════════════════════════════════
if "resultados" in st.session_state:
    r = st.session_state["resultados"]
    resumen = r["resumen"]
    n = r["n"]
    ruta_info = r.get("ruta_info", {})

    st.divider()
    st.header("📊 Resultados")
    st.info(f"📍 **{r['ruta_usada']}**")

    # ── Info detección automática ──
    if "umbral_detectado_kw" in ruta_info:
        st.caption(
            f"🎯 Umbral: **{ruta_info['umbral_detectado_kw']:.1f} kW** · "
            f"🏢 {ruta_info.get('arquetipo', '?')} · "
            f"K_basal: **{ruta_info.get('k_basal_kw', 0):.2f} kW**"
        )

        # Panel de diagnóstico de mesetas
        with st.expander("🔍 Diagnóstico de detección (mesetas, perfil, CAMS)"):
            st.markdown("#### Detección de umbral antivertido")
            st.markdown(
                f"- **Método**: {ruta_info.get('metodo', 'mesetas')}\n"
                f"- **Muestras horarias analizadas**: {ruta_info.get('muestras_horarias_validas', '?')}\n"
                f"- **Mesetas diagnóstico**: {ruta_info.get('mesetas_antivertido', 0)} antivertido, "
                f"{ruta_info.get('mesetas_pausa_comida', 0)} pausas comida\n"
                f"- **C_RED nocturno medio**: {ruta_info.get('c_nocturno_medio_kw', '?')} kW")

            detalle = ruta_info.get("detalle_mesetas", [])
            if detalle:
                import pandas as pd
                df_mesetas = pd.DataFrame(detalle)
                st.dataframe(df_mesetas, use_container_width=True, hide_index=True)
            else:
                st.info("No se detectaron mesetas en la curva C_RED.")

            st.markdown("#### Perfil de demanda")
            info_perfil = ruta_info.get("info_perfil", {})
            if info_perfil:
                st.markdown(
                    f"- Datos nocturnos (exactos): **{info_perfil.get('slots_noche', 0):,}** cuartos\n"
                    f"- Datos C_RED > G_TEO (exactos): **{info_perfil.get('slots_cred_gt_gteo', 0):,}** cuartos\n"
                    f"- Datos CAMS nublados (buenos): **{info_perfil.get('slots_cams_nublado', 0):,}** cuartos\n"
                    f"- Interpolados (solar→solar): **{info_perfil.get('slots_interpolados_solar', 0):,}** slots\n"
                    f"- Fallback K_basal (conservador): **{info_perfil.get('slots_k_basal_fallback', 0):,}** slots"
                )
            else:
                st.caption("Activando CAMS se obtienen más datos de días nublados para mejorar la estimación.")

            # Debug bisección (Ruta B)
            debug_biseccion = ruta_info.get("debug_biseccion", [])
            if debug_biseccion:
                st.markdown("#### Bisección mensual (Ruta B)")

                # ⚠️ Warning if any month capped
                meses_capados = [d for d in debug_biseccion if d.get("capado")]
                if meses_capados:
                    deficit_total = sum(d["deficit_kwh"] for d in meses_capados)
                    st.warning(
                        f"⚠️ **{len(meses_capados)} mes(es) con autoconsumo capado**: "
                        f"el target introducido supera la generación teórica (G_TEO) de PVGIS. "
                        f"Déficit total: **{deficit_total:,.0f} kWh**. "
                        f"Posibles causas: G_TEO infraestimado (revisar kWp, inclinación, pérdidas) "
                        f"o el autoconsumo real incluye energía de otra fuente."
                    )

                import pandas as pd
                df_bisec = pd.DataFrame(debug_biseccion)
                st.dataframe(df_bisec, use_container_width=True, hide_index=True)
                total_target = sum(d["target_kwh"] for d in debug_biseccion)
                total_conseguido = sum(d["auto_conseguido_kwh"] for d in debug_biseccion)
                st.markdown(
                    f"- **Target total**: {total_target:,.0f} kWh\n"
                    f"- **Conseguido total**: {total_conseguido:,.0f} kWh\n"
                    f"- **Diferencia**: {total_conseguido - total_target:,.0f} kWh"
                )

    # ── KPIs ──
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("⚡ Generación FV teórica",
                  f"{resumen['total_generacion_teorica_kwh']:,.0f} kWh")
    with col2:
        st.metric("☀️ Autoconsumo solar",
                  f"{resumen['total_autoconsumo_kwh']:,.0f} kWh",
                  f"{resumen['pct_autoconsumo_solar']:.1f}% de FV aprovechada")
    with col3:
        st.metric("💀 Excedente capado",
                  f"{resumen['total_excedente_capado_kwh']:,.0f} kWh",
                  f"-{resumen['pct_energia_perdida']:.1f}% perdido",
                  delta_color="inverse")
    with col4:
        st.metric("🏢 Demanda real edificio",
                  f"{resumen['total_demanda_real_kwh']:,.0f} kWh")

    # Info cobertura
    st.caption(
        f"🔌 {resumen['cuartos_sol']:,} cuartos con sol · "
        f"Cobertura solar: **{resumen['pct_cobertura_solar']:.1f}%** de la demanda real")

    st.divider()

    # ── Tabs de detalle ──
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📈 Gráficas", "📅 Resumen Mensual", "📋 Datos", "💾 Descargas"]
    )

    with tab1:
        df = pd.DataFrame({
            "Fecha": r["timestamps"],
            "Consumo Red (kWh)": r["c_red_15m"],
            "Generación FV (kWh)": r["g_teo_15m"],
            "Autoconsumo (kWh)": r["autoconsumo"],
            "Excedente Capado (kWh)": r["curva_perdida"],
            "Demanda Real (kWh)": r["consumo_real"],
        })
        df = df.set_index("Fecha")

        # ── Selector de resolución ──
        resoluciones = {
            "Cuartohoraria (15 min)": None,
            "Horaria": "h",
            "Diaria": "D",
            "Semanal": "W",
            "Mensual": "ME",
        }
        col_res, col_curvas = st.columns([1, 2])
        with col_res:
            res_label = st.selectbox("📏 Resolución", list(resoluciones.keys()), index=2)
        with col_curvas:
            curvas_disponibles = list(df.columns)
            curvas_seleccionadas = st.multiselect(
                "📊 Curvas a mostrar",
                curvas_disponibles,
                default=["Excedente Capado (kWh)", "Autoconsumo (kWh)", "Generación FV (kWh)"],
            )

        res_code = resoluciones[res_label]
        df_plot = df.resample(res_code).sum() if res_code else df

        if curvas_seleccionadas:
            st.subheader(f"Energía — {res_label}")
            st.line_chart(df_plot[curvas_seleccionadas])
        else:
            st.warning("Selecciona al menos una curva para graficar.")

        # ── Gráfico de área: excedente vs autoconsumo ──
        st.subheader(f"Excedente capado vs Autoconsumo ({res_label})")
        st.area_chart(df_plot[["Autoconsumo (kWh)", "Excedente Capado (kWh)"]])

    with tab2:
        st.subheader("Resumen por mes")
        datos_tabla = []
        for mes_key in sorted(resumen["mensual"].keys()):
            m = resumen["mensual"][mes_key]
            datos_tabla.append({
                "Mes": mes_key,
                "Consumo Red (kWh)": round(m["red"], 1),
                "Generación FV (kWh)": round(m["gen"], 1),
                "Autoconsumo (kWh)": round(m["auto"], 1),
                "Excedente Capado (kWh)": round(m["exc"], 1),
                "Demanda Real (kWh)": round(m["demanda"], 1),
                "% Pérdida": round(m["exc"] / m["gen"] * 100, 1) if m["gen"] > 0 else 0,
            })
        df_mensual = pd.DataFrame(datos_tabla)
        st.dataframe(df_mensual, use_container_width=True, hide_index=True)

        st.bar_chart(df_mensual.set_index("Mes")[
            ["Autoconsumo (kWh)", "Excedente Capado (kWh)"]
        ])

    with tab3:
        st.subheader("Balance energético")
        st.markdown(f"""
        | Concepto | kWh/año |
        |----------|--------:|
        | Consumo de red (CSV) | **{resumen['total_consumo_red_kwh']:,.1f}** |
        | + Autoconsumo FV | **{resumen['total_autoconsumo_kwh']:,.1f}** |
        | = **Demanda real edificio** | **{resumen['total_demanda_real_kwh']:,.1f}** |
        | | |
        | Generación FV teórica | **{resumen['total_generacion_teorica_kwh']:,.1f}** |
        | − Autoconsumo FV | **{resumen['total_autoconsumo_kwh']:,.1f}** |
        | = **Excedente capado** | **{resumen['total_excedente_capado_kwh']:,.1f}** |
        | | |
        | % Energía FV perdida | **{resumen['pct_energia_perdida']:.1f}%** |
        | % Cobertura solar | **{resumen['pct_cobertura_solar']:.1f}%** |
        """)

    with tab4:
        st.subheader("Descargar curvas cuartohorarias")

        # Codificar como bytes con BOM para que Excel lo abra bien
        csv_bytes = r["csv_salida"].encode("utf-8-sig")

        st.download_button(
            label="📥 Descargar CSV completo",
            data=csv_bytes,
            file_name="excedentes_antivertido.csv",
            mime="text/csv",
            use_container_width=True,
            key="download_csv",
        )

        st.caption(f"Archivo con {n:,} registros cuartohorarios · "
                   f"Columnas: Fecha, Consumo_Red, Gen_Teorica, Autoconsumo, "
                   f"Excedente_Capado, Demanda_Real")


if "resultados" not in st.session_state and not calcular:
    # ── Pantalla de bienvenida ──
    st.markdown("""
    ### ¿Cómo funciona?

    1. **Sube el CSV** de consumo de red de la distribuidora
    2. **Introduce la ubicación** y potencia pico de la instalación FV
    3. **Selecciona la ruta** según los datos de autoconsumo que tengas
    4. **Pulsa CALCULAR** para obtener los excedentes capados

    ---

    #### Rutas de cálculo disponibles

    | Ruta | Datos necesarios | Precisión |
    |------|-----------------|:---------:|
    | **A (FusionSolar)** | Excels mensuales FusionSolar | ⭐⭐⭐ |
    | **B (Anual)** | Autoconsumo anual (1 valor) | ⭐⭐ |

    ---

    #### Detección automática (Ruta B)

    El motor detecta automáticamente:
    - 🎯 **Umbral del instalador (U)**: analiza mesetas planas en C_RED durante pico solar
    - 🏢 **Consumo basal (K_basal)**: consumo mínimo nocturno del edificio
    - 🛰️ **Días nublados (CAMS)**: usa irradiancia real para inferir demanda en horas solares
    """)
