"""
============================================================================
CONSERI · /api/keepalive
----------------------------------------------------------------------------
Supabase pausa los proyectos del plan gratuito después de unos días sin
actividad. Si eso pasa, las descargas dejan de funcionar.

vercel.json programa un cron que llama a esta ruta una vez al día y hace una
consulta mínima a la base. Nada más.

La ruta está protegida con CRON_SECRET para que nadie de fuera la esté
llamando: Vercel manda el encabezado Authorization con ese secreto.

NOTA SOBRE LOS REGISTROS
----------------------------------------------------------------------------
Vercel guarda la línea de acceso (el "200 OK") pero NO el cuerpo de la
respuesta. Antes eso dejaba una duda importante: el cron podía verse en verde
todos los días aunque la consulta a Supabase estuviera fallando en silencio.

Por eso ahora la función escribe en los registros qué contestó Supabase. Así,
en Vercel -> Logs, al expandir la ejecución se lee el resultado real.
============================================================================
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import (          # noqa: E402
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    _cabeceras_supabase,
    pedir,
    responder_json,
)

CRON_SECRET = os.environ.get("CRON_SECRET", "")


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        autorizacion = self.headers.get("Authorization") or ""

        if CRON_SECRET and autorizacion != "Bearer " + CRON_SECRET:
            print("keepalive: llamada sin autorizacion valida")
            return responder_json(self, 401, {"error": "no autorizado"})

        # ---- ¿Están cargadas las credenciales de Supabase? ----
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            print("keepalive: FALTAN las variables de Supabase en el servidor",
                  "| hay URL:", bool(SUPABASE_URL),
                  "| hay llave:", bool(SUPABASE_SERVICE_KEY))
            return responder_json(self, 200, {
                "base_activa": False,
                "motivo": "faltan SUPABASE_URL o SUPABASE_SERVICE_KEY",
            })

        # ---- La consulta que mantiene despierta a la base ----
        codigo, respuesta = pedir(
            SUPABASE_URL + "/rest/v1/entregas?select=id&limit=1",
            cabeceras=_cabeceras_supabase(),
        )

        activa = codigo == 200

        # Esta línea es la que se lee en Vercel -> Logs al expandir la ejecución
        print("keepalive: Supabase respondio", codigo, "->",
              "OK" if activa else respuesta)

        return responder_json(self, 200, {
            "base_activa": activa,
            "codigo": codigo,
        })

    def log_message(self, formato, *args):
        return
