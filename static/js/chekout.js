document.addEventListener("DOMContentLoaded", function () {
  const carrito = JSON.parse(localStorage.getItem("carrito")) || [];

  const contenedor = document.getElementById("carrito-contenido");
  if (carrito.length === 0) {
    contenedor.innerHTML = "<p>Tu carrito está vacío.</p>";
  } else {
    const lista = document.createElement("ul");
    carrito.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      lista.appendChild(li);
    });
    contenedor.innerHTML = "";
    contenedor.appendChild(lista);
  }
});

function finalizarPedido() {
  alert("Pedido enviado correctamente.");
  localStorage.removeItem("carrito");
  location.reload();
}
