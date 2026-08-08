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
 
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
 
APPLICATION_KEY = (os.environ.get("ECOWITT_APPLICATION_KEY") or "").strip()
API_KEY = (os.environ.get("ECOWITT_API_KEY") or "").strip()
MAC = (os.environ.get("ECOWITT_MAC") or "").strip()
LOOKBACK_HOURS = float(os.environ.get("ECOWITT_LOOKBACK_HOURS") or "2")
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
PLOT_PATH = DATA_DIR / "temperatura_humedad.png"
 
REALTIME_URL = "https://api.ecowitt.net/api/v3/device/real_time"
HISTORY_URL = "https://api.ecowitt.net/api/v3/device/history"
 
AUTH = {
    "application_key": APPLICATION_KEY,
    "api_key": API_KEY,
    "mac": MAC,
}
 
# Grupos que real_time reporta pero que no son series historicas utiles
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
    """Recorre el JSON anidado que devuelve Ecowitt y extrae cada serie
    timestamp -> valor, identificando los bloques que tienen 'list' y 'unit'."""
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
 
 
def pedir_historico(grupos, start_date, end_date):
    """Pide el historico a Ecowitt. Intenta con todos los grupos de una vez;
    si la API lo rechaza, reintenta grupo por grupo para no perder todo por
    culpa de un solo grupo invalido.
 
    Devuelve (rows, ultimo_payload_crudo) -- el payload crudo se usa solo
    para diagnostico cuando no se encuentran filas."""
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
    grupos = descubrir_grupos()
 
    last_ts = get_last_timestamp()
    end_date = datetime.utcnow()
 
    if last_ts is not None:
        # pequeno solape hacia atras para no perder datos si un run fallo
        start_date = last_ts - timedelta(minutes=10)
    else:
        start_date = end_date - timedelta(hours=LOOKBACK_HOURS)
 
    print(f"Pidiendo datos desde {start_date} hasta {end_date} (UTC)")
 
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
        print("No hay variables de temperatura/humedad para graficar.")
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
