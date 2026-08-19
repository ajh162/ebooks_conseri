"""
============================================================================
CONSERI · /api/webhook
----------------------------------------------------------------------------
Mercado Pago avisa a esta dirección cada vez que pasa algo con un pago.
Aquí ocurre la entrega automática.

Orden de las cosas (importa el orden):
  1. Se valida la firma del aviso  -> que venga de Mercado Pago de verdad
  2. Se consulta el pago a la API  -> nunca se cree lo que llega en el aviso
  3. Se registra la venta          -> antes de enviar nada
  4. Se generan los enlaces        -> Signed URLs de Supabase
  5. Se manda el correo            -> solo si la venta era nueva

Siempre se responde 200 a Mercado Pago, incluso si algo falló de nuestro
lado. Si respondiéramos error, Mercado Pago reintentaría en bucle.

REGISTRO EN MERCADO PAGO:
  Panel -> Tus integraciones -> Webhooks -> Modo productivo
  URL:   https://TU-DOMINIO/api/webhook
  Evento: Pagos
============================================================================
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import (          # noqa: E402
    SITIO_URL,
    buscar_producto,
    consultar_pago,
    enlace_temporal,
    enviar_correo,
    firma_valida,
    registrar_venta,
    responder_json,
)


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        # ---- Leer el aviso ----
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            aviso = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return responder_json(self, 200, {"ok": True, "nota": "aviso ilegible"})

        # Mercado Pago manda el id en distintos lugares según el tipo de aviso
        id_pago = (
            (aviso.get("data") or {}).get("id")
            or aviso.get("id")
            or self._de_la_url("data.id")
        )
        tipo = aviso.get("type") or aviso.get("topic") or self._de_la_url("type")

        if not id_pago:
            return responder_json(self, 200, {"ok": True, "nota": "aviso sin id"})

        # Mercado Pago avisa el mismo pago dos veces: con el formato nuevo
        # (?data.id=...&type=payment) y con el viejo de IPN (?id=...&topic=payment).
        # El viejo no trae la firma que sabemos validar, asi que lo ignoramos:
        # el nuevo ya trae la misma informacion, firmada.
        if self._de_la_url("topic") and not self._de_la_url("data.id"):
            return responder_json(self, 200, {"ok": True, "nota": "aviso IPN ignorado"})

        # Solo nos interesan los avisos de pago
        if tipo and "payment" not in str(tipo):
            return responder_json(self, 200, {"ok": True, "nota": "aviso ignorado"})

        # ---- 1. Validar la firma ----
        if not firma_valida(self.headers, id_pago):
            print("Firma inválida para el pago", id_pago)
            return responder_json(self, 401, {"error": "firma inválida"})

        # ---- 2. Consultar el pago real ----
        pago = consultar_pago(id_pago)
        if not pago:
            print("No se pudo consultar el pago", id_pago)
            return responder_json(self, 200, {"ok": True, "nota": "pago no consultable"})

        if pago.get("status") != "approved":
            return responder_json(self, 200, {
                "ok": True, "nota": "pago en estado " + str(pago.get("status"))
            })

        clave = (
            pago.get("external_reference")
            or (pago.get("metadata") or {}).get("producto")
        )
        producto = buscar_producto(clave)
        if not producto:
            print("Pago aprobado de un producto desconocido:", clave)
            return responder_json(self, 200, {"ok": True, "nota": "producto desconocido"})

        correo = (pago.get("payer") or {}).get("email") or ""
        monto = pago.get("transaction_amount") or producto["precio"]

        # ---- 3. Registrar ANTES de entregar ----
        es_nueva = registrar_venta(id_pago, clave, correo, monto)
        if not es_nueva:
            # Mercado Pago repite avisos: si ya la habíamos registrado, no
            # volvemos a mandar el correo.
            return responder_json(self, 200, {"ok": True, "nota": "venta ya registrada"})

        # ---- 4. Generar los enlaces de descarga ----
        enlaces = []
        for archivo in producto.get("archivos", []):
            url = enlace_temporal(archivo["ruta"])
            if url:
                enlaces.append({"nombre": archivo["nombre"], "url": url})
            else:
                print("No se pudo firmar el archivo:", archivo["ruta"])

        # ---- 5. Enviar el correo ----
        url_gracias = "{}/api/gracias?payment_id={}".format(SITIO_URL, id_pago)

        if correo:
            enviado = enviar_correo(correo, producto, url_gracias, enlaces)
            if not enviado:
                print("Falló el envío del correo al comprador", correo)
        else:
            print("Pago sin correo del comprador:", id_pago)

        return responder_json(self, 200, {"ok": True, "entregado": clave})

    # Mercado Pago a veces manda los datos en la URL en vez del cuerpo
    def _de_la_url(self, nombre):
        from urllib.parse import parse_qs, urlparse
        consulta = parse_qs(urlparse(self.path).query)
        valores = consulta.get(nombre) or consulta.get(nombre.split(".")[-1])
        return valores[0] if valores else None

    def do_GET(self):
        # Útil para comprobar desde el navegador que la ruta existe.
        return responder_json(self, 200, {"ok": True, "servicio": "webhook CONSERI"})

    def log_message(self, formato, *args):
        return
