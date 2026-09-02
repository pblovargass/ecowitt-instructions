#!/usr/bin/env python3
"""
Descarga datos historicos desde la API de Ecowitt y los guarda en un CSV local,
ademas de actualizar un grafico de temperatura y humedad.

Pensado para correr dentro de un workflow de GitHub Actions con un trigger
tipo "schedule" (cron), pero tambien funciona corriendolo a mano.

Variables de entorno REQUERIDAS (en GitHub: se configuran como Secrets del repo,
nunca se escriben directo en este archivo):
    ECOWITT_APPLICATION_KEY
    ECOWITT_API_KEY
    ECOWITT_MAC            -> MAC address de la estacion (formato AA:BB:CC:DD:EE:FF)
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
 
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
 
CHILE_TZ = ZoneInfo("America/Santiago")
 
APPLICATION_KEY = (os.environ.get("ECOWITT_APPLICATION_KEY") or "").strip()
API_KEY = (os.environ.get("ECOWITT_API_KEY") or "").strip()
MAC = (os.environ.get("ECOWITT_MAC") or "").strip()
LOOKBACK_HOURS = float(os.environ.get("ECOWITT_LOOKBACK_HOURS") or "2")
BACKFILL_HOURS = float(os.environ.get("ECOWITT_BACKFILL_HOURS") or "60")
CALLBACK_MANUAL = (os.environ.get("ECOWITT_CALLBACK") or "").strip()
 
if not all([APPLICATION_KEY, API_KEY, MAC]):
    sys.exit(
        "Faltan variables de entorno: ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY o ECOWITT_MAC.\n"
        "En GitHub Actions estas se leen desde Secrets; si corres el script a mano, "
        "expórtalas primero en tu terminal (nunca las escribas en el código)."
    )
 
DATA_DIR = Path("data/ecowitt-sj")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "ecowitt_data.csv"
WIDE_CSV_PATH = DATA_DIR / "ecowitt_data_wide.csv"
PLOT_PATH = DATA_DIR / "temperatura_humedad.png"
 
REALTIME_URL = "https://api.ecowitt.net/api/v3/device/real_time"
HISTORY_URL = "https://api.ecowitt.net/api/v3/device/history"
 
AUTH = {
    "application_key": APPLICATION_KEY,
    "api_key": API_KEY,
    "mac": MAC,
    "temp_unitid": 1, 
}

GRUPOS_EXCLUIDOS = {"battery"}
 
 
def descubrir_grupos():
    """Consulta real_time (que si acepta call_back=all) para saber que grupos
    de sensores reporta esta estacion, y devolverlos como lista."""
    if CALLBACK_MANUAL:
        grupos = [g.strip() for g in CALLBACK_MANUAL.split(",") if g.strip()]
        print(f"Usando grupos definidos manualmente: {grupos}")
        return grupos
 
    params = dict(AUTH, call_back="all")
    resp = requests.get(REALTIME_URL, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
 
    if payload.get("code") != 0:
        sys.exit(f"Error consultando real_time: {payload.get('msg')}")
 
    data = payload.get("data", {}) or {}
    grupos = [
        k for k, v in data.items()
        if isinstance(v, dict) and k not in GRUPOS_EXCLUIDOS
    ]
 
    if not grupos:
        sys.exit("La estacion no reporto ningun grupo de sensores en real_time.")
 
    print(f"Grupos detectados en la estacion: {grupos}")
    return grupos
 
 
def flatten(node, prefix, rows):
    if isinstance(node, dict):
        if "list" in node and isinstance(node["list"], dict):
            unit = node.get("unit", "")
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
 
 
def resample_hourly(df):
    """Agrupa lecturas de 5 minutos en un promedio por hora (por variable)."""
    if df.empty:
        return df
    df = df.copy()
    df["timestamp"] = df["timestamp"].dt.floor("h")
    return (
        df.groupby(["timestamp", "variable", "unit"], as_index=False, dropna=False)["value"]
        .mean()
    )


def pedir_historico(grupos, start_date, end_date):
    rows = []
    ultimo_payload = None
 
    def _consulta(call_back):
        params = dict(
            AUTH,
            call_back=call_back,
            start_date=start_date.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end_date.strftime("%Y-%m-%d %H:%M:%S"),
            cycle_type="auto",
        )
        r = requests.get(HISTORY_URL, params=params, timeout=60)
        r.raise_for_status()
        return r.json()
 
    payload = _consulta(",".join(grupos))
    ultimo_payload = payload
 
    if payload.get("code") == 0:
        flatten(payload.get("data", {}) or {}, "", rows)
        if rows:
            return rows, ultimo_payload
        print("Aviso: la consulta conjunta respondio 'success' pero sin datos. "
              "Probando grupo por grupo para aislar cual (si alguno) tiene historial...")
    else:
        print(f"Aviso: la consulta conjunta fallo ({payload.get('msg')}). "
              f"Reintentando grupo por grupo...")
 
    for grupo in grupos:
        p = _consulta(grupo)
        ultimo_payload = p
        if p.get("code") != 0:
            print(f"  - {grupo}: error ({p.get('msg')})")
            continue
        antes = len(rows)
        flatten(p.get("data", {}) or {}, "", rows)
        n = len(rows) - antes
        estado = f"{n} lecturas" if n else "0 lecturas (success, pero data vacia)"
        print(f"  - {grupo}: {estado}")
 
    return rows, ultimo_payload
 
 
def get_last_timestamp():
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
    grupos = descubrir_grupos()
 
    last_ts = get_last_timestamp()
    end_date = datetime.utcnow()
    inicio_backfill = end_date - timedelta(hours=BACKFILL_HOURS)
 
    if last_ts is not None:
        start_date_anclado = last_ts - timedelta(minutes=10)
        start_date = min(start_date_anclado, inicio_backfill)
    else:
        start_date = end_date - timedelta(hours=LOOKBACK_HOURS)
 
    print(f"Pidiendo datos desde {start_date} hasta {end_date} (UTC) "
          f"[backfill activo: ultimas {BACKFILL_HOURS} h siempre revisadas]")
 
    rows, ultimo_payload = pedir_historico(grupos, start_date, end_date)
 
    if not rows:
        print("No hay datos nuevos en el rango solicitado.")
        print("--- Respuesta cruda de la API (diagnostico) ---")
        print(f"code: {ultimo_payload.get('code')}   msg: {ultimo_payload.get('msg')}")
        data_cruda = ultimo_payload.get("data", {}) or {}
        if not data_cruda:
            print("El campo 'data' vino vacio: la API no tiene datos archivados "
                  "para este rango (puede ser demora de archivado, o el rango "
                  "solicitado cae antes de que la estacion empezara a reportar).")
        else:
            print(f"Claves presentes en 'data': {list(data_cruda.keys())}")
            print(json.dumps(data_cruda, indent=2, default=str)[:2000])
        print("------------------------------------------------")
        return
 
    new_df = resample_hourly(pd.DataFrame(rows))

    if CSV_PATH.exists():
        old_df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
        combined = pd.concat([old_df, new_df], ignore_index=True)
        # keep="last": si una hora ya estaba en el CSV pero esta corrida trajo
        # lecturas mas completas para esa hora, el promedio nuevo reemplaza al viejo.
        combined.drop_duplicates(subset=["timestamp", "variable"], keep="last", inplace=True)
    else:
        combined = new_df

    combined.sort_values("timestamp", inplace=True)
    combined.to_csv(CSV_PATH, index=False)
    print(f"Guardadas {len(new_df)} horas nuevas ({len(combined)} filas totales) en {CSV_PATH}")
 
    update_wide_csv(combined)
    update_plot(combined)
 
 
def a_hora_local(serie_timestamp_utc):
    return (
        serie_timestamp_utc
        .dt.tz_localize("UTC")
        .dt.tz_convert(CHILE_TZ)
        .dt.tz_localize(None)
    )
 
 
def update_wide_csv(df):
    df = df.copy()
    df = df[~df["variable"].str.endswith(("_high", "_low"))]
    df["timestamp_local"] = a_hora_local(df["timestamp"])

    pivot = df.pivot_table(index="timestamp_local", columns="variable", values="value")
    pivot.index.name = "timestamp_local (America/Santiago)"
    pivot.sort_index(inplace=True)
    pivot.to_csv(WIDE_CSV_PATH)
    print(f"CSV en formato ancho actualizado en {WIDE_CSV_PATH} (hora de Chile)")
 
 
VARIABLES_TEMPERATURA = ["indoor.temperature", "temp_ch1.temperature", "temp_ch2.temperature"] # pa separar gráficos en temperatura y humedad
VARIABLES_HUMEDAD = ["indoor.humidity", "soil_ch1.soilmoisture", "soil_ch2.soilmoisture"] # así se visualiza mejor, cunado estaban juntos no se veía bien x la escala
 
 
def _graficar_panel(ax, df, variables, titulo, ylabel):
    subset = df[df["variable"].isin(variables)].copy()
    if subset.empty:
        ax.set_title(f"{titulo} (sin datos)")
        return
    subset["timestamp_local"] = a_hora_local(subset["timestamp"])
    pivot = subset.pivot_table(index="timestamp_local", columns="variable", values="value")
    pivot.plot(ax=ax)
    ax.set_ylabel(ylabel)
    ax.set_title(titulo)
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize="small")
 
 
def update_plot(df):
    fig, (ax_temp, ax_hum) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
 
    _graficar_panel(ax_temp, df, VARIABLES_TEMPERATURA,
                     "Temperatura - Estacion Ecowitt", "Temperatura (°C)")
    _graficar_panel(ax_hum, df, VARIABLES_HUMEDAD,
                     "Humedad - Estacion Ecowitt", "Humedad (%)")
    
    ax_hum.set_xlabel("Fecha/hora (America/Santiago)")
    fig.tight_layout()
    fig.savefig(PLOT_PATH, dpi=150)
    plt.close(fig)
    print(f"Grafico actualizado en {PLOT_PATH}")
 
 
if __name__ == "__main__":
    main()
