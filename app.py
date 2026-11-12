# ===============================================
# ¿QUÉ ME CONVIENE CON MIS PESOS? — PROTOTIPO EN STREAMLIT
# Programación para Economistas — UADE · Segundo Parcial · Bautista Avila
# ===============================================
# Esta app usa las mismas funciones del cuaderno de Colab.
# La diferencia: el monto y la fecha de inicio los elige el usuario.

import datetime

import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st
from scipy.interpolate import CubicSpline


# ===============================================
# CONFIGURACIÓN DE LA PÁGINA
# ===============================================
st.set_page_config(page_title="¿Qué me conviene con mis pesos?", page_icon="💰", layout="centered")

ALTERNATIVAS = ["Colchón", "Plazo fijo", "Dólar MEP"]
DATOS_DESDE = "2019-12-01"   # Primer dato que descargamos (diciembre 2019 sirve de base para enero 2020)
COLORES = ["#888888", "#3a7d44", "#2f6690"]   # Un color por alternativa, en el mismo orden
MESES = "['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic']"   # Para el eje del gráfico


# ===============================================
# FUNCIONES DE DESCARGA (las mismas del cuaderno)
# ===============================================
def descargar_serie_oficial(id_serie, nombre_columna, desde):
    """Descarga una serie de la API de Series de Tiempo del Ministerio de Economía (datos.gob.ar)"""
    url = "https://apis.datos.gob.ar/series/api/series/"
    parametros = {"ids": id_serie, "start_date": desde, "limit": 5000, "format": "json"}
    respuesta = requests.get(url, params=parametros, timeout=30)
    datos = respuesta.json()
    df = pd.DataFrame(datos["data"], columns=["fecha", nombre_columna])
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


def descargar_serie_bcra(id_variable, nombre_columna, desde, hasta):
    """Descarga una serie diaria de la API de Estadísticas del Banco Central (BCRA)"""
    url = f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/{id_variable}"
    parametros = {"desde": desde, "hasta": hasta, "limit": 3000}
    respuesta = requests.get(url, params=parametros, timeout=30)
    datos = respuesta.json()
    df = pd.DataFrame(datos["results"][0]["detalle"])
    df = df.rename(columns={"valor": nombre_columna})
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df.sort_values("fecha")


def descargar_mep():
    """Descarga el dólar MEP (precio de venta) de argentinadatos.com"""
    respuesta = requests.get("https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa", timeout=30)
    df = pd.DataFrame(respuesta.json())
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df[["fecha", "venta"]].rename(columns={"venta": "dolar_mep"})


@st.cache_data(ttl=60 * 60 * 12)   # Guarda los datos 12 horas para no descargarlos en cada clic
def cargar_datos():
    """Descarga y une todos los datos. Devuelve el IPC mensual y la tabla diaria de mercado."""
    # IPC: el último mes publicado define hasta dónde llega el análisis
    ipc = descargar_serie_oficial("148.3_INIVELNAL_DICI_M_26", "ipc", desde=DATOS_DESDE)
    fecha_fin = ipc["fecha"].max() + pd.offsets.MonthEnd(0)   # Último día del último mes con inflación

    hasta = fecha_fin.strftime("%Y-%m-%d")
    tasa_pf = descargar_serie_bcra(12, "tasa_pf", "2020-01-01", hasta)
    mep = descargar_mep()

    mercado = pd.merge(mep, tasa_pf, on="fecha", how="inner")
    mercado = mercado[mercado["fecha"] <= fecha_fin].reset_index(drop=True)
    return ipc, mercado, fecha_fin


# ===============================================
# FUNCIONES DE SIMULACIÓN (las mismas del cuaderno)
# ===============================================
def simular_plazo_fijo(monto, datos, plazo_dias=30):
    """Simula un plazo fijo que se renueva al vencimiento (capital + intereses)"""
    fecha = datos["fecha"].iloc[0]
    fecha_limite = datos["fecha"].iloc[-1]
    historial = [{"fecha": fecha, "plazo_fijo": monto}]
    while fecha + pd.Timedelta(days=plazo_dias) <= fecha_limite:
        tasa = datos.loc[datos["fecha"] >= fecha, "tasa_pf"].iloc[0]
        monto = monto + monto * (tasa / 100) * plazo_dias / 365
        fecha = fecha + pd.Timedelta(days=plazo_dias)
        historial.append({"fecha": fecha, "plazo_fijo": monto})
    return pd.DataFrame(historial)


def simular_dolar(monto, datos):
    """Simula la compra de dólares MEP el primer día y su valor en pesos día a día"""
    dolares = monto / datos["dolar_mep"].iloc[0]
    return dolares * datos["dolar_mep"]


def ipc_diario(ipc, fechas):
    """Estima el IPC de cada día con una spline cúbica (cada IPC mensual se ubica al cierre de su mes)"""
    anclas = ipc.copy()
    anclas["fecha_cierre"] = anclas["fecha"] + pd.DateOffset(months=1)
    dia_cero = anclas["fecha_cierre"].iloc[0]
    x_anclas = (anclas["fecha_cierre"] - dia_cero).dt.days
    x_diario = (fechas - dia_cero).dt.days
    spline = CubicSpline(x_anclas, anclas["ipc"])
    return spline(x_diario)


def veredicto(rendimiento_real):
    """Clasifica una inversión según su rendimiento real"""
    if rendimiento_real > 1:
        return "✅ Le ganó a la inflación"
    elif rendimiento_real >= -1:
        return "➖ Empató con la inflación"
    else:
        return "❌ Perdió contra la inflación"


def tasa_mensual(valor_final, valor_inicial, dias):
    """Convierte un rendimiento total en su equivalente por mes (interés compuesto), en %"""
    meses = dias / 30.4375   # Cantidad promedio de días de un mes
    return ((valor_final / valor_inicial) ** (1 / meses) - 1) * 100


def rendimiento_necesario(precio_inicial, monto, inflacion, dias):
    """
    Calcula qué rendimiento hacía falta para poder comprar hoy un bien

    Input:
        precio_inicial: precio del bien en la fecha de inicio, en pesos
        monto: dinero que tenía el ahorrista, en pesos
        inflacion: inflación acumulada del período, en %
        dias: cantidad de días del período

    Output:
        precio_hoy: precio estimado del bien hoy (se supone que subió igual que la inflación)
        total: rendimiento necesario en todo el período, en %
        mensual: el mismo rendimiento expresado por mes, en %
    """
    precio_hoy = precio_inicial * (1 + inflacion / 100)
    total = (precio_hoy / monto - 1) * 100
    mensual = tasa_mensual(precio_hoy, monto, dias)
    return precio_hoy, total, mensual


def pesos(valor):
    """Formato argentino: $ 1.234.567"""
    return "$ " + f"{valor:,.0f}".replace(",", ".")


def pesos_texto(valor):
    """Igual que pesos(), pero para usar dentro de textos: Streamlit interpreta $ como fórmula matemática"""
    return pesos(valor).replace("$", "\\$")


def porcentaje(valor, con_signo=False):
    """Formato argentino: 1.234,5% (con_signo=True agrega el + a los positivos)"""
    texto = f"{valor:+,.1f}" if con_signo else f"{valor:,.1f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".") + "%"


# ===============================================
# INTERFAZ: ENCABEZADO
# ===============================================
st.title("💰 ¿Qué me conviene con mis pesos?")
st.write(
    "Elegí cuánto dinero tenías y desde cuándo. Te mostramos qué hubiera pasado con tu "
    "**poder de compra** si lo dejabas quieto, lo ponías en plazo fijo o comprabas dólares."
)

try:
    ipc, mercado, fecha_fin = cargar_datos()
except Exception:
    st.error("No se pudieron descargar los datos en este momento. Probá de nuevo en unos minutos.")
    st.stop()


# ===============================================
# INTERFAZ: DATOS QUE ELIGE EL USUARIO
# ===============================================
col1, col2 = st.columns(2)
with col1:
    monto = st.number_input("¿Cuánto dinero tenías? (en pesos)", min_value=10_000,
                            max_value=1_000_000_000, value=1_000_000, step=100_000)
with col2:
    fecha_inicio = st.date_input("¿Desde cuándo?", value=datetime.date(2024, 1, 2),
                                 min_value=datetime.date(2020, 1, 2),
                                 max_value=(fecha_fin - pd.DateOffset(months=3)).date(),
                                 format="DD/MM/YYYY")

st.caption(f"Resultados calculados hasta el {fecha_fin.strftime('%d/%m/%Y')} "
           f"(último mes con inflación publicada por el INDEC).")


# ===============================================
# CÁLCULOS
# ===============================================
datos = mercado[mercado["fecha"] >= pd.Timestamp(fecha_inicio)].reset_index(drop=True)

evolucion = datos[["fecha"]].copy()
evolucion["Colchón"] = monto
evolucion["Dólar MEP"] = simular_dolar(monto, datos).values
pf = simular_plazo_fijo(monto, datos)
evolucion = pd.merge_asof(evolucion, pf, on="fecha").rename(columns={"plazo_fijo": "Plazo fijo"})

# Términos reales: todo expresado en pesos del día de inicio
evolucion["ipc"] = ipc_diario(ipc, evolucion["fecha"])
ipc_inicio = evolucion["ipc"].iloc[0]
reales = pd.DataFrame({"fecha": evolucion["fecha"]})
for alternativa in ALTERNATIVAS:
    reales[alternativa] = evolucion[alternativa] * ipc_inicio / evolucion["ipc"]

inflacion_periodo = (evolucion["ipc"].iloc[-1] / ipc_inicio - 1) * 100
necesario = monto * (1 + inflacion_periodo / 100)

resumen = pd.DataFrame(index=ALTERNATIVAS)
resumen["Final en pesos"] = [evolucion[a].iloc[-1] for a in ALTERNATIVAS]
resumen["Poder de compra (%)"] = [(reales[a].iloc[-1] / monto - 1) * 100 for a in ALTERNATIVAS]
resumen["Veredicto"] = resumen["Poder de compra (%)"].apply(veredicto)


# ===============================================
# INTERFAZ: RESULTADOS
# ===============================================
st.divider()
st.subheader("Resultado")

st.write(f"En el período, los precios subieron **{porcentaje(inflacion_periodo)}**. "
         f"Para comprar lo mismo que con {pesos_texto(monto)}, hoy necesitás **{pesos_texto(necesario)}**.")

c1, c2, c3 = st.columns(3)
for columna, alternativa in zip([c1, c2, c3], ALTERNATIVAS):
    columna.metric(alternativa, pesos(resumen.loc[alternativa, "Final en pesos"]),
                   f"{porcentaje(resumen.loc[alternativa, 'Poder de compra (%)'], con_signo=True)} poder de compra")

# Mensaje principal
mejor = resumen["Poder de compra (%)"].idxmax()
if (resumen["Poder de compra (%)"] < -1).all():
    st.warning(f"**Ninguna de las tres alternativas conservó tu poder de compra.** "
               f"La que menos perdió fue **{mejor}** ({porcentaje(resumen.loc[mejor, 'Poder de compra (%)'])}).")
else:
    st.success(f"La mejor alternativa en este período fue **{mejor}** "
               f"({porcentaje(resumen.loc[mejor, 'Poder de compra (%)'], con_signo=True)} de poder de compra).")

st.dataframe(
    resumen.style.format({"Final en pesos": pesos, "Poder de compra (%)": lambda v: porcentaje(v, con_signo=True)}),
    width="stretch",
)

st.subheader("¿Cuánto podés comprar con tu dinero?")
st.caption(f"Valor de cada alternativa en pesos del {fecha_inicio.strftime('%d/%m/%Y')}. "
           f"La línea punteada marca tus {pesos_texto(monto)} iniciales: por debajo, perdiste poder de compra.")

# Pasamos la tabla de "ancha" (una columna por alternativa) a "larga" (una fila por fecha y alternativa)
largo = reales.melt(id_vars="fecha", var_name="Alternativa", value_name="Valor")
largo["Monto"] = largo["Valor"].apply(pesos)   # Texto que se muestra al pasar el mouse

# Una marca en el eje cada tantos meses, según el largo del período (unas 8 marcas en total)
meses_periodo = (reales["fecha"].iloc[-1] - reales["fecha"].iloc[0]).days / 30
paso = max(1, round(meses_periodo / 8))

lineas = alt.Chart(largo).mark_line().encode(
    x=alt.X("fecha:T", title=None,
            axis=alt.Axis(tickCount={"interval": "month", "step": paso},
                          labelExpr=MESES + "[month(datum.value)] + ' ' + year(datum.value)")),
    y=alt.Y("Valor:Q", title=None,
            axis=alt.Axis(labelExpr="'$ ' + replace(format(datum.value, ',.0f'), /,/g, '.')")),
    color=alt.Color("Alternativa:N", scale=alt.Scale(domain=ALTERNATIVAS, range=COLORES),
                    legend=alt.Legend(orient="bottom", title=None)),
    tooltip=[alt.Tooltip("fecha:T", title="Fecha", format="%d/%m/%Y"), "Alternativa", "Monto"],
)
referencia = alt.Chart(pd.DataFrame({"Valor": [monto]})).mark_rule(strokeDash=[5, 5], color="#d1495b").encode(y="Valor:Q")
st.altair_chart(lineas + referencia)


# ===============================================
# INTERFAZ: ¿ESTABAS AHORRANDO PARA COMPRAR ALGO?
# ===============================================
st.divider()
st.subheader("🎯 ¿Estabas ahorrando para comprar algo?")
st.write("Contanos qué querías comprar y cuánto costaba en ese momento. Te mostramos qué rendimiento "
         "necesitabas para poder comprarlo hoy y si cada alternativa te alcanzaba.")

b1, b2 = st.columns(2)
with b1:
    bien = st.text_input("¿Qué querías comprar?", value="un televisor")
with b2:
    precio_inicial = st.number_input(f"¿Cuánto costaba el {fecha_inicio.strftime('%d/%m/%Y')}? (en pesos)",
                                     min_value=10_000, max_value=10_000_000_000, value=int(monto), step=100_000)

dias = (evolucion["fecha"].iloc[-1] - evolucion["fecha"].iloc[0]).days
precio_hoy, rend_total, rend_mensual = rendimiento_necesario(precio_inicial, monto, inflacion_periodo, dias)

st.write(f"El {fecha_inicio.strftime('%d/%m/%Y')}, {bien} costaba {pesos_texto(precio_inicial)}. "
         f"Si su precio subió igual que la inflación, hoy cuesta **{pesos_texto(precio_hoy)}**.")

if precio_hoy <= monto:
    st.success(f"Con tus {pesos_texto(monto)} hoy te alcanza para comprarlo, incluso sin haberlos invertido.")
else:
    st.write(f"Para poder comprarlo hoy, tus {pesos_texto(monto)} tenían que rendir **{porcentaje(rend_total)}** "
             f"en el período, es decir, **{porcentaje(rend_mensual)} por mes**.")

# ¿Qué parte del bien se puede comprar hoy con cada alternativa?
for alternativa in ALTERNATIVAS:
    final = resumen.loc[alternativa, "Final en pesos"]
    cobertura = final / precio_hoy                 # 1 = alcanza justo; 0,5 = alcanza para la mitad
    rindio = tasa_mensual(final, monto, dias)
    if cobertura >= 1:
        detalle = f"✅ te alcanza y te sobran {pesos_texto(final - precio_hoy)}"
    else:
        detalle = f"❌ te alcanza para el {cobertura:.0%} (te faltan {pesos_texto(precio_hoy - final)})"
    st.progress(min(cobertura, 1.0), text=f"**{alternativa}** · rindió {porcentaje(rindio)} por mes: {detalle}")

st.caption("Supuesto: el precio del bien aumentó igual que la inflación promedio (IPC). "
           "El precio de un bien en particular puede haber subido más o menos que el promedio.")


# ===============================================
# INTERFAZ: EXPLICACIÓN Y CIERRE
# ===============================================
with st.expander("¿Qué significa \"poder de compra\"?"):
    st.write(
        "Tener más pesos no significa estar mejor: si los precios suben más que tus pesos, "
        "con ese dinero podés comprar menos cosas que antes. El **poder de compra** mide justamente eso: "
        "cuánto podés comprar hoy con tu dinero, comparado con lo que comprabas al inicio. "
        "Por eso una inversión puede \"ganar\" pesos y, al mismo tiempo, hacerte perder."
    )

st.info(
    "**Las alternativas más conocidas no siempre protegen tus ahorros.** "
    "Antes de decidir, informate y consultá con un asesor financiero matriculado."
)

st.caption(
    "Fuentes: INDEC (IPC, vía datos.gob.ar), BCRA (tasa de plazo fijo a 30 días) y argentinadatos.com (dólar MEP). "
    "Supuestos: plazo fijo a 30 días renovado al vencimiento con la tasa promedio informada por el BCRA; "
    "no se consideran impuestos, comisiones ni la diferencia entre precio de compra y venta del dólar. "
    "Resultados pasados no garantizan resultados futuros. Herramienta con fines educativos."
)
