"""
Asistente IA del Taller - puente entre Home Assistant y Ollama (IA 100 % local).

Flujo:
  1. El usuario escribe una pregunta en la pagina (puerto 8000).
  2. El script lee los estados actuales del taller desde la API REST de Home Assistant.
  3. Arma un prompt con las reglas del sistema y esos datos.
  4. Lo envia a Ollama (modelo Hermes3:3b) en la misma VM.
  5. Devuelve la respuesta, el tiempo que tardo y el contexto usado.

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

# Entidades de Home Assistant que se le pasan al modelo como contexto
ENTIDADES = {
    "temperatura": "sensor.taller_temperatura",
    "humedad": "sensor.taller_humedad",
    "puerta": "binary_sensor.taller_puerta",
    "ventilador": "switch.taller_ventilador",
    "luz": "switch.taller_luz",
    "luz natural": "input_number.luz_taller",
}
TRADUCCION = {"on": "encendido/abierta", "off": "apagado/cerrada"}

app = FastAPI(title="Asistente del Taller")

# Archivos estaticos (imagen de fondo). Se publica SOLO la carpeta static/,
# nunca el directorio del proyecto, porque ahi esta el .env con el token.
CARPETA_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(CARPETA_STATIC):
    app.mount("/static", StaticFiles(directory=CARPETA_STATIC), name="static")


class Pregunta(BaseModel):
    texto: str


def leer_estado(entity_id: str) -> str:
    """Consulta una entidad en la API de Home Assistant y devuelve 'estado unidad'."""
    r = requests.get(f"{HA_URL}/api/states/{entity_id}",
                     headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=10)
    r.raise_for_status()
    d = r.json()
    estado = TRADUCCION.get(d["state"], d["state"])
    return f"{estado} {d['attributes'].get('unit_of_measurement', '')}".strip()


def contexto_taller() -> str:
    """Arma la lista de datos actuales del taller. Si una entidad falla, lo indica."""
    lineas = []
    for nombre, ent in ENTIDADES.items():
        try:
            lineas.append(f"- {nombre}: {leer_estado(ent)}")
        except Exception as e:
            lineas.append(f"- {nombre}: no disponible ({type(e).__name__})")
    return "\n".join(lineas)


# Prompt de sistema: rol, idioma, reglas del taller y restriccion de no inventar datos
SISTEMA = (
    "Sos el asistente de un taller de trabajo de 15 a 20 m2 con domotica local. "
    "Responde en espanol, breve y claro, usando SOLO los datos actuales que se te dan. "
    "Reglas del sistema: el ventilador se enciende solo si la temperatura supera 28 C y la puerta esta cerrada; "
    "la luz auxiliar se enciende al abrir la puerta si hay menos de 20 lx. "
    "Si falta un dato, decilo. No inventes valores."
)


@app.post("/preguntar")
def preguntar(p: Pregunta):
    """Recibe la pregunta, agrega el contexto real y consulta a Ollama."""
    ctx = contexto_taller()
    payload = {
        "model": MODELO, "stream": False,
        "messages": [
            {"role": "system", "content": SISTEMA + "\nDatos actuales del taller:\n" + ctx},
            {"role": "user", "content": p.texto},
        ],
    }
    t0 = time.time()
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
    r.raise_for_status()
    return {"respuesta": r.json()["message"]["content"],
            "segundos": round(time.time() - t0, 1), "contexto": ctx}


@app.get("/", response_class=HTMLResponse)
def inicio():
    """Pagina web simple para hacer preguntas al asistente."""
    return r"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Asistente del Taller</title>
    <style>
        body { margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif; background-image: url('/static/fondo.jpg'); background-size: cover; background-position: center; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
        .container { background-color: rgba(255, 255, 255, 0.85); padding: 40px; border-radius: 30px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); width: 80%; max-width: 700px; text-align: center; }
        h1 { color: #6b8e23; font-size: 36px; margin-bottom: 30px; font-weight: bold; }
        .search-box { display: flex; justify-content: center; margin-bottom: 20px; }
        input[type="text"] { width: 70%; padding: 12px 20px; font-size: 16px; border: 2px dashed #6b8e23; border-radius: 25px 0 0 25px; outline: none; background-color: rgba(255,255,255,0.9); }
        button { padding: 12px 30px; font-size: 16px; background-color: #a8c27a; color: #333; border: 2px dashed #6b8e23; border-left: none; border-radius: 0 25px 25px 0; cursor: pointer; font-weight: bold; transition: 0.3s; }
        button:hover { background-color: #8eae5e; }
        .response-box { background-color: #ffffff; padding: 20px; border-radius: 20px; min-height: 150px; text-align: left; white-space: pre-wrap; font-size: 16px; color: #333; margin-top: 20px; border: 1px solid #eee; }
        .loading { color: #888; font-style: italic; }
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
