"""
Asistente IA del Taller - puente entre Home Assistant y Ollama (IA 100 % local).

Flujo:
  1. El usuario escribe una pregunta en la pagina (puerto 8000).
  2. El script lee los estados actuales del taller desde la API REST de Home Assistant.
  3. Traduce cada estado segun el tipo de entidad (puerta abierta/cerrada, rele encendido/apagado).
  4. Evalua las reglas del taller y arma "hechos" ya calculados para el modelo.
  5. Envia todo a Ollama (Hermes3:3b) y devuelve la respuesta, el tiempo y los datos usados.

El asistente solo LEE estados: no llama a /api/services, asi que no acciona reles.
El token de Home Assistant se toma del archivo .env (chmod 600), nunca del codigo.
"""
import os
import time
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- Configuracion (variables de entorno cargadas desde .env) ---
HA_URL = os.environ["HA_URL"]               # http://IP_DE_HOME_ASSISTANT:8123
HA_TOKEN = os.environ["HA_TOKEN"]           # token de larga duracion (nunca en el codigo)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODELO = os.getenv("MODELO", "hermes3:3b")

# Umbrales de las reglas del taller (los mismos que las automatizaciones de HA)
TEMP_VENTILADOR = 28.0     # el ventilador se enciende por encima de este valor
LUX_MINIMA = 20.0          # la luz auxiliar se enciende por debajo de este valor
HUMEDAD_ALTA = 70.0        # por encima, riesgo de oxido y condensacion
TEMP_CRITICA = 35.0

# Entidades de Home Assistant: nombre -> (entity_id, tipo)
ENTIDADES = {
    "temperatura": ("sensor.taller_temperatura", "numero"),
    "humedad": ("sensor.taller_humedad", "numero"),
    "puerta": ("binary_sensor.taller_puerta", "puerta"),
    "ventilador": ("switch.taller_ventilador", "rele"),
    "luz auxiliar": ("switch.taller_luz", "rele"),
    "luz natural": ("input_number.luz_taller", "numero"),
}
# Traduccion segun el tipo de entidad (antes se usaba una sola tabla para todo)
TRADUCCION = {
    "puerta": {"on": "abierta", "off": "cerrada"},
    "rele": {"on": "encendido", "off": "apagado"},
}

app = FastAPI(title="Asistente del Taller")

# Archivos estaticos (imagen de fondo). Se publica SOLO la carpeta static/,
# nunca el directorio del proyecto, porque ahi esta el .env con el token.
CARPETA_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(CARPETA_STATIC):
    app.mount("/static", StaticFiles(directory=CARPETA_STATIC), name="static")


class Pregunta(BaseModel):
    texto: str


def leer_estados() -> dict:
    """Lee todas las entidades. Devuelve {nombre: {'valor', 'texto'}}."""
    datos = {}
    for nombre, (ent, tipo) in ENTIDADES.items():
        try:
            r = requests.get(f"{HA_URL}/api/states/{ent}",
                             headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=10)
            r.raise_for_status()
            d = r.json()
            estado = d["state"]
            if estado in ("unavailable", "unknown"):
                datos[nombre] = {"valor": None, "texto": "no disponible"}
            elif tipo == "numero":
                unidad = d["attributes"].get("unit_of_measurement", "")
                datos[nombre] = {"valor": float(estado), "texto": f"{float(estado):g} {unidad}".strip()}
            else:
                datos[nombre] = {"valor": estado, "texto": TRADUCCION[tipo].get(estado, estado)}
        except Exception as e:
            datos[nombre] = {"valor": None, "texto": f"no disponible ({type(e).__name__})"}
    return datos


def hechos_calculados(d: dict) -> list:
    """Evalua las reglas del taller con los datos reales y las explica en frases cortas."""
    h = []
    t, hum = d["temperatura"]["valor"], d["humedad"]["valor"]
    puerta, lux = d["puerta"]["valor"], d["luz natural"]["valor"]

    if t is None:
        h.append("No hay dato de temperatura, por eso no se puede evaluar la regla del ventilador.")
    elif t <= TEMP_VENTILADOR:
        h.append(f"La regla del ventilador NO se cumple porque la temperatura ({t:g} °C) no supera {TEMP_VENTILADOR:g} °C.")
    elif puerta == "on":
        h.append(f"La regla del ventilador NO se cumple porque, aunque hace {t:g} °C, la puerta está abierta. Para que se encienda hay que cerrar la puerta.")
    else:
        h.append(f"La regla del ventilador SE CUMPLE: hace {t:g} °C y la puerta está cerrada, por eso el ventilador debe estar encendido.")
    if t is not None and t >= TEMP_CRITICA:
        h.append(f"La temperatura es crítica ({t:g} °C). Conviene ventilar y revisar los equipos.")

    if hum is not None and hum >= HUMEDAD_ALTA:
        h.append(f"La humedad es alta ({hum:g} %). Hay riesgo de óxido en herramientas y de condensación en equipos. Se recomienda ventilar el taller.")
    elif hum is not None:
        h.append(f"La humedad es normal ({hum:g} %).")

    if puerta == "on" and lux is not None and lux < LUX_MINIMA:
        h.append(f"La regla de la luz SE CUMPLE: la puerta está abierta y hay {lux:g} lx, menos de {LUX_MINIMA:g} lx.")
    elif puerta == "on" and lux is not None:
        h.append(f"La regla de la luz NO se cumple porque hay {lux:g} lx de luz natural, suficiente.")
    elif puerta == "off":
        h.append("La regla de la luz NO se cumple porque la puerta está cerrada.")

    if t is not None and hum is not None and t <= TEMP_VENTILADOR and hum < HUMEDAD_ALTA:
        h.append("En resumen, el taller está en condiciones normales.")
    return h


# Prompt de sistema corto y directo: un modelo de 3B sigue mejor las instrucciones breves
SISTEMA = (
    "Sos el asistente de un taller de trabajo con domótica local. "
    "Respondé SIEMPRE en español, en dos o tres oraciones como máximo. "
    "Usá SOLO los datos y los hechos calculados que se te dan. Los hechos ya son correctos, "
    "no los contradigas ni inventes valores. Si falta un dato, decilo."
)


@app.post("/preguntar")
def preguntar(p: Pregunta):
    """Recibe la pregunta, agrega los datos reales y los hechos, y consulta a Ollama."""
    datos = leer_estados()
    lineas = "\n".join(f"- {n}: {v['texto']}" for n, v in datos.items())
    hechos = "\n".join(f"- {x}" for x in hechos_calculados(datos))
    ctx = f"Datos actuales del taller:\n{lineas}\n\nHechos calculados:\n{hechos}"
    payload = {
        "model": MODELO, "stream": False, "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_predict": 160, "num_ctx": 2048},
        "messages": [
            {"role": "system", "content": SISTEMA + "\n\n" + ctx},
            {"role": "user", "content": p.texto},
        ],
    }
    t0 = time.time()
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
    r.raise_for_status()
    return {"respuesta": r.json()["message"]["content"].strip(),
            "segundos": round(time.time() - t0, 1), "contexto": ctx}


@app.get("/", response_class=HTMLResponse)
def inicio():
    """Pagina web para hacer preguntas al asistente."""
    return r"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Asistente del Taller</title>
    <style>
        body { margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif; background-image: url('/static/fondo.jpg'); background-size: cover; background-position: center; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
        .container { background-color: rgba(255, 255, 255, 0.88); padding: 40px; border-radius: 30px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); width: 80%; max-width: 700px; text-align: center; margin: 30px 0; }
        h1 { color: #6b8e23; font-size: 36px; margin-bottom: 30px; font-weight: bold; }
        .search-box { display: flex; justify-content: center; margin-bottom: 20px; }
        input[type="text"] { width: 70%; padding: 12px 20px; font-size: 16px; border: 2px dashed #6b8e23; border-radius: 25px 0 0 25px; outline: none; background-color: rgba(255,255,255,0.9); }
        button { padding: 12px 30px; font-size: 16px; background-color: #a8c27a; color: #333; border: 2px dashed #6b8e23; border-left: none; border-radius: 0 25px 25px 0; cursor: pointer; font-weight: bold; transition: 0.3s; }
        button:hover { background-color: #8eae5e; }
        .response-box { background-color: #ffffff; padding: 20px; border-radius: 20px; min-height: 100px; text-align: left; white-space: pre-wrap; font-size: 16px; color: #333; margin-top: 20px; border: 1px solid #eee; }
        .loading { color: #888; font-style: italic; }
        details { text-align: left; margin-top: 12px; font-size: 13px; color: #555; }
        details pre { white-space: pre-wrap; background: #f6f6f6; padding: 10px; border-radius: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Asistente del Taller</h1>
        <div class="search-box">
            <input type="text" id="q" placeholder="Escribe tu pregunta aquí...">
            <button onclick="enviar()">Preguntar</button>
        </div>
        <div class="response-box" id="r">Esperando tu pregunta...</div>
        <details><summary>Datos que recibió el asistente</summary><pre id="c">-</pre></details>
    </div>
    <script>
        async function enviar() {
            const r = document.getElementById('r');
            const q = document.getElementById('q').value;
            if (!q) return;
            r.className = 'response-box loading';
            r.textContent = 'Pensando...';
            try {
                const response = await fetch('/preguntar', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({texto: q})
                });
                const data = await response.json();
                r.className = 'response-box';
                r.textContent = data.respuesta + '\n\n(Tiempo: ' + data.segundos + ' s)';
                document.getElementById('c').textContent = data.contexto;
            } catch (error) {
                r.className = 'response-box';
                r.textContent = 'Error al conectar.';
            }
        }
        document.getElementById('q').addEventListener('keypress', function (e) {
            if (e.key === 'Enter') enviar();
        });
    </script>
</body>
</html>"""
