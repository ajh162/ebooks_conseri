import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import (
    CORREO_CONTACTO,
    CORREO_REMITENTE,
    RESEND_API_KEY,
    pedir,
    responder_json,
)


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        consulta = parse_qs(urlparse(self.path).query)
        destino = (consulta.get("a") or [CORREO_CONTACTO])[0]

        diagnostico = {
            "hay_llave": bool(RESEND_API_KEY),
            "llave_empieza_con": RESEND_API_KEY[:6] if RESEND_API_KEY else "",
            "largo_de_la_llave": len(RESEND_API_KEY),
            "remitente": CORREO_REMITENTE,
            "destino": destino,
        }

        if not RESEND_API_KEY:
            diagnostico["resultado"] = (
                "El servidor NO esta viendo RESEND_API_KEY. Revisa que la "
                "variable exista en Vercel para Production y que hayas "
                "redesplegado despues de agregarla."
            )
            return responder_json(self, 200, diagnostico)

        codigo, respuesta = pedir(
            "https://api.resend.com/emails",
            metodo="POST",
            cabeceras={"Authorization": "Bearer " + RESEND_API_KEY},
            cuerpo={
                "from": "CONSERI <{}>".format(CORREO_REMITENTE),
                "to": [destino],
                "subject": "Prueba tecnica CONSERI",
                "html": "<p>Si ves esto, el envio desde el servidor funciona.</p>",
            },
        )

        diagnostico["codigo_de_resend"] = codigo
        diagnostico["respuesta_de_resend"] = respuesta
        diagnostico["resultado"] = (
            "Correo aceptado por Resend" if codigo in (200, 201)
            else "Resend rechazo el envio; el motivo esta en respuesta_de_resend"
        )

        return responder_json(self, 200, diagnostico)

    def log_message(self, formato, *args):
        return
