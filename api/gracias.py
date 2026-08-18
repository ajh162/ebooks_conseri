"""
============================================================================
CONSERI · /api/gracias
----------------------------------------------------------------------------
Página a la que llega el comprador justo después de pagar. También es el
enlace que va en el correo.

Recibe:  /api/gracias?payment_id=123456789
Hace:    consulta el pago en Mercado Pago; si está aprobado, genera enlaces
         de descarga frescos y arma la página.

Se generan enlaces NUEVOS en cada visita: así, si al comprador se le vencen
los del correo, solo vuelve a abrir esta página.

La página usa el mismo css/styles.css del sitio, para que no se sienta que
salió a otro lugar.
============================================================================
"""

import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import (          # noqa: E402
    CORREO_CONTACTO,
    HORAS_DE_VIGENCIA,
    SITIO_URL,
    buscar_producto,
    consultar_pago,
    enlace_temporal,
    responder_html,
)


def envoltura(titulo, contenido):
    """Arma la página completa con el estilo del sitio."""
    return """<!DOCTYPE html>
<html lang="es-MX">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{titulo} · CONSERI</title>
<meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="icon" type="image/png" href="{sitio}/assets/favicon.png">
<link rel="stylesheet" href="{sitio}/css/styles.css">
<style>
  body{{ padding-top: 4.5rem; }}
  .entrega{{ max-width: 640px; margin: 0 auto; padding: clamp(2rem,7vw,4rem) 1.4rem 4rem; }}
  .entrega__marca{{ width: 54px; margin-bottom: 1.4rem; }}
  .entrega__titulo{{ font-family: var(--display); font-size: clamp(1.8rem,4vw,2.6rem);
                     color: var(--navy); margin-bottom: 1rem; }}
  .archivos{{ list-style: none; padding: 0; margin: 2rem 0; }}
  .archivos li{{ margin-bottom: .8rem; }}
  .archivos a{{ display: flex; align-items: center; justify-content: space-between;
                gap: 1rem; min-height: 62px; padding: 1rem 1.3rem; background: var(--blanco);
                border: 1px solid var(--azul-humo); border-radius: var(--radio);
                text-decoration: none; color: var(--navy); font-family: var(--display);
                font-weight: 600; transition: border-color .25s ease, transform .25s ease; }}
  .archivos a:hover{{ border-color: var(--naranja); transform: translateX(4px); }}
  .archivos span{{ font-family: var(--display); font-size: .78rem; letter-spacing: .08em;
                   text-transform: uppercase; color: var(--naranja); }}
  .aviso-vigencia{{ font-size: .9rem; color: var(--gris); }}
</style>
</head>
<body>
<header class="barra barra--fija">
  <a class="barra__marca" href="{sitio}" aria-label="CONSERI, ir al inicio">
    <img src="{sitio}/assets/logo-horizontal-blanco.png" alt="CONSERI" class="barra__logo">
  </a>
  <a href="{sitio}" class="boton boton--chico">Volver al sitio</a>
</header>

<main class="entrega">
{contenido}
</main>

<footer class="pie">
  <img src="{sitio}/assets/logo-horizontal-blanco.png" alt="CONSERI" class="pie__logo">
  <p class="pie__legal">
    Material educativo e informativo. No constituye asesoría fiscal, contable,
    financiera ni legal, ni sustituye el análisis personalizado de un especialista.
  </p>
  <p class="pie__creditos">© CONSERI</p>
</footer>

</body>
</html>""".format(titulo=titulo, contenido=contenido, sitio=SITIO_URL)


def pagina_de_espera(mensaje):
    contenido = """
  <img src="{sitio}/assets/monograma-navy.png" alt="" class="entrega__marca">
  <p class="etiqueta">Tu compra</p>
  <h1 class="entrega__titulo">Estamos confirmando tu pago</h1>
  <p>{mensaje}</p>
  <p class="aviso-vigencia">
    En cuanto se acredite, te llega un correo con tus enlaces de descarga.
    Si pasan más de 30 minutos, escríbenos a
    <a href="mailto:{contacto}">{contacto}</a> con tu número de pago y lo revisamos.
  </p>
  <p style="margin-top:2rem"><a href="{sitio}" class="boton">Volver al sitio</a></p>
""".format(mensaje=mensaje, contacto=CORREO_CONTACTO, sitio=SITIO_URL)
    return envoltura("Confirmando tu pago", contenido)


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        consulta = parse_qs(urlparse(self.path).query)
        id_pago = (
            (consulta.get("payment_id") or consulta.get("collection_id") or [None])[0]
        )

        # ---- Sin número de pago no hay nada que entregar ----
        if not id_pago:
            return responder_html(self, 200, pagina_de_espera(
                "No encontramos el número de pago en el enlace."
            ))

        # ---- Verificar contra Mercado Pago ----
        pago = consultar_pago(id_pago)
        if not pago:
            return responder_html(self, 200, pagina_de_espera(
                "No pudimos consultar tu pago en este momento."
            ))

        if pago.get("status") != "approved":
            return responder_html(self, 200, pagina_de_espera(
                "Tu pago aparece como <strong>{}</strong>. Algunos medios de pago "
                "(como el efectivo o la transferencia) tardan unas horas."
                .format(pago.get("status_detail") or pago.get("status"))
            ))

        clave = (
            pago.get("external_reference")
            or (pago.get("metadata") or {}).get("producto")
        )
        producto = buscar_producto(clave)
        if not producto:
            return responder_html(self, 200, pagina_de_espera(
                "Tu pago está aprobado, pero no logramos identificar el producto."
            ))

        # ---- Generar enlaces frescos ----
        filas = []
        for archivo in producto.get("archivos", []):
            url = enlace_temporal(archivo["ruta"])
            if url:
                filas.append(
                    '<li><a href="{}" download>{} <span>Descargar</span></a></li>'
                    .format(url, archivo["nombre"])
                )

        acceso = ""
        if producto.get("enlace"):
            etiqueta = ("Agendar mi sesión" if producto.get("tipo") == "asesoria"
                        else "Abrir mi acceso en línea")
            acceso = ('<p style="margin-top:2rem">'
                      '<a href="{}" class="boton">{}</a></p>').format(
                          producto["enlace"], etiqueta)

        if not filas and not acceso:
            return responder_html(self, 200, pagina_de_espera(
                "Tu pago está aprobado, pero los archivos no están disponibles "
                "ahora mismo."
            ))

        contenido = """
  <img src="{sitio}/assets/monograma-navy.png" alt="" class="entrega__marca">
  <p class="etiqueta">Pago confirmado</p>
  <h1 class="entrega__titulo">Listo, aquí está tu material</h1>
  <p>Compraste <strong>{nombre}</strong>. También te lo mandamos por correo.</p>

  <ul class="archivos">{filas}</ul>
  {acceso}

  <p class="aviso-vigencia">
    Estos enlaces funcionan durante {horas} horas. Si se vencen, vuelve a abrir esta
    página desde tu correo y se generan de nuevo. Cualquier problema:
    <a href="mailto:{contacto}">{contacto}</a>.
  </p>

  <p style="margin-top:2.5rem">
    <a href="{sitio}#precios" class="enlace-suave">Ver el resto del material de CONSERI</a>
  </p>
""".format(
            nombre=producto["nombre"],
            filas="".join(filas),
            acceso=acceso,
            horas=HORAS_DE_VIGENCIA,
            contacto=CORREO_CONTACTO,
            sitio=SITIO_URL,
        )

        return responder_html(self, 200, envoltura("Tu material", contenido))

    def log_message(self, formato, *args):
        return
