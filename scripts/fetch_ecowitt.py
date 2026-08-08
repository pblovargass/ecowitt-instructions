#!/usr/bin/env python3

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

APPLICATION_KEY = os.environ.get("ECOWITT_APPLICATION_KEY")
API_KEY = os.environ.get("ECOWITT_API_KEY")
MAC = os.environ.get("ECOWITT_MAC")
LOOKBACK_HOURS = float(os.environ.get("ECOWITT_LOOKBACK_HOURS", "2"))

if not all([APPLICATION_KEY, API_KEY, MAC]):
    sys.exit(
        "Faltan variables de entorno: ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY o ECOWITT_MAC.\n"
        "En GitHub Actions estas se leen desde Secrets; si corres el script a mano, "
        "expórtalas primero en tu terminal (nunca las escribas en el código)."
    )

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "ecowitt_data.csv"
PLOT_PATH = DATA_DIR / "temperatura_humedad.png"

HISTORY_URL = "https://api.ecowitt.net/api/v3/device/history"


def flatten(node, prefix, rows):
    """Recorre el JSON anidado que devuelve Ecowitt y extrae cada serie
    timestamp -> valor, identificando los bloques que tienen 'list' y 'unit'."""
    if isinstance(node, dict):
        if "list" in node and "unit" in node:
            unit = node["unit"]
            for ts_str, value in node["list"].items():
                try:
                    rows.append({
                        "timestamp": datetime.fromtimestamp(int(ts_str)),
                        "variable": prefix,
                        "unit": unit,
                        "value": float(value),
                    })
                except (TypeError, ValueError):
                    continue
        else:
            for key, value in node.items():
                new_prefix = f"{prefix}.{key}" if prefix else key
                flatten(value, new_prefix, rows)


def get_last_timestamp():
    """Revisa el CSV existente para saber desde cuando pedir datos nuevos."""
    if not CSV_PATH.exists():
        return None
    try:
        df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
        if df.empty:
            return None
        return df["timestamp"].max()
    except Exception:
        return None


def main():
    last_ts = get_last_timestamp()
    end_date = datetime.utcnow()

    if last_ts is not None:
        start_date = last_ts - timedelta(minutes=10)
    else:
        start_date = end_date - timedelta(hours=LOOKBACK_HOURS)

    params = {
        "application_key": APPLICATION_KEY,
        "api_key": API_KEY,
        "mac": MAC,
        "call_back": "all",
        "start_date": start_date.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end_date.strftime("%Y-%m-%d %H:%M:%S"),
        "cycle_type": "auto",
    }

    resp = requests.get(HISTORY_URL, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("code") != 0:
        sys.exit(f"Error de la API de Ecowitt: {payload.get('msg')}")

    rows = []
    flatten(payload.get("data", {}), "", rows)

    if not rows:
        print("No hay datos nuevos en el rango solicitado.")
        return

    new_df = pd.DataFrame(rows)

    if CSV_PATH.exists():
        old_df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
        combined = pd.concat([old_df, new_df], ignore_index=True)
        combined.drop_duplicates(subset=["timestamp", "variable"], inplace=True)
    else:
        combined = new_df

    combined.sort_values("timestamp", inplace=True)
    combined.to_csv(CSV_PATH, index=False)
    print(f"Guardadas {len(new_df)} lecturas nuevas ({len(combined)} filas totales) en {CSV_PATH}")

    update_plot(combined)


def update_plot(df):
    """Genera/actualiza un grafico simple con las variables de temperatura y humedad."""
    subset = df[df["variable"].str.contains("temperature|humidity", case=False, na=False)]
    if subset.empty:
        return

    pivot = subset.pivot_table(index="timestamp", columns="variable", values="value")

    fig, ax = plt.subplots(figsize=(12, 6))
    pivot.plot(ax=ax)
    ax.set_xlabel("Fecha/hora (UTC)")
    ax.set_ylabel("Valor")
    ax.set_title("Temperatura y humedad - Estacion Ecowitt")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize="small")
    fig.tight_layout()
    fig.savefig(PLOT_PATH, dpi=150)
    plt.close(fig)
    print(f"Grafico actualizado en {PLOT_PATH}")


if __name__ == "__main__":
    main()
