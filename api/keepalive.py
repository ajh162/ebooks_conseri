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
============================================================================
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import despertar_base, responder_json      # noqa: E402

CRON_SECRET = os.environ.get("CRON_SECRET", "")


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        autorizacion = self.headers.get("Authorization") or ""

        if CRON_SECRET and autorizacion != "Bearer " + CRON_SECRET:
            return responder_json(self, 401, {"error": "no autorizado"})

        activa = despertar_base()
        return responder_json(self, 200, {"base_activa": activa})

    def log_message(self, formato, *args):
        return
