"""
============================================================================
CONSERI · /api/checkout
----------------------------------------------------------------------------
Recibe del navegador una clave de producto y devuelve el enlace al checkout
de Mercado Pago.

Entra:   POST {"producto": "kit_completo"}
Sale:    {"url_pago": "https://www.mercadopago.com.mx/checkout/..."}

Regla de seguridad importante: el precio NUNCA viaja desde el navegador.
El navegador manda la clave, y el servidor busca el precio en catalogo.json.
Si no fuera así, cualquiera podría cambiar el precio desde la consola.
============================================================================
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import (          # noqa: E402
    MP_ACCESS_TOKEN,
    buscar_producto,
    crear_preferencia,
    responder_json,
)


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        # ---- 1. ¿Están cargadas las credenciales? ----
        if not MP_ACCESS_TOKEN:
            return responder_json(self, 500, {
                "error": "Falta la variable MP_ACCESS_TOKEN en el servidor."
            })

        # ---- 2. Leer lo que mandó el navegador ----
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return responder_json(self, 400, {"error": "Petición mal formada."})

        clave = (datos.get("producto") or "").strip()

        # ---- 3. Buscar el producto en el catálogo ----
        producto = buscar_producto(clave)
        if not producto:
            return responder_json(self, 404, {
                "error": "Ese producto no existe en el catálogo."
            })

        # ---- 4. Pedirle a Mercado Pago el enlace de pago ----
        url_pago, respuesta = crear_preferencia(clave, producto)

        if not url_pago:
            print("Mercado Pago rechazó la preferencia:", respuesta)
            return responder_json(self, 502, {
                "error": "Mercado Pago no pudo crear el pago en este momento."
            })

        return responder_json(self, 200, {
            "url_pago": url_pago,
            "producto": clave,
        })

    def do_GET(self):
        # Esta ruta solo acepta POST; el GET responde algo útil para diagnóstico.
        return responder_json(self, 405, {
            "error": "Usa POST con {\"producto\": \"clave\"}."
        })

    def log_message(self, formato, *args):
        """Silencia el log por línea de Vercel para no llenar la consola."""
        return
