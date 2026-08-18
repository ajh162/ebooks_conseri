/* ============================================================================
   CONSERI · fondo.js
   ----------------------------------------------------------------------------
   Dibuja el fondo animado de la portada: una constelación de iconos de línea
   (los mismos temas que usa la marca en sus patrones: documentos, monedas,
   gráficas, calculadora, sobres) flotando muy despacio y unidos por hilos
   cuando quedan cerca.

   Es discreto a propósito: es una firma contable, no una pantalla de juego.
   Si el visitante activó "reducir movimiento", se dibuja una vez y se detiene.
   ============================================================================ */

(function () {
  'use strict';

  const lienzo = document.getElementById('fondo');
  if (!lienzo) return;

  const ctx = lienzo.getContext('2d');
  const quietud = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* Los colores se leen de las variables CSS: si cambia la paleta en
     styles.css, el fondo se actualiza solo. */
  const estilos = getComputedStyle(document.documentElement);
  const color = (nombre, respaldo) =>
    (estilos.getPropertyValue(nombre) || respaldo).trim();

  const AZUL = color('--azul-claro', '#6BA3CF');
  const NARANJA = color('--naranja', '#F4882A');

  let ancho = 0, alto = 0, anchoPrevio = 0, movil = false;
  let piezas = [];
  let raton = { x: -9999, y: -9999 };

  /* ---------------------------------------------------------------------
     ICONOS
     Cada uno se dibuja con trazos simples dentro de una caja de 20x20,
     centrada en el origen. Nada de imágenes: todo es canvas.
  --------------------------------------------------------------------- */
  const iconos = [
    // Documento
    function (c) {
      c.beginPath();
      c.roundRect(-7, -9, 14, 18, 1.5);
      c.moveTo(-3.5, -4); c.lineTo(3.5, -4);
      c.moveTo(-3.5, 0);  c.lineTo(3.5, 0);
      c.moveTo(-3.5, 4);  c.lineTo(1, 4);
      c.stroke();
    },
    // Moneda con signo de pesos
    function (c) {
      c.beginPath();
      c.arc(0, 0, 8.5, 0, Math.PI * 2);
      c.moveTo(0, -5); c.lineTo(0, 5);
      c.moveTo(-2.5, -3); c.lineTo(2.5, -3);
      c.moveTo(-2.5, 3);  c.lineTo(2.5, 3);
      c.stroke();
    },
    // Gráfica de barras
    function (c) {
      c.beginPath();
      c.moveTo(-8, 8); c.lineTo(8, 8);
      c.moveTo(-5, 8); c.lineTo(-5, 1);
      c.moveTo(0, 8);  c.lineTo(0, -4);
      c.moveTo(5, 8);  c.lineTo(5, -7);
      c.stroke();
    },
    // Calculadora
    function (c) {
      c.beginPath();
      c.roundRect(-7, -9, 14, 18, 2);
      c.moveTo(-4.5, -5.5); c.lineTo(4.5, -5.5);
      c.stroke();
      c.beginPath();
      for (let fx = -4; fx <= 4; fx += 4) {
        for (let fy = -1; fy <= 5; fy += 3) {
          c.moveTo(fx, fy); c.arc(fx, fy, 0.9, 0, Math.PI * 2);
        }
      }
      c.stroke();
    },
    // Línea de crecimiento con flecha
    function (c) {
      c.beginPath();
      c.moveTo(-9, 6); c.lineTo(-3, 0); c.lineTo(1, 3); c.lineTo(9, -6);
      c.moveTo(4, -6); c.lineTo(9, -6); c.lineTo(9, -1);
      c.stroke();
    },
    // Sobre
    function (c) {
      c.beginPath();
      c.roundRect(-9, -6, 18, 12, 1.5);
      c.moveTo(-9, -6); c.lineTo(0, 1); c.lineTo(9, -6);
      c.stroke();
    },
  ];

  /* ---------- Tamaño ---------- */
  function ajustar() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const caja = lienzo.getBoundingClientRect();
    ancho = caja.width;
    alto = caja.height;
    movil = ancho < 780;

    lienzo.width = ancho * dpr;
    lienzo.height = alto * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    /* Solo se resiembra si cambió el ANCHO. En iPhone, al ocultarse la barra
       del navegador cambia la altura; si resembráramos ahí, el fondo
       parpadearía en cada scroll. */
    if (ancho !== anchoPrevio) {
      anchoPrevio = ancho;
      sembrar();
    }
  }

  /* ---------- Piezas flotantes ---------- */
  function sembrar() {
    piezas = [];
    const cuantas = movil ? 9 : 18;

    for (let i = 0; i < cuantas; i++) {
      piezas.push({
        x: Math.random() * ancho,
        y: Math.random() * alto,
        vx: (Math.random() - 0.5) * 0.16,
        vy: (Math.random() - 0.5) * 0.16,
        escala: 0.75 + Math.random() * 0.85,
        giro: (Math.random() - 0.5) * 0.5,
        velGiro: (Math.random() - 0.5) * 0.0022,
        icono: iconos[Math.floor(Math.random() * iconos.length)],
        naranja: Math.random() < 0.22,     // unos pocos en color de acento
        alfa: 0.1 + Math.random() * 0.16,
      });
    }
  }

  /* ---------- Hilos entre piezas cercanas ---------- */
  function dibujarHilos() {
    const limite = movil ? 150 : 210;
    ctx.lineWidth = 1;

    for (let i = 0; i < piezas.length; i++) {
      for (let j = i + 1; j < piezas.length; j++) {
        const dx = piezas[i].x - piezas[j].x;
        const dy = piezas[i].y - piezas[j].y;
        const dist = Math.hypot(dx, dy);
        if (dist > limite) continue;

        ctx.globalAlpha = (1 - dist / limite) * 0.12;
        ctx.strokeStyle = AZUL;
        ctx.beginPath();
        ctx.moveTo(piezas[i].x, piezas[i].y);
        ctx.lineTo(piezas[j].x, piezas[j].y);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  /* ---------- Ciclo ---------- */
  function pintar() {
    ctx.clearRect(0, 0, ancho, alto);

    piezas.forEach((p) => {
      /* Movimiento base */
      p.x += p.vx;
      p.y += p.vy;
      p.giro += p.velGiro;

      /* Empujón suave para alejarse del cursor */
      const dx = p.x - raton.x;
      const dy = p.y - raton.y;
      const dist = Math.hypot(dx, dy);
      if (dist < 130 && dist > 0.1) {
        const fuerza = (130 - dist) / 130 * 0.5;
        p.x += (dx / dist) * fuerza;
        p.y += (dy / dist) * fuerza;
      }

      /* Reaparecen del otro lado al salirse */
      const margen = 40;
      if (p.x < -margen) p.x = ancho + margen;
      if (p.x > ancho + margen) p.x = -margen;
      if (p.y < -margen) p.y = alto + margen;
      if (p.y > alto + margen) p.y = -margen;

      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.giro);
      ctx.scale(p.escala, p.escala);
      ctx.globalAlpha = p.alfa;
      ctx.strokeStyle = p.naranja ? NARANJA : AZUL;
      ctx.lineWidth = 1.4;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      p.icono(ctx);
      ctx.restore();
    });

    ctx.globalAlpha = 1;
    dibujarHilos();

    if (!quietud) requestAnimationFrame(pintar);
  }

  /* ---------- Arranque ---------- */
  ajustar();
  window.addEventListener('resize', ajustar);

  if (!quietud) {
    window.addEventListener('mousemove', (evento) => {
      const caja = lienzo.getBoundingClientRect();
      raton.x = evento.clientX - caja.left;
      raton.y = evento.clientY - caja.top;
    }, { passive: true });

    window.addEventListener('mouseout', () => {
      raton.x = -9999;
      raton.y = -9999;
    });
  }

  requestAnimationFrame(pintar);
})();
