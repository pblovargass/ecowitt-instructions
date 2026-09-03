#!/usr/bin/env python3
"""
Compara la temperatura de suelo medida por la estacion Ecowitt (campus San
Joaquin, UC) contra la Red Agrometeorologica de INIA (estacion La Platina,
La Pintana), como validacion cruzada.

INIA reporta maximo y minimo diario de temperatura de suelo a 10cm, en huso
horario fijo UTC-4. Este script agrega los promedios horarios de Ecowitt al
mismo formato (maximo/minimo diario, UTC-4 fijo) para poder compararlos.

Sensores propios usados:
    temp_ch1.temperature -> enterrado 10-15cm (comparable directo con INIA TS10)
    temp_ch2.temperature -> enterrado 20-25cm (comparacion secundaria, mas profundo)
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
ECOWITT_CSV = BASE_DIR / "data" / "ecowitt-sj" / "ecowitt_data.csv"
INIA_MAX_CSV = BASE_DIR / "data" / "Datos_INIA" / "datos_2026_INIA-4_TS10_MAX.csv"
INIA_MIN_CSV = BASE_DIR / "data" / "Datos_INIA" / "datos_2026_INIA-4_TS10_MIN.csv"

OUT_DIR = BASE_DIR / "data" / "analisis_inia"
OUT_CSV = OUT_DIR / "comparacion_temp_suelo.csv"
OUT_PNG = OUT_DIR / "comparacion_temp_suelo.png"

SENSORES_PROPIOS = {
    "temp_ch1.temperature": "ch1 (10-15cm)",
    "temp_ch2.temperature": "ch2 (20-25cm)",
}


def leer_inia(path, col_valor, col_pct):
    """Salta las filas de metadata/disclaimer y deja fecha + valor + %datos."""
    df = pd.read_csv(path, skiprows=5)
    df.columns = ["fecha", col_valor, col_pct]
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d-%m-%Y", errors="coerce")
    df = df.dropna(subset=["fecha"])
    df["fecha"] = df["fecha"].dt.date
    df[col_valor] = pd.to_numeric(df[col_valor], errors="coerce")
    df[col_pct] = pd.to_numeric(df[col_pct], errors="coerce")
    return df.dropna(subset=[col_valor])


def diario_propio(df_ecowitt):
    """Maximo/minimo diario (UTC-4 fijo) por sensor propio, a partir de los
    promedios horarios ya guardados. OJO: el maximo de un promedio horario
    subestima el maximo instantaneo real (y el minimo lo sobreestima)."""
    df = df_ecowitt[df_ecowitt["variable"].isin(SENSORES_PROPIOS)].copy()
    df["fecha"] = (df["timestamp"] - pd.Timedelta(hours=4)).dt.date
    diario = (
        df.groupby(["fecha", "variable"])["value"]
        .agg(propio_max="max", propio_min="min", horas_con_dato="count")
        .reset_index()
    )
    return diario


def metricas(diff):
    diff = diff.dropna()
    if diff.empty:
        return dict(n=0, bias=float("nan"), mae=float("nan"), rmse=float("nan"))
    return dict(
        n=len(diff),
        bias=diff.mean(),
        mae=diff.abs().mean(),
        rmse=(diff**2).mean() ** 0.5,
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    inia_max = leer_inia(INIA_MAX_CSV, "inia_max", "inia_max_pct")
    inia_min = leer_inia(INIA_MIN_CSV, "inia_min", "inia_min_pct")
    inia = inia_max.merge(inia_min, on="fecha", how="outer")

    ecowitt = pd.read_csv(ECOWITT_CSV, parse_dates=["timestamp"])
    propio = diario_propio(ecowitt)

    resultados = []
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)

    for variable, etiqueta in SENSORES_PROPIOS.items():
        sub = propio[propio["variable"] == variable]
        comparado = inia.merge(sub, on="fecha", how="inner")
        comparado["variable_propia"] = variable
        comparado["diff_max"] = comparado["propio_max"] - comparado["inia_max"]
        comparado["diff_min"] = comparado["propio_min"] - comparado["inia_min"]
        resultados.append(comparado)

        m_max = metricas(comparado["diff_max"])
        m_max_100 = metricas(comparado.loc[comparado["inia_max_pct"] == 100, "diff_max"])
        m_min = metricas(comparado["diff_min"])

        print(f"\n=== {etiqueta} ({variable}) vs INIA TS10 ===")
        print(f"Dias comparados: {m_max['n']}")
        print(f"MAXIMOS  -> bias={m_max['bias']:+.2f}C  MAE={m_max['mae']:.2f}C  RMSE={m_max['rmse']:.2f}C")
        print(f"           (solo dias con 100% datos INIA: bias={m_max_100['bias']:+.2f}C, n={m_max_100['n']})")
        print(f"MINIMOS  -> bias={m_min['bias']:+.2f}C  MAE={m_min['mae']:.2f}C  RMSE={m_min['rmse']:.2f}C")

        axes[0].plot(comparado["fecha"], comparado["propio_max"], label=f"{etiqueta} (propio)")
        axes[1].plot(comparado["fecha"], comparado["propio_min"], label=f"{etiqueta} (propio)")

    axes[0].plot(inia["fecha"], inia["inia_max"], label="INIA TS10 (La Platina)", color="black", linestyle="--")
    axes[1].plot(inia["fecha"], inia["inia_min"], label="INIA TS10 (La Platina)", color="black", linestyle="--")
    axes[0].set_title("Temperatura de suelo - Maximo diario")
    axes[1].set_title("Temperatura de suelo - Minimo diario")
    for ax in axes:
        ax.set_ylabel("Temperatura (C)")
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize="small")
    axes[1].set_xlabel("Fecha (UTC-4)")
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=150)
    plt.close(fig)

    tabla = pd.concat(resultados, ignore_index=True)
    tabla.sort_values(["variable_propia", "fecha"], inplace=True)
    tabla.to_csv(OUT_CSV, index=False)

    print(f"\nTabla diaria guardada en {OUT_CSV}")
    print(f"Grafico guardado en {OUT_PNG}")


if __name__ == "__main__":
    main()
