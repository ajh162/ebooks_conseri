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

Esa comodidad tiene un costo: el único candado es el número de pago de la
dirección, y Mercado Pago lo seguiría dando por aprobado dentro de un año. Por
eso la página tiene dos límites propios (ver LIMITES DE ACCESO más abajo): una
caducidad en días y un máximo de aperturas.

La página usa el mismo css/styles.css del sitio, para que no se sienta que
salió a otro lugar.
============================================================================
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _comun import (          # noqa: E402
    CORREO_CONTACTO,
    DIAS_DE_ACCESO,
    LIMITE_DE_APERTURAS,
    MINUTOS_DE_SESION,
    SITIO_URL,
    plazo_de_la_liga,
    plazo_de_los_enlaces,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    _cabeceras_supabase,
    buscar_producto,
    consultar_pago,
    enlace_temporal,
    pedir,
    responder_html,
)


# ---------------------------------------------------------------------------
# LIMITES DE ACCESO
# ---------------------------------------------------------------------------
# DIAS_DE_ACCESO, LIMITE_DE_APERTURAS y MINUTOS_DE_SESION viven en _comun.py,
# junto a las demas variables de entorno, porque el correo tambien los menciona.

def fecha_de_vencimiento(fecha_de_pago):
    """Convierte la fecha de aprobacion de Mercado Pago (date_approved) en la
    fecha en que la liga deja de servir.

    Devuelve None si no se pudo leer la fecha. En ese caso NO se aplica la
    caducidad: es mejor entregar de mas que dejar fuera a un comprador real
    porque Mercado Pago cambio el formato de la fecha.
    """
    if not fecha_de_pago:
        return None
    # Mercado Pago manda algo como 2026-09-25T12:34:56.000-04:00, pero no
    # siempre incluye los milisegundos.
    for formato in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            aprobado = datetime.strptime(fecha_de_pago, formato)
        except (ValueError, TypeError):
            continue
        return aprobado + timedelta(days=DIAS_DE_ACCESO)
    return None


def registrar_apertura(id_pago):
    """Abre o continua la sesion de descarga y dice en cual va.

    El trabajo lo hace la funcion registrar_apertura() de Postgres, en un solo
    UPDATE, para que sea atomico y para que la ventana se mida con el reloj de
    la base y no con el del servidor.

    Devuelve (numero_de_sesion, fin_de_la_sesion) o (None, None) cuando no se
    pudo contar: Supabase sin configurar, sin respuesta, o sin registro de esa
    venta (por ejemplo si el webhook fallo y nunca se guardo la fila). En ese
    caso la pagina entrega igual, a proposito: preferimos dejar pasar a un
    comprador real que bloquearlo por una falla nuestra.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None, None

    cabeceras = _cabeceras_supabase()
    cabeceras["Content-Type"] = "application/json"

    codigo, respuesta = pedir(
        SUPABASE_URL + "/rest/v1/rpc/registrar_apertura",
        metodo="POST",
        cabeceras=cabeceras,
        cuerpo={"pid": str(id_pago), "ventana_min": MINUTOS_DE_SESION},
    )

    if codigo != 200 or not isinstance(respuesta, list) or not respuesta:
        return None, None

    fila = respuesta[0]
    numero = fila.get("num_apertura")
    fin = fila.get("fin_sesion")
    if not isinstance(numero, int) or not isinstance(fin, str):
        return None, None

    # La fecha se pinta dentro de un atributo HTML. Viene de nuestra propia base,
    # pero se limpia de todos modos: es una linea y evita una sorpresa.
    for feo in ('"', "'", "<", ">"):
        fin = fin.replace(feo, "")
    return numero, fin


def banda_de_sesion(num_apertura, fin_sesion):
    """Cuenta regresiva arriba de las descargas.

    Es la pieza que hace justa la regla: sin ella, el comprador se queda sin
    acceso sin haber entendido nunca que habia un reloj corriendo. Dice tres
    cosas: que descargue ya, cuanto le queda de esta sesion, y que pasa despues.
    """
    if not num_apertura or not fin_sesion:
        return ""

    restantes = max(LIMITE_DE_APERTURAS - num_apertura, 0)
    if restantes == 0:
        despues = ("Es tu última sesión: cuando el reloj llegue a cero, esta "
                   "liga deja de funcionar.")
        terminado = ("Esta sesión terminó y era la última. Si necesitas el "
                     'material otra vez, escríbenos a '
                     '<a href="mailto:{contacto}">{contacto}</a> con tu número '
                     "de pago.").format(contacto=CORREO_CONTACTO)
    elif restantes == 1:
        despues = "Cuando termine, te queda 1 sesión más."
        terminado = ("Esta sesión terminó. Si vuelves a cargar la página, usas "
                     "tu última sesión.")
    else:
        despues = "Cuando termine, te quedan {} sesiones más.".format(restantes)
        terminado = ("Esta sesión terminó. Si vuelves a cargar la página, usas "
                     "otra de tus {} sesiones.".format(restantes))

    return """
    <div class="sesion" id="sesion" data-fin="{fin}">
      <p class="sesion__texto" id="sesion-viva">
        <strong>Descarga todo ahora.</strong> Esta sesión sigue abierta
        <span class="sesion__reloj" id="sesion-reloj">--:--</span>. Mientras no
        termine puedes recargar, ver el video y bajar los archivos uno por uno
        sin gastar nada. {despues}
      </p>
      <p class="sesion__texto sesion__texto--fin" id="sesion-muerta" hidden>
        {terminado}
      </p>
    </div>
    <script>
      (function(){{
        var caja = document.getElementById('sesion');
        var viva = document.getElementById('sesion-viva');
        var muerta = document.getElementById('sesion-muerta');
        var reloj = document.getElementById('sesion-reloj');
        if (!caja || !viva || !muerta || !reloj) return;

        var fin = new Date(caja.getAttribute('data-fin')).getTime();
        if (isNaN(fin)) {{ caja.hidden = true; return; }}

        function pinta(){{
          var faltan = Math.round((fin - Date.now()) / 1000);
          if (faltan <= 0) {{
            viva.hidden = true;
            muerta.hidden = false;
            caja.className = 'sesion sesion--fin';
            return;
          }}
          var m = Math.floor(faltan / 60), s = faltan % 60;
          reloj.textContent = m + ':' + (s < 10 ? '0' : '') + s;
          setTimeout(pinta, 1000);
        }}
        pinta();
      }})();
    </script>""".format(fin=fin_sesion, despues=despues, terminado=terminado)


def texto_de_vigencia(vence):
    """Linea tranquila del pie de la tarjeta: hasta cuando vive la liga.

    Lo urgente (el reloj de la sesion) va arriba, en banda_de_sesion.
    """
    if not vence:
        return ""
    # Dos cuidados aqui:
    #  - No decir solo la fecha: suena a "tienes hasta el 27", y no es cierto,
    #    porque si se acaban las sesiones antes, la liga muere antes.
    #  - Decirlo como plazo y no como dia del calendario: con 24 horas, "el 27
    #    de septiembre" se lee como "todo el 27", cuando en realidad vence a la
    #    hora exacta en que se compro.
    return ('<p class="aviso-vigencia">Esta liga es personal. Deja de funcionar '
            '{} después de tu compra, o cuando se acaben tus sesiones, lo que '
            'pase primero.</p>').format(plazo_de_la_liga())


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

<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','GTM-MN6X9CH6');</script>
<!-- End Google Tag Manager -->

<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=AW-18470481500"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  gtag('config', 'AW-18470481500');
</script>
<!-- End Google tag -->

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

  /* ---- Reproductor de video ---- */
  .reproductor{{ margin: 0 0 1.6rem; }}
  .reproductor video{{
    width: 100%;
    display: block;
    border-radius: 12px;
    background: var(--navy);
    box-shadow: 0 18px 40px -26px rgba(17,34,52,.6);
  }}
  .reproductor__pie{{
    display: flex; flex-wrap: wrap; align-items: center;
    justify-content: space-between; gap: .6rem;
    margin: .7rem 0 0;
    font-size: .85rem; color: var(--gris);
  }}
  .reproductor__pie a{{
    font-family: var(--display); font-weight: 600;
    color: var(--naranja); text-decoration: none;
  }}
  .reproductor__pie a:hover{{ text-decoration: underline; }}

  .aviso-vigencia{{
    font-size: .9rem;
    color: var(--gris);
    margin: 1.6rem 0 0;
    padding-top: 1.4rem;
    border-top: 1px solid var(--azul-humo);
  }}
  .sesion{{
    background: rgba(244, 136, 42, .10);
    border: 1px solid rgba(244, 136, 42, .35);
    border-radius: var(--radio);
    padding: 1rem 1.2rem;
    margin: 0 0 1.8rem;
  }}
  .sesion--fin{{
    background: rgba(94, 112, 128, .10);
    border-color: var(--azul-humo);
  }}
  .sesion__texto{{
    margin: 0; font-size: .92rem; line-height: 1.55; color: var(--navy);
  }}
  .sesion__texto--fin{{ color: var(--gris); }}
  .sesion__texto a{{ color: var(--azul); }}
  .sesion__reloj{{
    font-family: var(--display); font-weight: 700; color: var(--naranja);
    font-variant-numeric: tabular-nums;
  }}
  .siguiente{{ text-align: center; margin: 2.5rem 0 0; }}
  .siguiente .enlace-suave{{ color: var(--azul); }}
</style>
</head>
<body>

<!-- Google Tag Manager (noscript) -->
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-MN6X9CH6"
height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
<!-- End Google Tag Manager (noscript) -->


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


def pagina_de_acceso_cerrado(titulo, mensaje):
    """Liga vencida o con las aperturas agotadas.

    El tono es de "escribenos y te lo reponemos", no de regaño: quien llega aqui
    puede ser el comprador de verdad, que volvio tarde o ya abrio su liga varias
    veces.
    """
    contenido = """
<section class="remate">
  <canvas class="remate__lienzo" id="fondo" aria-hidden="true"></canvas>
  <div class="remate__dentro">
    <div class="remate__marca">
      <img src="{sitio}/assets/monograma-blanco.png" alt="">
    </div>
    <p class="etiqueta etiqueta--clara"><span class="etiqueta__punto"></span> Tu compra</p>
    <h1 class="remate__titulo">{titulo}</h1>
    <p class="remate__bajada">{mensaje}</p>
  </div>
</section>

<div class="entrega">
  <div class="tarjeta animar" data-animar="subir">
    <h2 class="tarjeta__titulo">Si tú eres quien compró</h2>
    <p style="color:var(--gris)">
      Escríbenos a <a href="mailto:{contacto}">{contacto}</a> con tu número de
      pago y te reponemos el acceso. Tu compra no se pierde.
    </p>
    <p class="aviso-vigencia">
      Cada compra tiene su propia liga, personal y con vigencia. Si llegaste
      aquí con la liga de alguien más, el material está a la venta en el sitio.
    </p>
  </div>

  <p class="siguiente"><a href="{sitio}#precios" class="boton">Ver el material</a></p>
</div>
""".format(titulo=titulo, mensaje=mensaje, contacto=CORREO_CONTACTO, sitio=SITIO_URL)
    return envoltura(titulo, contenido, tono="espera")


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


def bloque_video(nombre, url):
    """Reproductor para los archivos marcados como video en catalogo.json.

    Se reproduce dentro de la pagina en vez de obligar a bajar 90 MB antes de
    poder ver nada. preload="metadata" hace que solo se cargue la duracion al
    abrir; el resto se descarga conforme se ve.
    """
    return """
    <div class="reproductor">
      <video controls preload="metadata" playsinline>
        <source src="{url}" type="video/mp4">
        Tu navegador no puede reproducir el video. Usa el enlace de descarga.
      </video>
      <p class="reproductor__pie">
        <span>{nombre}</span>
        <a href="{url}" download>Descargar el video</a>
      </p>
    </div>""".format(url=url, nombre=nombre)


def pagina_de_entrega(producto, filas, acceso="", videos="", venta=None,
                      aviso_acceso="", banda=""):
    """Pantalla de 'aqui esta tu material'.

    Vive aqui, en una sola funcion, para que la herramienta de
    previsualizacion (previsualizar.py) muestre EXACTAMENTE lo mismo que ve el
    comprador.

    "venta" es opcional: cuando viene, la pagina le avisa a Google Tag Manager
    que hubo una compra (ver el bloque de abajo). Si estuviera duplicada, tarde o temprano una copia se
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

    # El minicurso va arriba: es la pieza que la persona quiere ver primero.
    if videos:
        bloque_archivos = (
            '<h2 class="tarjeta__titulo">Tu minicurso</h2>'
            + videos
            + bloque_archivos
        )

    # ---- Aviso de venta para Google Tag Manager ---------------------------
    # Esta pagina solo se muestra cuando Mercado Pago confirma que el pago esta
    # aprobado, asi que el evento representa una VENTA real y no un clic en el
    # boton de comprar.
    #
    # En GTM se usa con un activador de tipo "Evento personalizado" con el
    # nombre  compra_completada.  transaction_id evita contar dos veces a quien
    # vuelva a abrir su pagina de descarga.
    aviso_venta = ""
    if venta:
        aviso_venta = """
<script>
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push({{
    event: 'compra_completada',
    transaction_id: '{id_pago}',
    value: {monto},
    currency: 'MXN',
    item_id: '{clave}',
    item_name: '{nombre_producto}'
  }});
</script>
""".format(
            id_pago=venta.get("id_pago", ""),
            monto=venta.get("monto") or 0,
            clave=venta.get("clave", ""),
            nombre_producto=str(producto["nombre"]).replace("'", ""),
        )

    contenido = aviso_venta + """
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
    {banda}
    {bloque_archivos}
    {acceso}

    <p class="aviso-vigencia">
      {vigencia}
      Cualquier problema: <a href="mailto:{contacto}">{contacto}</a>.
    </p>
    {aviso_acceso}
  </div>

  <p class="siguiente">
    <a href="{sitio}#precios" class="enlace-suave">Ver el resto del material de CONSERI</a>
  </p>
</div>
""".format(
        nombre=producto["nombre"],
        bloque_archivos=bloque_archivos,
        banda=banda,
        vigencia=(
            # OJO: no prometer que volver a abrir es gratis. Con las sesiones
            # limitadas, recargar esta pagina despues de que venza la sesion
            # descuenta una. Decirlo aqui evita la aclaracion de despues.
            "Guarda los archivos en tu equipo: estos enlaces dejan de funcionar "
            "en {}, y volver a abrir esta página consume una de tus "
            "sesiones.".format(plazo_de_los_enlaces())
            if filas else ""
        ),
        acceso=acceso,
        aviso_acceso=aviso_acceso,
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


# ---------------------------------------------------------------------------
# MODO DEMOSTRACION
# ---------------------------------------------------------------------------
# Permite abrir cualquiera de las pantallas en el sitio real, sin pagar y sin
# tocar el catalogo. Se protege con la misma clave del cron (CRON_SECRET):
#
#     /api/gracias?demo=entrega-kit&clave=TU_CRON_SECRET
#
# Sin la clave correcta no pasa nada: se sigue de largo al flujo normal, asi
# que un visitante que adivine el parametro solo vera la pantalla de siempre.
# Si CRON_SECRET esta vacia, el modo demostracion queda apagado por completo.
# ---------------------------------------------------------------------------

CLAVE_DEMO = os.environ.get("CRON_SECRET", "")

_DEMO_KIT = {"nombre": "Kit Antes de Emprender", "tipo": "kit", "enlace": None}
_DEMO_EBOOK = {
    "nombre": "Antes de Emprender: Finanzas e Impuestos que Nadie te Explica",
    "tipo": "ebook", "enlace": None,
}
_DEMO_CURSO = {
    "nombre": "Kit Antes de Emprender", "tipo": "kit",
    "enlace": "https://www.conseri.mx/minicurso",
}
_DEMO_ASESORIA = {
    "nombre": "Asesoría personalizada de diagnóstico", "tipo": "asesoria",
    "enlace": "https://bookings.cloud.microsoft/book/DiagnsticoConseri@conseri.mx/",
}


def _filas_demo(*nombres):
    return "".join(fila_archivo(n, "#") for n in nombres)


def pantalla_demo(nombre):
    """Devuelve el HTML de una pantalla de ejemplo, o None si el nombre no existe."""
    pantallas = {
        "entrega-kit": lambda: pagina_de_entrega(
            _DEMO_KIT,
            _filas_demo("Antes de Emprender (PDF)", "Plantillas del Kit (Excel)"),
            aviso_acceso=texto_de_vigencia(
                datetime.now(timezone.utc) + timedelta(days=DIAS_DE_ACCESO)),
            banda=banda_de_sesion(
                1,
                (datetime.now(timezone.utc)
                 + timedelta(minutes=MINUTOS_DE_SESION)).isoformat()),
        ),
        "entrega-ebook": lambda: pagina_de_entrega(
            _DEMO_EBOOK, _filas_demo("Antes de Emprender (PDF)")
        ),
        "entrega-curso": lambda: pagina_de_entrega(
            _DEMO_CURSO,
            _filas_demo("Antes de Emprender (PDF)", "Plantillas del Kit (Excel)"),
            bloque_de_acceso(_DEMO_CURSO),
        ),
        "entrega-asesoria": lambda: pagina_de_entrega(
            _DEMO_ASESORIA, "", bloque_de_acceso(_DEMO_ASESORIA)
        ),
        "cerrada-vencida": lambda: pagina_de_acceso_cerrado(
            "Esta liga ya venció",
            "Esta compra estuvo disponible {} después del pago.".format(
                plazo_de_la_liga()),
        ),
        "cerrada-limite": lambda: pagina_de_acceso_cerrado(
            "Esta liga alcanzó su límite",
            "Esta compra ya usó sus {} sesiones de descarga.".format(
                LIMITE_DE_APERTURAS),
        ),
        "entrega-ultima": lambda: pagina_de_entrega(
            _DEMO_KIT,
            _filas_demo("Antes de Emprender (PDF)", "Plantillas del Kit (Excel)"),
            banda=banda_de_sesion(
                LIMITE_DE_APERTURAS,
                (datetime.now(timezone.utc)
                 + timedelta(minutes=MINUTOS_DE_SESION)).isoformat()),
        ),
        "espera-pendiente": lambda: pagina_de_espera(
            "Tu pago aparece como <strong>pending_waiting_payment</strong>. Algunos "
            "medios de pago (como el efectivo o la transferencia) tardan unas horas."
        ),
        "espera-sin-id": lambda: pagina_de_espera(
            "No encontramos el número de pago en el enlace."
        ),
        "espera-sin-consulta": lambda: pagina_de_espera(
            "No pudimos consultar tu pago en este momento."
        ),
        "espera-sin-producto": lambda: pagina_de_espera(
            "Tu pago está aprobado, pero no logramos identificar el producto."
        ),
        "espera-sin-archivos": lambda: pagina_de_espera(
            "Tu pago está aprobado, pero los archivos no están disponibles ahora mismo."
        ),
    }

    constructor = pantallas.get(nombre)
    return constructor() if constructor else None


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        consulta = parse_qs(urlparse(self.path).query)

        # ---- Modo demostracion (solo con la clave correcta) ----
        pedida = (consulta.get("demo") or [""])[0]
        clave = (consulta.get("clave") or [""])[0]
        if pedida and CLAVE_DEMO and clave == CLAVE_DEMO:
            html = pantalla_demo(pedida)
            if html:
                return responder_html(self, 200, html)

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

        # ---- Caducidad de la liga ----
        # Se cuenta desde que Mercado Pago aprobo el pago, no desde ahora.
        vence = fecha_de_vencimiento(pago.get("date_approved"))
        if vence and datetime.now(timezone.utc) > vence:
            return responder_html(self, 200, pagina_de_acceso_cerrado(
                "Esta liga ya venció",
                "Esta compra estuvo disponible {} después del pago."
                .format(plazo_de_la_liga())
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

        # ---- Sesiones de descarga ----
        # Se cuenta ANTES de generar los enlaces: si ya se acabaron las
        # sesiones, no tiene sentido pedirle firmas a Supabase.
        num_apertura, fin_sesion = registrar_apertura(id_pago)
        if num_apertura is not None and num_apertura > LIMITE_DE_APERTURAS:
            return responder_html(self, 200, pagina_de_acceso_cerrado(
                "Esta liga alcanzó su límite",
                "Esta compra ya usó sus {} sesiones de descarga.".format(
                    LIMITE_DE_APERTURAS)
            ))

        # ---- Generar enlaces frescos ----
        # Los archivos marcados con "formato": "video" en catalogo.json se
        # muestran en un reproductor; el resto, como lista de descargas.
        filas = []
        videos = []
        for archivo in producto.get("archivos", []):
            url = enlace_temporal(archivo["ruta"])
            if not url:
                continue
            if archivo.get("formato") == "video":
                videos.append(bloque_video(archivo["nombre"], url))
            else:
                filas.append(fila_archivo(archivo["nombre"], url))

        acceso = bloque_de_acceso(producto)

        if not filas and not videos and not acceso:
            return responder_html(self, 200, pagina_de_espera(
                "Tu pago está aprobado, pero los archivos no están disponibles "
                "ahora mismo."
            ))

        return responder_html(
            self, 200,
            pagina_de_entrega(
                producto, "".join(filas), acceso, "".join(videos),
                venta={
                    "id_pago": id_pago,
                    "monto": pago.get("transaction_amount"),
                    "clave": clave,
                },
                aviso_acceso=texto_de_vigencia(vence),
                banda=banda_de_sesion(num_apertura, fin_sesion),
            )
        )

    def log_message(self, formato, *args):
        return
