# Tool Registry de Jarvis

Este paquete es el **Tool Registry**: el sistema que descubre, expone al LLM
(Groq/OpenAI function calling) y ejecuta las herramientas que Jarvis puede usar.

- `base.py` — clase base `Tool`, `ToolExecutionResult` y el enum `RiskLevel`.
- `registry.py` — `ToolRegistry` (descubrimiento, generacion de schema,
  ejecucion), el singleton global `get_registry()` y el decorador
  `@register_tool`. Aplica la **allowlist** (`tools_config.json`) con politica
  fail-closed.
- `__init__.py` — importa cada modulo de tool para dispararar su registro.
- Un archivo por tool, organizado en subpaquetes por categoria:
  - `system/` — tools que actuan sobre el sistema operativo (`abrir_app.py`,
    `ajustar_brillo.py`, `subir_bajar_volumen.py`, `ejecutar_comando.py`).
  - `conversation/` — tools puramente conversacionales
    (`responder_conversacional.py`).

El schema que ve el LLM se genera 100% desde aqui (ver `brain/tools_schema.py`)
y el despacho pasa por `get_registry().execute(...)` (via
`actions/system_control.execute_tool`). Una tool que no este registrada **y**
activa en la allowlist NO se ejecuta, punto.

## Como agregar una tool nueva en 3 pasos

1. **Crea el archivo** `tools/<categoria>/mi_tool.py` (p. ej.
   `tools/system/mi_tool.py` o `tools/conversation/mi_tool.py`) con una clase que
   herede de `Tool`, decorada con `@register_tool`. Declara `name`,
   `description`, `parameters` (JSON schema de function calling), `risk_level` e
   implementa `execute(**kwargs) -> ToolExecutionResult`:

   ```python
   from tools.base import RiskLevel, Tool, ToolExecutionResult
   from tools.registry import register_tool

   @register_tool
   class MiTool(Tool):
       name = "mi_tool"
       description = "Que hace y cuando usarla (esto lo lee el LLM)."
       parameters = {
           "type": "object",
           "properties": {"arg": {"type": "string", "description": "..."}},
           "required": ["arg"],
       }
       risk_level = RiskLevel.SAFE  # o REVERSIBLE / DESTRUCTIVE

       def execute(self, arg: str) -> ToolExecutionResult:
           return ToolExecutionResult(True, "listo")
   ```

2. **Registra el import** en `tools/__init__.py`, en la seccion
   "DESCUBRIMIENTO DE TOOLS" (el orden define el orden del schema):

   ```python
   from tools.system import mi_tool as _mi_tool  # noqa: F401,E402
   ```

3. **Activa la tool** anadiendo su `name` a `active_tools` en
   `tools_config.json` (la allowlist es fail-closed: si no esta listada, no se
   expone ni se ejecuta).

Notas:
- Si `risk_level = RiskLevel.DESTRUCTIVE`, `main.py` pedira confirmacion por voz
  antes de ejecutarla (ver `_run_voice_confirmation`).
- Al arrancar, el registry deja en el log que tools descubrio, cuales quedaron
  activas y cuales ocultas por la allowlist.
