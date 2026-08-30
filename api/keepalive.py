"""
============================================================================
CONSERI · /api/keepalive
----------------------------------------------------------------------------
Supabase pausa los proyectos del plan gratuito cuando no reciben "suficiente
actividad de base de datos" durante una semana. El umbral no lo publican, pero
una sola consulta de lectura al dia resulto no alcanzar: de ahi el aviso de
pausa que llego pese a que el cron corria bien todos los dias.

Por eso esta version hace tres cosas en vez de una, y sobre todo ESCRIBE:

  1. Inserta un renglon en la tabla "latidos"   <- escritura, no hay duda
  2. Borra los latidos viejos                    <- otra escritura, y mantiene
                                                    la tabla del tamano de un
                                                    pañuelo
  3. Lee la tabla de entregas                    <- la consulta de siempre

ANTES DE DESPLEGAR hay que crear la tabla en Supabase (SQL Editor):

    create table if not exists latidos (
      id        bigserial primary key,
      origen    text,
      creado_en timestamptz default now()
    );
    alter table latidos enable row level security;

La ruta esta protegida con CRON_SECRET para que nadie de fuera la llame:
Vercel manda el encabezado Authorization con ese secreto.

NOTA SOBRE LOS REGISTROS
----------------------------------------------------------------------------
Vercel guarda la linea de acceso (el "200 OK") pero NO el cuerpo de la
respuesta. Por eso la funcion escribe el resultado en los registros: asi, en
Vercel -> Logs, al expandir la ejecucion se lee que contesto Supabase de
verdad, en vez de suponerlo.
============================================================================
"""

import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
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

# Cuantos dias de latidos se conservan antes de borrarlos
DIAS_DE_HISTORIAL = 7


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        autorizacion = self.headers.get("Authorization") or ""

        if CRON_SECRET and autorizacion != "Bearer " + CRON_SECRET:
            print("keepalive: llamada sin autorizacion valida")
            return responder_json(self, 401, {"error": "no autorizado"})

        # ---- ¿Estan cargadas las credenciales de Supabase? ----
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            print("keepalive: FALTAN las variables de Supabase en el servidor",
                  "| hay URL:", bool(SUPABASE_URL),
                  "| hay llave:", bool(SUPABASE_SERVICE_KEY))
            return responder_json(self, 200, {
                "base_activa": False,
                "motivo": "faltan SUPABASE_URL o SUPABASE_SERVICE_KEY",
            })

        cabeceras = _cabeceras_supabase()
        cabeceras["Content-Type"] = "application/json"

        resultado = {}

        # ---- 1. Escritura: dejar constancia del latido ----
        codigo_insercion, respuesta_insercion = pedir(
            SUPABASE_URL + "/rest/v1/latidos",
            metodo="POST",
            cabeceras=cabeceras,
            cuerpo=[{"origen": "vercel-cron"}],
        )
        resultado["insercion"] = codigo_insercion

        # ---- 2. Limpieza: borrar los latidos viejos ----
        # Es otra escritura y ademas evita que la tabla crezca sin control.
        #
        # La fecha va codificada a proposito: en formato ISO termina en
        # "+00:00", y dentro de una direccion web el signo + significa espacio.
        # Sin codificar, a Supabase le llega una fecha rota y responde 400.
        corte = (datetime.now(timezone.utc) - timedelta(days=DIAS_DE_HISTORIAL)).isoformat()
        codigo_borrado, respuesta_borrado = pedir(
            SUPABASE_URL + "/rest/v1/latidos?creado_en=lt."
            + urllib.parse.quote(corte, safe=""),
            metodo="DELETE",
            cabeceras=cabeceras,
        )
        resultado["borrado"] = codigo_borrado

        # ---- 3. Lectura: la consulta de siempre ----
        codigo_lectura, _ = pedir(
            SUPABASE_URL + "/rest/v1/entregas?select=id&limit=1",
            cabeceras=_cabeceras_supabase(),
        )
        resultado["lectura"] = codigo_lectura

        # La escritura es la que cuenta como actividad de verdad
        activa = codigo_insercion in (200, 201, 204)

        if activa:
            print("keepalive: OK | insercion", codigo_insercion,
                  "| borrado", codigo_borrado,
                  "| lectura", codigo_lectura)
            if codigo_borrado not in (200, 204):
                # No es grave (la tabla es diminuta), pero conviene saberlo
                print("keepalive: la limpieza no borro nada ->", respuesta_borrado)
        else:
            print("keepalive: FALLO la escritura |", codigo_insercion,
                  "->", respuesta_insercion,
                  "| ¿existe la tabla 'latidos' en Supabase?")

        resultado["base_activa"] = activa
        return responder_json(self, 200, resultado)

    def log_message(self, formato, *args):
        return
