function agregarAlCarrito(id) {
  fetch('/api/carrito', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ plato_id: id })
  })
  .then(response => {
    if (!response.ok) throw new Error("Error al añadir al carrito");
    return response.json();
  })
  .then(data => {
    alert("Producto añadido al carrito");
  })
  .catch(error => {
    console.error("Error:", error);
    alert("Hubo un problema al añadir el producto");
  });
}
