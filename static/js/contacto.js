document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form-contacto");
  const mensaje = document.getElementById("mensaje-contacto");

  if (form) {
    form.addEventListener("submit", e => {
      e.preventDefault();
      mensaje.innerHTML = "<p style='color:lightgreen'>¡Gracias por tu mensaje!</p>";
      form.reset();
    });
  }
});
