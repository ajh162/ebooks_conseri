/* ============================================================================
   CONSERI · script.js
   ----------------------------------------------------------------------------
   1. Barra superior: fondo sólido al bajar + barra naranja de progreso
   2. Revelado de secciones al entrar en pantalla
   3. Libro en 3D que sigue el cursor
   4. Tarjetas con inclinación 3D (data-inclina)
   5. Desglose de Carlos: barras y cifras que se animan solas
   6. Línea del índice que se llena con el scroll
   7. Botones de compra: hablan con /api/checkout
   ============================================================================ */

(function () {
  'use strict';

  const quietud = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finoParaTocar = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  /* =========================================================================
     1. BARRA SUPERIOR Y PROGRESO
     ========================================================================= */

  const barra = document.getElementById('barra');
  const progreso = document.getElementById('progreso');
  const relleno = document.getElementById('indice-relleno');
  const indice = document.getElementById('indice');

  let pendiente = false;

  function alDesplazar() {
    const y = window.scrollY;

    if (barra) barra.classList.toggle('barra--fija', y > 60);

    if (progreso) {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const avance = total > 0 ? (y / total) * 100 : 0;
      progreso.style.width = avance + '%';
    }

    /* 6. La línea naranja del índice se llena conforme la sección pasa */
    if (indice && relleno) {
      const caja = indice.getBoundingClientRect();
      const visto = window.innerHeight * 0.6 - caja.top;
      const proporcion = Math.max(0, Math.min(1, visto / caja.height));
      relleno.style.height = (proporcion * 100) + '%';
    }

    pendiente = false;
  }

  window.addEventListener('scroll', () => {
    if (!pendiente) {
      pendiente = true;
      requestAnimationFrame(alDesplazar);
    }
  }, { passive: true });

  alDesplazar();

  /* =========================================================================
     2. REVELADO AL ENTRAR EN PANTALLA
     ========================================================================= */

  const animables = document.querySelectorAll('.animar');

  animables.forEach((el) => {
    const retraso = el.dataset.retraso;
    if (retraso) el.style.transitionDelay = retraso + 'ms';
  });

  if ('IntersectionObserver' in window && !quietud) {
    const vigia = new IntersectionObserver((entradas) => {
      entradas.forEach((entrada) => {
        if (!entrada.isIntersecting) return;
        entrada.target.classList.add('visible');
        vigia.unobserve(entrada.target);
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

    animables.forEach((el) => vigia.observe(el));
  } else {
    animables.forEach((el) => el.classList.add('visible'));
  }

  /* =========================================================================
     3. LIBRO EN 3D
     -------------------------------------------------------------------------
     El cursor mueve dos variables CSS y el CSS hace el resto. Solo en equipos
     con ratón: en celular el libro se queda con su inclinación de reposo.
     ========================================================================= */

  const escena = document.getElementById('escena');
  const libro = document.getElementById('libro');

  if (escena && libro && finoParaTocar && !quietud) {
    escena.addEventListener('mousemove', (evento) => {
      const caja = escena.getBoundingClientRect();
      const x = (evento.clientX - caja.left) / caja.width - 0.5;
      const y = (evento.clientY - caja.top) / caja.height - 0.5;

      libro.style.setProperty('--giro-y', (-26 + x * 46) + 'deg');
      libro.style.setProperty('--giro-x', (-8 - y * 22) + 'deg');
    });

    escena.addEventListener('mouseleave', () => {
      libro.style.setProperty('--giro-y', '-26deg');
      libro.style.setProperty('--giro-x', '-8deg');
    });
  }

  /* =========================================================================
     4. TARJETAS CON INCLINACIÓN
     ========================================================================= */

  if (finoParaTocar && !quietud) {
    document.querySelectorAll('[data-inclina]').forEach((tarjeta) => {
      tarjeta.addEventListener('mousemove', (evento) => {
        const caja = tarjeta.getBoundingClientRect();
        const x = (evento.clientX - caja.left) / caja.width - 0.5;
        const y = (evento.clientY - caja.top) / caja.height - 0.5;

        tarjeta.style.transform =
          'perspective(900px) rotateY(' + (x * 9) + 'deg) rotateX(' +
          (-y * 9) + 'deg) translateY(-6px)';
      });

      tarjeta.addEventListener('mouseleave', () => {
        tarjeta.style.transform = '';
      });
    });
  }

  /* =========================================================================
     5. DESGLOSE DE CARLOS
     -------------------------------------------------------------------------
     Cuando la sección entra en pantalla, las barras crecen y las cifras
     cuentan desde cero. Es la parte que sustituye a la calculadora: cuenta la
     misma idea sin pedirle nada al visitante.
     ========================================================================= */

  const desglose = document.getElementById('desglose');

  const pesos = new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
    maximumFractionDigits: 0,
  });

  function contar(elemento, hasta, duracion) {
    const signo = elemento.dataset.signo || '';
    const arranque = performance.now();

    function paso(ahora) {
      const avance = Math.min(1, (ahora - arranque) / duracion);
      /* Curva suave: rápido al inicio, lento al final */
      const suave = 1 - Math.pow(1 - avance, 3);
      elemento.textContent = signo + pesos.format(Math.round(hasta * suave));
      if (avance < 1) requestAnimationFrame(paso);
    }

    requestAnimationFrame(paso);
  }

  function animarDesglose() {
    desglose.querySelectorAll('.desglose__barra span').forEach((barra, i) => {
      setTimeout(() => {
        barra.style.width = barra.dataset.ancho + '%';
      }, i * 160);
    });

    desglose.querySelectorAll('.desglose__monto').forEach((monto, i) => {
      setTimeout(() => {
        contar(monto, Number(monto.dataset.hasta), 1100);
      }, i * 160);
    });
  }

  if (desglose) {
    if (quietud || !('IntersectionObserver' in window)) {
      /* Sin animación: se muestran los valores finales de una vez */
      desglose.querySelectorAll('.desglose__barra span').forEach((b) => {
        b.style.width = b.dataset.ancho + '%';
      });
      desglose.querySelectorAll('.desglose__monto').forEach((m) => {
        m.textContent = (m.dataset.signo || '') + pesos.format(Number(m.dataset.hasta));
      });
    } else {
      const vigiaDesglose = new IntersectionObserver((entradas) => {
        entradas.forEach((entrada) => {
          if (!entrada.isIntersecting) return;
          animarDesglose();
          vigiaDesglose.unobserve(entrada.target);
        });
      }, { threshold: 0.35 });

      vigiaDesglose.observe(desglose);
    }
  }

  /* Año del pie, siempre al día */
  const anio = document.getElementById('anio');
  if (anio) anio.textContent = new Date().getFullYear();

  /* =========================================================================
     7. BOTONES DE COMPRA
     -------------------------------------------------------------------------
     Cada botón lleva data-producto="clave". Esa clave debe existir en
     api/catalogo.json. El navegador nunca manda el precio: solo la clave, y el
     servidor busca cuánto cuesta. Así nadie puede pagar $1 desde la consola.
     ========================================================================= */

  const aviso = document.getElementById('aviso-compra');

  function mostrarAviso(texto) {
    if (!aviso) return;
    aviso.textContent = texto;
    aviso.hidden = false;
  }

  document.querySelectorAll('[data-producto]').forEach((boton) => {
    boton.addEventListener('click', async () => {
      const producto = boton.dataset.producto;
      const textoOriginal = boton.textContent;

      if (aviso) aviso.hidden = true;
      boton.disabled = true;
      boton.textContent = 'Preparando tu pago…';

      try {
        const respuesta = await fetch('/api/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ producto: producto }),
        });

        const datos = await respuesta.json();

        if (!respuesta.ok || !datos.url_pago) {
          throw new Error(datos.error || 'No se pudo crear el pago.');
        }

        window.location.href = datos.url_pago;

      } catch (error) {
        boton.disabled = false;
        boton.textContent = textoOriginal;
        mostrarAviso(
          'No pudimos abrir la pantalla de pago. Vuelve a intentarlo en un momento ' +
          'o escríbenos a contacto@conseri.mx y te lo enviamos por otro medio.'
        );
        console.error('Error al crear el pago:', error);
      }
    });
  });
})();
