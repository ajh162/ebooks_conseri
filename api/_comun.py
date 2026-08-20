"""
============================================================================
CONSERI · _comun.py
----------------------------------------------------------------------------
Funciones que usan todas las demás funciones del back-end. El nombre empieza
con guion bajo a propósito: así Vercel NO lo publica como una ruta web, solo
lo deja disponible para importarlo.

Aquí vive el trato con los tres servicios externos:
  · Mercado Pago  -> cobrar
  · Supabase      -> guardar la venta y entregar los archivos
  · Resend        -> mandar el correo con el enlace de descarga

Todo se hace con urllib (librería que ya trae Python), así que este proyecto
no necesita instalar dependencias y arranca más rápido en Vercel.
============================================================================
"""

import hashlib
import hmac
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# VARIABLES DE ENTORNO
# ---------------------------------------------------------------------------
# En local van en el archivo .env; en Vercel se cargan en:
#   Project -> Settings -> Environment Variables
# Los nombres deben escribirse EXACTAMENTE igual que aquí.
# ---------------------------------------------------------------------------

MP_ACCESS_TOKEN   = os.environ.get("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "")

SUPABASE_URL          = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET       = os.environ.get("SUPABASE_BUCKET", "productos")

RESEND_API_KEY    = os.environ.get("RESEND_API_KEY", "")
CORREO_REMITENTE  = os.environ.get("CORREO_REMITENTE", "hola@digitalconseri.com")
CORREO_CONTACTO   = os.environ.get("CORREO_CONTACTO", "contacto@conseri.mx")

# Solo para pruebas sin dominio verificado: si trae valor, TODOS los correos de
# entrega se mandan a esta direccion en vez de a la del comprador.
# Vaciala en cuanto el dominio este verificado en Resend.
CORREO_PRUEBA     = os.environ.get("CORREO_PRUEBA", "")

SITIO_URL = os.environ.get("SITIO_URL", "https://www.digitalconseri.com").rstrip("/")

# Cuántas horas dura el enlace de descarga antes de vencerse
HORAS_DE_VIGENCIA = int(os.environ.get("HORAS_DE_VIGENCIA", "72"))

_contexto_ssl = ssl.create_default_context()


# ---------------------------------------------------------------------------
# CATÁLOGO
# ---------------------------------------------------------------------------

def cargar_catalogo():
    """Lee api/catalogo.json. Para agregar un producto nuevo (otro ebook, un
    curso, un video), solo se edita ese archivo: nada de tocar código."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogo.json")
    with open(ruta, "r", encoding="utf-8") as archivo:
        return json.load(archivo)


def buscar_producto(clave):
    """Devuelve el producto o None si la clave no existe."""
    return cargar_catalogo().get(clave)


# ---------------------------------------------------------------------------
# PETICIONES HTTP (envoltura mínima sobre urllib)
# ---------------------------------------------------------------------------

def pedir(url, metodo="GET", cabeceras=None, cuerpo=None, tiempo=20):
    """Hace una petición y devuelve (codigo, diccionario_de_respuesta)."""
    datos = None
    cabeceras = dict(cabeceras or {})

    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
        cabeceras.setdefault("Content-Type", "application/json")

    # urllib se identifica por defecto como "Python-urllib/3.x". Cloudflare, que
    # protege a Resend, lo toma por bot y corta la peticion con el error 1010
    # antes de que llegue a Resend (por eso no aparecia ni en su panel).
    # Con un nombre propio la peticion pasa normal.
    cabeceras.setdefault("User-Agent", "CONSERI-Sitio/1.0 (+https://www.digitalconseri.com)")
    cabeceras.setdefault("Accept", "application/json")

    peticion = urllib.request.Request(url, data=datos, headers=cabeceras, method=metodo)

    try:
        with urllib.request.urlopen(peticion, timeout=tiempo, context=_contexto_ssl) as respuesta:
            texto = respuesta.read().decode("utf-8") or "{}"
            try:
                return respuesta.status, json.loads(texto)
            except json.JSONDecodeError:
                return respuesta.status, {"texto": texto}

    except urllib.error.HTTPError as error:
        texto = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(texto)
        except json.JSONDecodeError:
            return error.code, {"error": texto}

    except Exception as error:                      # red caída, timeout, etc.
        return 0, {"error": str(error)}


# ---------------------------------------------------------------------------
# MERCADO PAGO
# ---------------------------------------------------------------------------

def crear_preferencia(clave_producto, producto):
    """Crea la 'preferencia' de pago y devuelve el enlace al checkout.

    external_reference guarda la clave del producto: es lo que después lee el
    webhook para saber qué archivos entregar.
    """
    cuerpo = {
        "items": [{
            "id": clave_producto,
            "title": producto["nombre"],
            "description": producto.get("descripcion", ""),
            "quantity": 1,
            "currency_id": "MXN",
            "unit_price": float(producto["precio"]),
        }],
        "external_reference": clave_producto,
        "metadata": {"producto": clave_producto},
        "statement_descriptor": "CONSERI",
        "back_urls": {
            "success": SITIO_URL + "/api/gracias",
            "pending": SITIO_URL + "/api/gracias",
            "failure": SITIO_URL + "/#precios",
        },
        "auto_return": "approved",
        "notification_url": SITIO_URL + "/api/webhook",
    }

    codigo, respuesta = pedir(
        "https://api.mercadopago.com/checkout/preferences",
        metodo="POST",
        cabeceras={"Authorization": "Bearer " + MP_ACCESS_TOKEN},
        cuerpo=cuerpo,
    )

    if codigo not in (200, 201):
        return None, respuesta

    # init_point = producción · sandbox_init_point = pruebas
    return respuesta.get("init_point") or respuesta.get("sandbox_init_point"), respuesta


def consultar_pago(id_pago):
    """Pregunta a Mercado Pago el estado real de un pago.
    Nunca confiamos en lo que llega por la URL: siempre se verifica aquí."""
    codigo, respuesta = pedir(
        "https://api.mercadopago.com/v1/payments/" + str(id_pago),
        cabeceras={"Authorization": "Bearer " + MP_ACCESS_TOKEN},
    )
    return (respuesta if codigo == 200 else None)


def firma_valida(cabeceras, id_dato):
    """Comprueba que la notificación venga de verdad de Mercado Pago.

    Mercado Pago manda un encabezado x-signature con esta forma:
        ts=1712345678,v1=abcdef...
    y la firma se calcula sobre el texto:
        id:{id};request-id:{x-request-id};ts:{ts};
    """
    if not MP_WEBHOOK_SECRET:
        return False

    firma = cabeceras.get("x-signature") or cabeceras.get("X-Signature") or ""
    id_peticion = cabeceras.get("x-request-id") or cabeceras.get("X-Request-Id") or ""

    marca, valor = None, None
    for parte in firma.split(","):
        if "=" not in parte:
            continue
        nombre, contenido = parte.split("=", 1)
        nombre = nombre.strip()
        if nombre == "ts":
            marca = contenido.strip()
        elif nombre == "v1":
            valor = contenido.strip()

    if not marca or not valor:
        return False

    manifiesto = "id:{};request-id:{};ts:{};".format(id_dato, id_peticion, marca)
    calculada = hmac.new(
        MP_WEBHOOK_SECRET.encode("utf-8"),
        manifiesto.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(calculada, valor)


# ---------------------------------------------------------------------------
# SUPABASE
# ---------------------------------------------------------------------------

def _cabeceras_supabase():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": "Bearer " + SUPABASE_SERVICE_KEY,
    }


def enlace_temporal(ruta_archivo):
    """Genera una Signed URL: un enlace que sirve unas horas y luego se muere.
    Es lo que permite tener el bucket privado y aun así entregar el archivo."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None

    url = "{}/storage/v1/object/sign/{}/{}".format(
        SUPABASE_URL, SUPABASE_BUCKET, urllib.parse.quote(ruta_archivo)
    )

    codigo, respuesta = pedir(
        url,
        metodo="POST",
        cabeceras=_cabeceras_supabase(),
        cuerpo={"expiresIn": HORAS_DE_VIGENCIA * 3600},
    )

    if codigo != 200 or "signedURL" not in respuesta:
        return None

    return SUPABASE_URL + "/storage/v1" + respuesta["signedURL"]


def registrar_venta(id_pago, clave_producto, correo, monto):
    """Guarda la venta en la tabla 'entregas'.

    Usa upsert por payment_id: si Mercado Pago manda la misma notificación dos
    veces (pasa seguido), no se duplica el registro ni se manda doble correo.
    Devuelve True si el registro es NUEVO.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False

    cabeceras = _cabeceras_supabase()
    cabeceras["Content-Type"] = "application/json"
    cabeceras["Prefer"] = "resolution=ignore-duplicates,return=representation"

    codigo, respuesta = pedir(
        SUPABASE_URL + "/rest/v1/entregas",
        metodo="POST",
        cabeceras=cabeceras,
        cuerpo=[{
            "payment_id": str(id_pago),
            "producto": clave_producto,
            "correo": correo,
            "monto": monto,
        }],
    )

    # Con ignore-duplicates, si ya existía la lista vuelve vacía.
    if codigo in (200, 201) and isinstance(respuesta, list):
        return len(respuesta) > 0

    return codigo in (200, 201)


def despertar_base():
    """Consulta trivial para que Supabase (plan gratuito) no pause el proyecto
    por inactividad. La llama el cron diario de /api/keepalive."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    codigo, _ = pedir(
        SUPABASE_URL + "/rest/v1/entregas?select=id&limit=1",
        cabeceras=_cabeceras_supabase(),
    )
    return codigo == 200


# ---------------------------------------------------------------------------
# RESEND (correo de entrega)
# ---------------------------------------------------------------------------

def _fila_correo(enlace):
    """Un renglon de archivo dentro del correo.

    Todo va en tablas y con estilos en linea: es la unica forma de que se vea
    igual en Gmail, Outlook y Apple Mail. Los clientes de correo ignoran las
    hojas de estilo externas y varios ni siquiera soportan flexbox.
    """
    es_video = enlace.get("formato") == "video"
    nota = ("Se ve mejor desde tu página de descarga"
            if es_video else "Descargar")

    return """
          <tr>
            <td style="padding:0 0 10px 0">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                     style="background:#F4F7FA;border:1px solid #D6E4F0;border-radius:10px">
                <tr>
                  <td style="padding:14px 16px">
                    <a href="{url}" style="color:#112234;text-decoration:none;
                       font-family:Arial,Helvetica,sans-serif;font-weight:bold;
                       font-size:15px">{nombre}</a>
                    <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;
                                color:#F4882A;letter-spacing:1px;text-transform:uppercase;
                                padding-top:4px">{nota}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>""".format(url=enlace["url"], nombre=enlace["nombre"], nota=nota)


def plantilla_correo(producto, url_gracias, enlaces):
    """Arma el correo de entrega y devuelve (html, texto).

    Esta separado del envio para que la herramienta de previsualizacion
    (previsualizar.py) muestre exactamente el mismo correo que le llega al
    comprador, sin tener que mandar nada.
    """
    filas = "".join(_fila_correo(enlace) for enlace in enlaces)

    extra = ""
    if producto.get("enlace"):
        extra = """
          <tr>
            <td style="padding:6px 0 0 0;font-family:Arial,Helvetica,sans-serif;
                       font-size:14px;color:#5E7080">
              Tu acceso en línea:
              <a href="{enlace}" style="color:#3173A7">{enlace}</a>
            </td>
          </tr>""".format(enlace=producto["enlace"])

    html = """<!DOCTYPE html>
<html lang="es-MX">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tu material de CONSERI</title>
</head>
<body style="margin:0;padding:0;background:#F4F7FA">

<!-- Linea de vista previa: es lo que se lee en la bandeja antes de abrir -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0">
  Aquí están tus enlaces de descarga de {nombre}.
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#F4F7FA">
  <tr>
    <td align="center" style="padding:24px 12px">

      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="width:100%;max-width:600px">

        <!-- ================= CABECERA ================= -->
        <tr>
          <td align="center" style="background:#112234;border-radius:14px 14px 0 0;
                                    padding:34px 24px 30px">
            <img src="{sitio}/assets/logo-horizontal-blanco.png"
                 alt="CONSERI" width="190"
                 style="display:block;width:190px;max-width:70%;height:auto;border:0">
            <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;
                        letter-spacing:3px;text-transform:uppercase;color:#F4882A;
                        padding-top:20px">Pago confirmado</div>
            <div style="font-family:Arial,Helvetica,sans-serif;font-size:26px;
                        font-weight:bold;color:#ffffff;padding-top:8px;
                        line-height:1.25">Ya tienes tu material</div>
          </td>
        </tr>

        <!-- Filo naranja que separa cabecera y cuerpo -->
        <tr><td style="background:#F4882A;height:4px;line-height:4px;font-size:0">&nbsp;</td></tr>

        <!-- ================= CUERPO ================= -->
        <tr>
          <td style="background:#ffffff;padding:32px 28px 28px">

            <p style="margin:0 0 22px;font-family:Arial,Helvetica,sans-serif;
                      font-size:16px;line-height:1.6;color:#112234">
              Gracias por tu compra de <strong>{nombre}</strong>.
              Aquí está todo lo que incluye:
            </p>

            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              {filas}
              {extra}
            </table>

            <!-- Boton principal, armado con tabla para que Outlook lo respete -->
            <table role="presentation" cellpadding="0" cellspacing="0"
                   style="margin:26px auto 6px">
              <tr>
                <td align="center" style="background:#F4882A;border-radius:999px">
                  <a href="{gracias}"
                     style="display:inline-block;padding:15px 34px;
                            font-family:Arial,Helvetica,sans-serif;font-size:16px;
                            font-weight:bold;color:#2A1200;text-decoration:none">
                    Abrir mi página de descarga
                  </a>
                </td>
              </tr>
            </table>

            <p style="margin:18px 0 0;font-family:Arial,Helvetica,sans-serif;
                      font-size:13px;line-height:1.6;color:#5E7080;text-align:center">
              Los enlaces vencen en {horas} horas. Si se te pasan, vuelve a abrir tu
              página de descarga y se generan de nuevo.
            </p>

          </td>
        </tr>

        <!-- ================= PIE ================= -->
        <tr>
          <td style="background:#ffffff;border-top:1px solid #D6E4F0;
                     border-radius:0 0 14px 14px;padding:22px 28px 26px">
            <p style="margin:0 0 14px;font-family:Arial,Helvetica,sans-serif;
                      font-size:13px;line-height:1.6;color:#5E7080">
              ¿Algún problema con tu descarga? Responde este correo o escríbenos a
              <a href="mailto:{contacto}" style="color:#3173A7">{contacto}</a>.
            </p>
            <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:11px;
                      line-height:1.6;color:#8A9AA8">
              Material educativo e informativo. No constituye asesoría fiscal, contable,
              financiera ni legal, ni sustituye el análisis personalizado de un
              especialista. Obra en trámite de registro de derechos de autor.
            </p>
          </td>
        </tr>

        <tr>
          <td align="center" style="padding:18px 12px 0;
                     font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#8A9AA8">
            CONSERI · Consultoría Contable de Servicios Integrales
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>
</body>
</html>""".format(
        nombre=producto["nombre"],
        filas=filas,
        extra=extra,
        gracias=url_gracias,
        horas=HORAS_DE_VIGENCIA,
        contacto=CORREO_CONTACTO,
        sitio=SITIO_URL,
    )

    # Version en texto plano. Mejora la entregabilidad (los filtros desconfian
    # de los correos que solo traen HTML) y sirve para quien lee sin formato.
    texto = "CONSERI\n\nYa tienes tu material.\n\n"
    texto += "Gracias por tu compra de {}.\n\n".format(producto["nombre"])
    for enlace in enlaces:
        texto += "- {}: {}\n".format(enlace["nombre"], enlace["url"])
    if producto.get("enlace"):
        texto += "- Acceso en linea: {}\n".format(producto["enlace"])
    texto += "\nPagina de descarga: {}\n".format(url_gracias)
    texto += "\nLos enlaces vencen en {} horas.\n".format(HORAS_DE_VIGENCIA)
    texto += "Dudas: {}\n".format(CORREO_CONTACTO)

    return html, texto


def enviar_correo(destino, producto, url_gracias, enlaces):
    """Manda por Resend el correo con el acceso al material.

    Ojo: para produccion el remitente TIENE que ser un correo de un dominio
    verificado en Resend (SPF/DKIM). Con onboarding@resend.dev solo se entrega
    al correo de la propia cuenta de Resend, que sirve para probar.
    """
    if not RESEND_API_KEY:
        return False

    html, texto = plantilla_correo(producto, url_gracias, enlaces)

    # Mientras no haya dominio verificado, Resend solo entrega al correo de la
    # cuenta. Con CORREO_PRUEBA definido, todo se redirige ahi para poder probar.
    destinatario = CORREO_PRUEBA or destino

    codigo, respuesta = pedir(
        "https://api.resend.com/emails",
        metodo="POST",
        cabeceras={"Authorization": "Bearer " + RESEND_API_KEY},
        cuerpo={
            "from": "CONSERI <{}>".format(CORREO_REMITENTE),
            "to": [destinatario],
            "reply_to": CORREO_CONTACTO,
            "subject": "Tu acceso a " + producto["nombre"],
            "html": html,
            "text": texto,
        },
    )

    if codigo not in (200, 201):
        print("Resend rechazo el correo:", codigo, respuesta)

    return codigo in (200, 201)


# ---------------------------------------------------------------------------
# RESPUESTAS HTTP (para no repetir código en cada función)
# ---------------------------------------------------------------------------

def responder_json(handler, codigo, datos):
    cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    handler.send_response(codigo)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(cuerpo)))
    handler.end_headers()
    handler.wfile.write(cuerpo)


def responder_html(handler, codigo, html):
    cuerpo = html.encode("utf-8")
    handler.send_response(codigo)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(cuerpo)))
    handler.end_headers()
    handler.wfile.write(cuerpo)
