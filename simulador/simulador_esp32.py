"""
Simulador del ESP32 del Taller.
Publica por MQTT lo mismo que haria el firmware ESPHome y obedece los reles.

Comandos por teclado:
  t 31    fija la temperatura en 31 C
  h 70    fija la humedad en 70 %
  p       abre/cierra la puerta
  a       alterna la generacion automatica de valores
  s       muestra el estado
  q       salir

Las credenciales NO van en el codigo. Antes de ejecutar (PowerShell):
  $env:MQTT_BROKER = "IP_DE_HOME_ASSISTANT"
  $env:MQTT_USER   = "mqtt_taller"
  $env:MQTT_PASS   = "CLAVE_DE_mqtt_taller"
  python simulador_esp32.py
"""
import os, time, random, threading
import paho.mqtt.client as mqtt

# --- Configuracion: se lee de variables de entorno ---
BROKER = os.environ["MQTT_BROKER"]          # IP de la VM-HA
PORT = int(os.getenv("MQTT_PORT", "1883"))
USER = os.environ["MQTT_USER"]              # usuario dedicado mqtt_taller
PASS = os.environ["MQTT_PASS"]

# --- Topics (los mismos que usa el YAML de ESPHome y Home Assistant) ---
T_TEMP, T_HUM = "taller/sensor/temp", "taller/sensor/humedad"
T_PUERTA = "taller/puerta/estado"             # ON = abierta, OFF = cerrada
T_ESTADO = "taller/estado"                    # online / offline (disponibilidad)
RELES = {"ventilador": "taller/ventilador", "luz": "taller/luz"}

# Estado simulado de los sensores
st = {"temp": 24.0, "hum": 45.0, "puerta": "OFF", "auto": True}
lock = threading.Lock()

cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="esp32-simulado")
cli.username_pw_set(USER, PASS)
# Last will: si el programa se cae sin avisar, el broker publica "offline"
cli.will_set(T_ESTADO, "offline", retain=True)


def on_connect(c, userdata, flags, rc, props=None):
    """Al conectar: avisa online, se suscribe a los reles y publica el estado."""
    print("\n[OK] Conectado al broker, codigo:", rc)
    c.publish(T_ESTADO, "online", retain=True)
    for base in RELES.values():
        c.subscribe(base + "/control")
        c.publish(base + "/estado", "OFF", retain=True)
    publicar_todo()


def on_message(c, userdata, msg):
    """Recibe ON/OFF de Home Assistant y confirma el nuevo estado del rele."""
    orden = msg.payload.decode().strip().upper()
    for nombre, base in RELES.items():
        if msg.topic == base + "/control" and orden in ("ON", "OFF"):
            print(f"\n>>> RELE {nombre.upper()} -> {orden}")
            c.publish(base + "/estado", orden, retain=True)


def publicar_todo():
    """Publica temperatura, humedad y puerta."""
    with lock:
        cli.publish(T_TEMP, f"{st['temp']:.1f}")
        cli.publish(T_HUM, f"{st['hum']:.1f}")
        cli.publish(T_PUERTA, st["puerta"], retain=True)
    print(f"[Enviado] Temp: {st['temp']} | Hum: {st['hum']} | Puerta: {st['puerta']}")


def bucle_sensores():
    """Cada 10 s varia levemente los valores (modo automatico) y los publica."""
    while True:
        time.sleep(10)
        with lock:
            if st["auto"]:
                st["temp"] = round(min(40, max(10, st["temp"] + random.uniform(-0.4, 0.4))), 1)
                st["hum"] = round(min(95, max(20, st["hum"] + random.uniform(-1, 1))), 1)
        if st["auto"]:
            publicar_todo()


cli.on_connect = on_connect
cli.on_message = on_message
cli.connect(BROKER, PORT, keepalive=30)
cli.loop_start()   # hilo de red de paho: reconecta solo si se cae el broker
threading.Thread(target=bucle_sensores, daemon=True).start()

print("\n--- SIMULADOR ESP32 INICIADO ---")
print("Comandos: t <num> | h <num> | p | a | s | q")
print("Esperando ordenes...\n")

try:
    while True:
        try:
            entrada = input("> ").strip()
            if not entrada:
                continue

            partes = entrada.split()
            cmd = partes[0].lower()

            with lock:
                if cmd == "t" and len(partes) > 1:
                    st["temp"] = float(partes[1])
                    st["auto"] = False
                    print(f"-> Temperatura ajustada a {st['temp']} C")
                elif cmd == "h" and len(partes) > 1:
                    st["hum"] = float(partes[1])
                    st["auto"] = False
                    print(f"-> Humedad ajustada a {st['hum']} %")
                elif cmd == "p":
                    st["puerta"] = "ON" if st["puerta"] == "OFF" else "OFF"
                    print("-> Puerta:", "ABIERTA" if st["puerta"] == "ON" else "CERRADA")
                elif cmd == "a":
                    st["auto"] = not st["auto"]
                    print("-> Modo automatico:", "ACTIVADO" if st["auto"] else "DESACTIVADO")
                elif cmd == "s":
                    print(f"-> Estado actual: {st}")
                elif cmd == "q":
                    break
                else:
                    print("Comando no reconocido.")
                    continue

            # Publica de inmediato despues de cada comando (no espera los 10 s)
            publicar_todo()

        except ValueError:
            print("Error: el valor debe ser un numero (ej: t 31).")
        except Exception as e:
            print("Error inesperado:", e)

except KeyboardInterrupt:
    pass
finally:
    print("\nCerrando conexion...")
    cli.publish(T_ESTADO, "offline", retain=True)
    cli.loop_stop()
    cli.disconnect()
