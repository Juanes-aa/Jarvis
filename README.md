# 🤖 Jarvis — Asistente de Escritorio Autónomo para Windows

Jarvis es un asistente de voz inteligente y autónomo para Windows, potenciado por **Groq (`openai/gpt-oss-120b`)** para el razonamiento y toma de decisiones, **openWakeWord** para activación manos libres (*"Hey Jarvis"*), **faster-whisper** para transcripción de voz ultrarrápida, y **edge-tts** para síntesis de voz natural en español (con reproducción por *streaming* de baja latencia).

---

## 🚀 Características Principales

- 🎙️ **Activación Manos Libres (Wake Word)**: Escucha continua en bajo consumo con `openWakeWord` ("Hey Jarvis").
- ⌨️ **Atajo Global de Teclado**: Presiona `Ctrl+Alt+J` desde cualquier ventana para **pausar/reanudar** Jarvis.
- 🖱️ **Ícono en la Bandeja del Sistema**: Ícono junto al reloj de Windows con menú (Pausar/Reanudar, Ver logs, Salir) y color según estado.
- 🌊 **Indicador Flotante tipo Siri** (opcional): Ventana flotante animada que aparece al escuchar/procesar/hablar. Desactivable por config.
- 📝 **Speech to Text (STT)**: Transcripción automática con `faster-whisper` y detección de silencio (VAD).
- 🧠 **Cerebro con IA (Groq / `openai/gpt-oss-120b`)**: Comprensión semántica, soporte multi-turno y llamadas a herramientas (*function calling*).
- 🖥️ **Control Real del Sistema**:
  - Abrir más de 40 aplicaciones comunes (`Chrome`, `Spotify`, `VS Code`, `Calculadora`, `Word`, `Configuración`, etc.).
  - Ajuste del volumen general del sistema con `pycaw`.
  - Modificación del brillo de pantalla con `screen-brightness-control`.
  - Creación segura de carpetas (allowlist; NO ejecuta PowerShell arbitrario).
  - **Gestión de archivos con sandbox** (`crear_archivo`, `crear_carpeta`, `mover_archivo`, `renombrar_archivo`), confinada a una carpeta de trabajo dentro del perfil del usuario.
  - **Control de ventanas** con `pywin32` (minimizar, cambiar de ventana, cerrar la ventana activa con confirmación).
  - **Portapapeles** (`leer_portapapeles`, `escribir_portapapeles`) con `pyperclip`.
  - **Captura de pantalla y OCR** (`tomar_captura`, `leer_pantalla`) con `Pillow` + `pytesseract`.
  - **Control multimedia** (`pausar_reproducir`, `siguiente_cancion`, `cancion_anterior`) vía teclas multimedia del SO con `pyautogui`.
  - **Notificaciones nativas de Windows** (`mostrar_notificacion`) con `winotify`.
- 🧰 **Tool Registry + Allowlist**: Las herramientas se registran de forma explícita en el paquete `tools/` y se activan/ocultan mediante una allowlist configurable (`tools_config.json`), con política *fail-closed*. Ver la sección [Arquitectura de Herramientas](#-arquitectura-de-herramientas).
- 🛡️ **Confirmación por voz para acciones destructivas**: Antes de ejecutar una acción etiquetada como `DESTRUCTIVE` (p. ej. crear una carpeta), Jarvis pide confirmación hablada ("¿Confirmas...? Di sí o no") y solo procede ante un sí claro.
- 🔊 **Text to Speech (TTS)**: Respuestas habladas fluidas y confirmaciones naturales en español mediante `edge-tts` (`es-MX-JorgeNeural`), con reproducción por *streaming* en tiempo real usando `sounddevice` y decodificación con **PyAV** (sin archivos temporales).
- 💾 **Memoria Persistente**: Historial de conversación guardado en JSON local (`memory/history.json`) para recordar el contexto entre sesiones.
- 🔄 **Autoarranque en Windows**: Script para iniciar automáticamente Jarvis en segundo plano al encender el equipo.

---

## 📁 Estructura del Proyecto

```text
Jarvis Juanes/
├── brain/
│   ├── __init__.py
│   ├── tools_schema.py        # Esquemas JSON de herramientas (OpenAI/Groq format)
│   └── groq_client.py         # Interfaz con Groq LLM y gestión de herramientas
├── actions/
│   ├── __init__.py
│   └── system_control.py      # Ejecutores reales para Windows (Apps, Volumen, Brillo, Carpetas)
├── tools/                     # Tool Registry: descubrimiento y despacho de herramientas
│   ├── __init__.py            # Descubre y registra las tools (import explícito) + allowlist + hooks de arranque
│   ├── base.py                # Tool, RiskLevel (SAFE/REVERSIBLE/DESTRUCTIVE), requires_confirmation, ToolExecutionResult
│   ├── registry.py            # ToolRegistry: allowlist (fail-closed), schema y ejecución
│   ├── conversation/
│   │   └── responder_conversacional.py  # Tool: respuesta conversacional (SAFE)
│   └── system/                # Tools que actúan sobre el sistema operativo
│       ├── abrir_app.py           # Abrir aplicaciones
│       ├── subir_bajar_volumen.py # Ajustar volumen
│       ├── ajustar_brillo.py      # Ajustar brillo
│       ├── ejecutar_comando.py    # Crear carpetas (DESTRUCTIVE, requiere confirmación por voz)
│       ├── file_management.py     # Crear/mover/renombrar archivos y carpetas (sandbox, REVERSIBLE)
│       ├── window_control.py      # Minimizar/cambiar/cerrar ventana activa (pywin32)
│       ├── clipboard.py           # Leer/escribir portapapeles (pyperclip)
│       ├── screenshot.py          # Captura de pantalla + OCR (Pillow + pytesseract)
│       ├── media_control.py       # Play/pause, siguiente/anterior canción (pyautogui)
│       └── notifications.py       # Notificaciones nativas de Windows (winotify)
├── stt/
│   ├── __init__.py
│   └── transcriber.py         # Grabación de audio (sounddevice) y transcripción (faster-whisper)
├── tts/
│   ├── __init__.py
│   └── speaker.py             # Síntesis de voz (edge-tts) y reproducción por streaming (sounddevice + PyAV)
├── activation/
│   ├── __init__.py
│   ├── hotkey.py              # Escuchador global de teclado (Ctrl+Alt+J)
│   └── wake_word.py           # Detección de 'Hey Jarvis' en segundo plano (openWakeWord)
├── memory/
│   ├── __init__.py
│   └── conversation_store.py  # Persistencia de memoria conversacional
├── ui/
│   ├── __init__.py
│   ├── status_controller.py   # Máquina de estados thread-safe (idle/escuchando/procesando/hablando + pausa)
│   ├── tray_icon.py           # Ícono de bandeja (pystray + Pillow), menú dinámico
│   └── floating_indicator.py  # Indicador flotante tipo Siri (PySide6), exploratorio
├── logs/
│   └── jarvis.log             # Log rotativo (se abre desde 'Ver logs' del tray)
├── main.py                    # Orquestador central y punto de entrada
├── tools_config.json          # Allowlist de herramientas activas (configurable)
├── setup_autostart.py         # Utilidad para configurar autoarranque en Windows
├── requirements.txt           # Dependencias del proyecto
├── .env.example               # Plantilla de variables de entorno
└── README.md                  # Documentación completa
```

---

## 🧰 Arquitectura de Herramientas

Jarvis no despacha acciones "a mano": todo lo que el LLM puede invocar pasa por el **Tool Registry** (paquete `tools/`).

- **Tool Registry**: cada herramienta es una clase `Tool` (en `tools/`) que declara su contrato para el LLM (`name`, `description`, `parameters`) y su `risk_level`. Al importar `tools`, el decorador `@register_tool` la registra automáticamente; el registry genera el schema de *function calling* que ve Groq y despacha la ejecución.
- **Allowlist configurable**: `tools_config.json` decide qué tools están **activas**. Una tool puede existir en el código pero quedar oculta para el LLM e inejecutable si no está en la lista. La política es ***fail-closed***: si el archivo falta o es inválido, solo se activan las tools `SAFE` (Jarvis avisa por voz al arrancar).
- **Niveles de riesgo**: `SAFE` (sin efectos secundarios, p. ej. responder texto), `REVERSIBLE` (cambia estado, fácil de deshacer, p. ej. volumen/brillo), `DESTRUCTIVE` (cambios difíciles de revertir).
- **`requires_confirmation` (independiente de `RiskLevel`)**: además del nivel de riesgo, una tool puede declarar el flag `requires_confirmation = True` para **forzar la confirmación por voz aunque no sea `DESTRUCTIVE`**. Los dos ejes son distintos: `RiskLevel` describe la reversibilidad/peligrosidad intrínseca de la acción; `requires_confirmation` es una decisión de diseño explícita de "pedir confirmación antes de ejecutar". Ejemplo actual: `cerrar_ventana_activa` es `REVERSIBLE` (no destruye nada a nivel de sistema) pero pide confirmación porque podría perderse trabajo sin guardar.
- **Confirmación por voz**: cuando el LLM invoca una tool con `RiskLevel.DESTRUCTIVE` **o** con `requires_confirmation = True`, Jarvis **no la ejecuta de inmediato**. Anuncia la acción concreta (p. ej. la ruta completa de la carpeta a crear), escucha una respuesta sin wake word y solo procede ante un "sí" claro. Cualquier otra respuesta, o el silencio, **cancela** la acción por seguridad.
- **Sandbox de gestión de archivos**: las tools de archivos operan confinadas a un directorio raíz configurable (`file_management_root` en `tools_config.json`; por defecto `~/Jarvis`). Toda ruta se resuelve y valida dentro de ese root (se rechazan `..`, rutas absolutas fuera del root y symlinks que escapen). El root **debe** estar dentro del perfil del usuario: si se configura fuera, Jarvis **no arranca** (falla en la inicialización con un mensaje claro) para no operar con un sandbox roto.

---

## 🛠️ Instalación y Configuración

### 1. Clonar o descargar el repositorio y situarse en la carpeta:
```powershell
cd "c:\Users\j8716\OneDrive\Documentos\Proyectos\Jarvis Juanes"
```

### 2. Instalar las dependencias requeridas:
```powershell
pip install -r requirements.txt
```

> **OCR opcional (Tesseract):** la tool `leer_pantalla` (leer texto de la pantalla
> por OCR) necesita el motor **Tesseract** instalado en el sistema, además del
> paquete de Python `pytesseract`. Sin el binario, `leer_pantalla` arranca
> **inactiva** automáticamente (el resto de tools funciona normal). Para
> habilitarla en Windows, instala Tesseract desde
> [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
> (o `choco install tesseract` con Chocolatey), asegúrate de que `tesseract` esté
> en el `PATH` y reinicia Jarvis: al arrancar detecta el binario y activa la tool.
> Instalar el paquete de idioma español (`spa`) mejora el reconocimiento.

### 3. Configurar tu API Key gratuita de Groq:
1. Obtén tu API Key gratuita en [Groq Console](https://console.groq.com/keys).
2. Copia el archivo `.env.example` a `.env`:
   ```powershell
   copy .env.example .env
   ```
3. Edita `.env` y pega tu clave:
   ```env
   GROQ_API_KEY=gsk_tu_clave_aqui
   ```

---

## 🎮 Modos de Uso

Jarvis corre **sin ventana de consola** y toda la interacción es por voz, hotkey o el ícono de bandeja.

### Modo normal (sin consola)
```powershell
pythonw main.py
```
No se abre ninguna ventana. Verás el ícono de Jarvis junto al reloj de Windows.
Los logs se escriben en `logs/jarvis.log` (usa **Ver logs** en el menú del tray para abrirlo).

> **Nota (Windows 11):** la preferencia de "mostrar el ícono en la barra" se guarda
> **por ejecutable**, y `python.exe` y `pythonw.exe` son binarios distintos. Jarvis
> se encarga solo: al arrancar, promueve automáticamente su propio ícono (entrada
> `IsPromoted` en `HKCU\Control Panel\NotifyIconSettings`) para que sea visible sin
> tener que activarlo a mano. La primera vez puede requerir un reinicio del
> Explorador o volver a lanzar la app para que Windows lo refleje.

### Modo debug (con consola y logs en vivo)
```powershell
python main.py
```
Idéntico funcionamiento, pero además imprime los logs en vivo en la terminal
(también se siguen escribiendo a `logs/jarvis.log`).

### 1. Activación Manos Libres (Wake Word)
Di en voz alta: **"Hey Jarvis"**
- Jarvis responderá *"¿Dime?"*.
- Di tu instrucción (ej. *"Abre Spotify y sube el volumen un 20%"*).

### 2. Pausar / Reanudar (`Ctrl+Alt+J`)
El hotkey es un **interruptor de pausa/reanudar** (mismo estado que el botón del tray).
Mientras está pausado, *"Hey Jarvis"* no hace nada y el ícono se ve apagado.
Vuelve a pulsar el hotkey (o usa **Reanudar** en el tray) para reactivarlo.

### 3. Ícono de Bandeja del Sistema
Clic derecho en el ícono de Jarvis:
- **Pausar / Reanudar** — refleja el estado actual dinámicamente.
- **Ver logs** — abre `logs/jarvis.log` en tu editor por defecto.
- **Salir** — cierra Jarvis de forma limpia.

El ícono cambia de color según el estado: gris (idle), azul (escuchando),
verde (procesando), morado (hablando), y oscuro con diagonal (pausado).

### 4. Indicador Flotante (exploratorio)
Ventana animada tipo Siri que aparece al escuchar/procesar/hablar.
Se desactiva por completo con la flag en `config.py`:
```python
FLOATING_INDICATOR_ENABLED = False   # o variable de entorno FLOATING_INDICATOR_ENABLED=0
```
Al desactivarla, Jarvis funciona solo con el cambio de estado del ícono de bandeja.

### Probar cada pieza de UI por separado
```powershell
python -m ui.tray_icon           # Solo el ícono de bandeja (cicla estados)
python -m ui.floating_indicator  # Solo el indicador flotante (cicla estados)
```

---

## ⚙️ Configurar Inicio Automático con Windows

Para que Jarvis se inicie automáticamente en segundo plano cada vez que enciendas tu computadora o inicies sesión:

```powershell
# Activar autoarranque
python setup_autostart.py --enable

# Comprobar estado
python setup_autostart.py --status

# Desactivar autoarranque
python setup_autostart.py --disable
```

---

## 💡 Ejemplos de Comandos que puedes probar

| Acción | Comando de ejemplo |
|---|---|
| **Abrir Programas** | *"Abre Google Chrome"*, *"Inicia el bloc de notas"*, *"Abre Spotify"* |
| **Volumen** | *"Sube el volumen"*, *"Baja el volumen un 30%"* |
| **Brillo** | *"Ajusta el brillo al 80%"*, *"Pon el brillo al máximo"* |
| **Carpetas** | *"Crea una carpeta llamada Proyectos en el escritorio"* |
| **Conversación y Ayuda** | *"¿Cuál es la distancia de la Tierra a la Luna?"*, *"Cuéntame un chiste"* |
| **Contexto Continuo** | *"¿Quién fue Alan Turing?"* $\rightarrow$ luego: *"¿En qué año nació?"* |
