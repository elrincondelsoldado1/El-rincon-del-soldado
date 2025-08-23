document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form-reserva");
  const mensaje = document.getElementById("mensaje-reserva");

  if (form) {
    form.addEventListener("submit", e => {
      e.preventDefault();
      mensaje.innerHTML = "<p style='color:lightgreen'>¡Reserva enviada correctamente!</p>";
      form.reset();
    });
  }
});

// /static/js/reserva.js
(function () {
  // RUTAS LITERALES (si tu app cuelga de la raíz)
  const API_MESAS = "/api/mesas-disponibles";
  const API_RES   = "/api/reservas";

  const fecha = document.getElementById("f-res");
  const hora  = document.getElementById("h-res");
  const pers  = document.getElementById("p-res");
  const mesa  = document.getElementById("mesa-id");
  const disp  = document.getElementById("disp-msg");

  if (!fecha || !hora || !pers || !mesa || !disp) {
    console.warn("[reservas] Falta algún id (f-res, h-res, p-res, mesa-id, disp-msg)");
    return;
  }

  async function cargarMesas() {
    try {
      mesa.innerHTML = "";
      disp.textContent = "";

      const f = fecha.value;
      const h = hora.value;
      const p = pers.value;

      if (!f || !h || !p) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Completa fecha, hora y personas";
        opt.disabled = true; opt.selected = true;
        mesa.appendChild(opt);
        return;
      }

      const url = `${API_MESAS}?fecha=${encodeURIComponent(f)}&hora=${encodeURIComponent(h)}&personas=${encodeURIComponent(p)}`;
      const r = await fetch(url, { headers: { Accept: "application/json" } });
      if (!r.ok) {
        console.error("Error HTTP al pedir mesas:", r.status, await r.text());
        disp.textContent = "No se pudo consultar la disponibilidad.";
        return;
      }
      const data = await r.json();

      if (!Array.isArray(data) || data.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Sin mesas disponibles";
        opt.disabled = true; opt.selected = true;
        mesa.appendChild(opt);
        disp.textContent = "No hay mesas disponibles para ese tramo.";
        return;
      }

      data.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = `${m.nombre} (${m.capacidad} pax)`;
        mesa.appendChild(opt);
      });
      disp.textContent = `Disponibles: ${data.length}`;
    } catch (err) {
      console.error("Excepción al cargar mesas:", err);
      disp.textContent = "No se pudo consultar la disponibilidad.";
      mesa.innerHTML = "";
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "Error de disponibilidad";
      opt.disabled = true; opt.selected = true;
      mesa.appendChild(opt);
    }
  }

  ["change", "blur"].forEach((ev) => {
    fecha.addEventListener(ev, cargarMesas);
    hora.addEventListener(ev, cargarMesas);
  });
  pers.addEventListener("input", cargarMesas);

  window.addEventListener("DOMContentLoaded", cargarMesas);
})();
