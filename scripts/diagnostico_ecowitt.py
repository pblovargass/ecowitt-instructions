#!/usr/bin/env python3
import os
import sys
import requests

APPLICATION_KEY = os.environ.get("ECOWITT_APPLICATION_KEY", "")
API_KEY = os.environ.get("ECOWITT_API_KEY", "")
MAC = os.environ.get("ECOWITT_MAC", "")

print("=" * 60)
print("DIAGNOSTICO DE CREDENCIALES ECOWITT")
print("=" * 60)

for nombre, valor in [
    ("ECOWITT_APPLICATION_KEY", APPLICATION_KEY),
    ("ECOWITT_API_KEY", API_KEY),
    ("ECOWITT_MAC", MAC),
]:
    if not valor:
        print(f"[FALTA]  {nombre} esta vacio o no existe.")
    else:
        limpio = valor.strip()
        aviso = ""
        if limpio != valor:
            aviso = "  <-- OJO: tiene espacios o saltos de linea al inicio/final"
        # no imprimimos el valor de las llaves, solo su largo
        if nombre == "ECOWITT_MAC":
            print(f"[OK]     {nombre} = '{limpio}' (largo: {len(limpio)}){aviso}")
        else:
            print(f"[OK]     {nombre} presente (largo: {len(limpio)}){aviso}")

print("-" * 60)

if not APPLICATION_KEY or not API_KEY:
    sys.exit("No se puede continuar sin las dos llaves.")

url = "https://api.ecowitt.net/api/v3/device/list"
params = {
    "application_key": APPLICATION_KEY.strip(),
    "api_key": API_KEY.strip(),
}

resp = requests.get(url, params=params, timeout=60)
data = resp.json()

print(f"Respuesta de la API: code={data.get('code')}  msg={data.get('msg')}")
print("-" * 60)

if data.get("code") != 0:
    print("La API rechazo la consulta. Si el mensaje habla de las llaves,")
    print("revisa que Application Key y API Key vengan de la MISMA cuenta.")
    sys.exit(1)

lista = data.get("data", {}).get("list", [])

if not lista:
    print("La cuenta NO tiene dispositivos vinculados.")
    print("Esto significa que las llaves son validas, pero la estacion esta")
    print("registrada en otra cuenta de Ecowitt. Revisa con que correo iniciaste sesion.")
    sys.exit(1)

print(f"Dispositivos vinculados a esta cuenta: {len(lista)}\n")
macs_encontradas = []
for d in lista:
    mac_dev = d.get("mac") or d.get("imei") or "(sin MAC/IMEI)"
    macs_encontradas.append(mac_dev)
    print(f"  Nombre : {d.get('name')}")
    print(f"  MAC    : {mac_dev}")
    print(f"  Tipo   : {d.get('type')}   ID: {d.get('id')}")
    print(f"  Modelo : {d.get('stationtype')}")
    print()

print("-" * 60)
mac_limpia = MAC.strip().upper()
coincide = [m for m in macs_encontradas if m and m.upper() == mac_limpia]

if coincide:
    print(f"BIEN: tu ECOWITT_MAC ('{mac_limpia}') coincide con un dispositivo.")
    print("Si el script principal sigue fallando, el problema es otro.")
else:
    print(f"PROBLEMA: tu ECOWITT_MAC ('{mac_limpia}') NO coincide con ninguno.")
    print("Copia exactamente uno de los valores 'MAC' listados arriba")
    print("y pegalo en el Secret ECOWITT_MAC.")
