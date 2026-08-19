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
CORREO_REMITENTE  = os.environ.get("CORREO_REMITENTE", "hola@conseri.mx")
CORREO_CONTACTO   = os.environ.get("CORREO_CONTACTO", "contacto@conseri.mx")

# Solo para pruebas sin dominio verificado: si trae valor, TODOS los correos de
# entrega se mandan a esta direccion en vez de a la del comprador.
# Vaciala en cuanto el dominio este verificado en Resend.
CORREO_PRUEBA     = os.environ.get("CORREO_PRUEBA", "")

SITIO_URL = os.environ.get("SITIO_URL", "https://www.conseri.mx").rstrip("/")

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
    cabeceras.setdefault("User-Agent", "CONSERI-Sitio/1.0 (+https://www.conseri.mx)")
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

def enviar_correo(destino, producto, url_gracias, enlaces):
    """Manda el correo con el acceso al material.

    Ojo: para producción el remitente TIENE que ser un correo de un dominio
    verificado en Resend (SPF/DKIM). Con onboarding@resend.dev solo se entrega
    al correo de la propia cuenta de Resend, que sirve para probar.
    """
    if not RESEND_API_KEY:
        return False

    filas = "".join(
        '<li style="margin:6px 0"><a href="{}" style="color:#3173A7">{}</a></li>'.format(
            enlace["url"], enlace["nombre"]
        )
        for enlace in enlaces
    )

    extra = ""
    if producto.get("enlace"):
        extra = (
            '<p style="margin:18px 0 0">Tu acceso en línea: '
            '<a href="{}" style="color:#3173A7">{}</a></p>'
        ).format(producto["enlace"], producto["enlace"])

    html = """
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;
                color:#112234;line-height:1.6">
      <p style="font-size:13px;letter-spacing:2px;color:#F4882A;margin:0 0 6px">CONSERI</p>
      <h1 style="font-size:22px;margin:0 0 16px;color:#112234">Ya tienes tu material</h1>

      <p>Gracias por tu compra de <strong>{nombre}</strong>.</p>

      <p style="margin-bottom:6px">Descarga tus archivos aquí:</p>
      <ul style="padding-left:18px;margin:0">{filas}</ul>
      {extra}

      <p style="margin:24px 0">
        <a href="{gracias}"
           style="background:#F4882A;color:#2A1200;text-decoration:none;
                  padding:13px 26px;border-radius:999px;font-weight:bold;
                  display:inline-block">Abrir mi página de descarga</a>
      </p>

      <p style="font-size:13px;color:#5E7080">
        Los enlaces de descarga vencen en {horas} horas. Si se te vencen, abre de nuevo
        tu página de descarga o escríbenos a {contacto} y te los reponemos.
      </p>

      <hr style="border:none;border-top:1px solid #D6E4F0;margin:28px 0">

      <p style="font-size:12px;color:#5E7080">
        Material educativo e informativo. No constituye asesoría fiscal, contable,
        financiera ni legal, ni sustituye el análisis personalizado de un especialista.
      </p>
    </div>
    """.format(
        nombre=producto["nombre"],
        filas=filas,
        extra=extra,
        gracias=url_gracias,
        horas=HORAS_DE_VIGENCIA,
        contacto=CORREO_CONTACTO,
    )

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
