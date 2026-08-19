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


def envoltura(titulo, contenido, tono="exito"):
    """Arma la pagina completa con el estilo del sitio.

    tono = "exito"  -> cabecera con el material listo
    tono = "espera" -> cabecera mas sobria, para pagos sin acreditar

    La cabecera reusa el mismo lenguaje visual de la portada: fondo navy, los
    halos de color, el lienzo animado y el monograma. Asi la persona no siente
    que salio del sitio despues de pagar.
    """
    return """<!DOCTYPE html>
<html lang="es-MX">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{titulo} · CONSERI</title>
<meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="icon" type="image/png" href="{sitio}/assets/favicon.png">
<link rel="stylesheet" href="{sitio}/css/styles.css">
<style>
  /* ---- Cabecera ---- */
  .remate{{
    position: relative;
    background: var(--navy);
    color: var(--blanco);
    padding: clamp(6rem, 14vw, 8rem) clamp(1.2rem, 5vw, 2rem) clamp(4rem, 9vw, 6rem);
    overflow: hidden;
    text-align: center;
  }}
  .remate::before{{
    content: "";
    position: absolute;
    width: 70vw; height: 70vw;
    top: -34vw; right: -18vw;
    background: radial-gradient(circle, rgba(49,115,167,.5), transparent 62%);
    animation: flotar 16s ease-in-out infinite alternate;
  }}
  .remate::after{{
    content: "";
    position: absolute;
    width: 52vw; height: 52vw;
    bottom: -30vw; left: -16vw;
    background: radial-gradient(circle, rgba(244,136,42,.3), transparent 62%);
    animation: flotar 20s ease-in-out infinite alternate-reverse;
  }}
  .remate__lienzo{{ position: absolute; inset: 0; width: 100%; height: 100%; }}
  .remate__dentro{{ position: relative; z-index: 2; max-width: 640px; margin: 0 auto; }}

  /* El monograma dentro de un anillo que late */
  .remate__marca{{
    display: grid;
    place-items: center;
    width: 96px; height: 96px;
    margin: 0 auto 1.6rem;
    border-radius: 50%;
    background: rgba(255,255,255,.06);
    border: 1px solid rgba(255,255,255,.16);
    animation: respirar 5s ease-in-out infinite;
  }}
  .remate__marca img{{ width: 46px; }}
  .remate__titulo{{
    font-family: var(--display);
    font-size: clamp(2rem, 5vw, 3.1rem);
    font-weight: 700;
    line-height: 1.12;
    letter-spacing: -.02em;
    margin: 0 0 1rem;
  }}
  .remate__bajada{{ color: rgba(255,255,255,.75); font-size: 1.05rem; margin: 0; }}
  .remate__bajada strong{{ color: var(--blanco); }}

  /* Palomita que se dibuja sola al cargar */
  .palomita{{ width: 26px; height: 26px; }}
  .palomita path{{
    fill: none;
    stroke: var(--naranja);
    stroke-width: 3.2;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 30;
    stroke-dashoffset: 30;
    animation: trazar .7s var(--curva) .3s forwards;
  }}
  @keyframes trazar{{ to{{ stroke-dashoffset: 0; }} }}

  /* ---- Cuerpo ---- */
  .entrega{{
    max-width: 660px;
    margin: -3.5rem auto 0;
    position: relative;
    z-index: 3;
    padding: 0 1.4rem clamp(3rem, 8vw, 5rem);
  }}
  .tarjeta{{
    background: var(--blanco);
    border: 1px solid var(--azul-humo);
    border-radius: var(--radio);
    box-shadow: var(--sombra);
    padding: clamp(1.6rem, 4vw, 2.4rem);
  }}
  .tarjeta__titulo{{
    font-family: var(--display);
    font-size: 1.15rem;
    color: var(--navy);
    margin: 0 0 1.2rem;
  }}

  .archivos{{ list-style: none; padding: 0; margin: 0; }}
  .archivos li{{ margin-bottom: .7rem; }}
  .archivos li:last-child{{ margin-bottom: 0; }}
  .archivos a{{
    display: flex; align-items: center; gap: 1rem;
    min-height: 66px; padding: 1rem 1.2rem;
    background: var(--hueso);
    border: 1px solid var(--azul-humo);
    border-radius: 12px;
    text-decoration: none;
    color: var(--navy);
    font-family: var(--display);
    font-weight: 600;
    transition: border-color .3s ease, background .3s ease, transform .3s var(--curva);
  }}
  .archivos a:hover{{
    border-color: var(--naranja);
    background: var(--blanco);
    transform: translateX(5px);
  }}
  .archivos__icono{{
    flex: none;
    display: grid; place-items: center;
    width: 40px; height: 40px;
    border-radius: 10px;
    background: rgba(244,136,42,.14);
    color: var(--naranja);
  }}
  .archivos__icono svg{{ width: 20px; height: 20px; }}
  .archivos__nombre{{ flex: 1; }}
  .archivos__accion{{
    font-size: .74rem; letter-spacing: .1em;
    text-transform: uppercase; color: var(--naranja);
  }}

  .aviso-vigencia{{
    font-size: .9rem;
    color: var(--gris);
    margin: 1.6rem 0 0;
    padding-top: 1.4rem;
    border-top: 1px solid var(--azul-humo);
  }}
  .siguiente{{ text-align: center; margin: 2.5rem 0 0; }}
  .siguiente .enlace-suave{{ color: var(--azul); }}
</style>
</head>
<body>

<header class="barra barra--fija">
  <a class="barra__marca" href="{sitio}" aria-label="CONSERI, ir al inicio">
    <img src="{sitio}/assets/logo-horizontal-blanco.png" alt="CONSERI" class="barra__logo">
  </a>
  <a href="{sitio}" class="boton boton--chico">Volver al sitio</a>
</header>

<main>
{contenido}
</main>

<footer class="pie">
  <div class="pie__arriba">
    <img src="{sitio}/assets/logo-horizontal-blanco.png" alt="CONSERI" class="pie__logo">
    <nav class="pie__nav" aria-label="Enlaces">
      <a href="{sitio}">Inicio</a>
      <a href="{sitio}/legal.html">Aviso de privacidad</a>
      <a href="mailto:{contacto}">{contacto}</a>
    </nav>
  </div>
  <p class="pie__legal">
    Material educativo e informativo. No constituye asesoría fiscal, contable,
    financiera ni legal, ni sustituye el análisis personalizado de un especialista.
  </p>
  <p class="pie__creditos">© CONSERI</p>
</footer>

<script src="{sitio}/js/fondo.js" defer></script>
<script src="{sitio}/js/script.js" defer></script>
</body>
</html>""".format(
        titulo=titulo,
        contenido=contenido,
        sitio=SITIO_URL,
        contacto=CORREO_CONTACTO,
    )


def pagina_de_espera(mensaje):
    """Pago que todavia no se acredita (efectivo, transferencia, revision)."""
    contenido = """
<section class="remate">
  <canvas class="remate__lienzo" id="fondo" aria-hidden="true"></canvas>
  <div class="remate__dentro">
    <div class="remate__marca">
      <img src="{sitio}/assets/monograma-blanco.png" alt="">
    </div>
    <p class="etiqueta etiqueta--clara"><span class="etiqueta__punto"></span> Tu compra</p>
    <h1 class="remate__titulo">Estamos confirmando tu pago</h1>
    <p class="remate__bajada">{mensaje}</p>
  </div>
</section>

<div class="entrega">
  <div class="tarjeta animar" data-animar="subir">
    <h2 class="tarjeta__titulo">Qué sigue</h2>
    <p style="color:var(--gris)">
      En cuanto se acredite el pago te llega un correo con tus enlaces de descarga.
      Algunos medios, como el efectivo o la transferencia, pueden tardar unas horas.
    </p>
    <p class="aviso-vigencia">
      Si pasan más de 30 minutos y no ves nada, escríbenos a
      <a href="mailto:{contacto}">{contacto}</a> con tu número de pago y lo revisamos.
    </p>
  </div>

  <p class="siguiente"><a href="{sitio}" class="boton">Volver al sitio</a></p>
</div>
""".format(mensaje=mensaje, contacto=CORREO_CONTACTO, sitio=SITIO_URL)
    return envoltura("Confirmando tu pago", contenido, tono="espera")


def fila_archivo(nombre, url):
    """Un renglon de la lista de descargas, con su icono."""
    return (
        '<li><a href="{}" download>'
        '<span class="archivos__icono">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/>'
        '<path d="M4 20h16"/></svg></span>'
        '<span class="archivos__nombre">{}</span>'
        '<span class="archivos__accion">Descargar</span>'
        '</a></li>'
    ).format(url, nombre)


def pagina_de_entrega(producto, filas, acceso=""):
    """Pantalla de 'aqui esta tu material'.

    Vive aqui, en una sola funcion, para que la herramienta de
    previsualizacion (previsualizar-gracias.py) muestre EXACTAMENTE lo mismo
    que ve el comprador. Si estuviera duplicada, tarde o temprano una copia se
    quedaria vieja y estariamos revisando un diseno que ya no existe.
    """
    # Hay productos sin archivos (una asesoria, por ejemplo): en esos casos no
    # se pone el encabezado "Tus archivos" ni una lista vacia.
    if filas:
        bloque_archivos = (
            '<h2 class="tarjeta__titulo">Tus archivos</h2>'
            '<ul class="archivos">{}</ul>'.format(filas)
        )
    else:
        bloque_archivos = '<h2 class="tarjeta__titulo">Tu acceso</h2>'

    contenido = """
<section class="remate">
  <canvas class="remate__lienzo" id="fondo" aria-hidden="true"></canvas>
  <div class="remate__dentro">
    <div class="remate__marca">
      <img src="{sitio}/assets/monograma-blanco.png" alt="">
    </div>
    <p class="etiqueta etiqueta--clara">
      <svg class="palomita" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 12.5l5 5L20 6.5"/>
      </svg>
      Pago confirmado
    </p>
    <h1 class="remate__titulo">Listo, aquí está tu material</h1>
    <p class="remate__bajada">
      Compraste <strong>{nombre}</strong>. También te lo mandamos por correo.
    </p>
  </div>
</section>

<div class="entrega">
  <div class="tarjeta animar" data-animar="subir">
    {bloque_archivos}
    {acceso}

    <p class="aviso-vigencia">
      {vigencia}
      Cualquier problema: <a href="mailto:{contacto}">{contacto}</a>.
    </p>
  </div>

  <p class="siguiente">
    <a href="{sitio}#precios" class="enlace-suave">Ver el resto del material de CONSERI</a>
  </p>
</div>
""".format(
        nombre=producto["nombre"],
        bloque_archivos=bloque_archivos,
        vigencia=(
            "Estos enlaces funcionan durante {} horas. Si se vencen, vuelve a abrir "
            "esta página desde tu correo y se generan de nuevo.".format(HORAS_DE_VIGENCIA)
            if filas else ""
        ),
        acceso=acceso,
        contacto=CORREO_CONTACTO,
        sitio=SITIO_URL,
    )

    return envoltura("Tu material", contenido)


def bloque_de_acceso(producto):
    """Boton de acceso en linea (minicurso, sesion agendada, etc.)."""
    if not producto.get("enlace"):
        return ""
    etiqueta = ("Agendar mi sesión" if producto.get("tipo") == "asesoria"
                else "Abrir mi acceso en línea")
    return ('<p style="margin:1.6rem 0 0;text-align:center">'
            '<a href="{}" class="boton" target="_blank" '
            'rel="noopener">{}</a></p>').format(producto["enlace"], etiqueta)


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
                filas.append(fila_archivo(archivo["nombre"], url))

        acceso = bloque_de_acceso(producto)

        if not filas and not acceso:
            return responder_html(self, 200, pagina_de_espera(
                "Tu pago está aprobado, pero los archivos no están disponibles "
                "ahora mismo."
            ))

        return responder_html(
            self, 200, pagina_de_entrega(producto, "".join(filas), acceso)
        )

    def log_message(self, formato, *args):
        return
