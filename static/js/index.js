function actualizarHora() {
    const ahora = new Date();
    const opciones = { hour: '2-digit', minute: '2-digit', second: '2-digit' };
    document.getElementById('hora').textContent = "Hora actual: " + ahora.toLocaleTimeString('es-ES', opciones);
}
setInterval(actualizarHora, 1000);
actualizarHora();


fetch('https://api.open-meteo.com/v1/forecast?latitude=41.3548&longitude=-1.6433&current_weather=true')
  .then(response => response.json())
  .then(data => {
      const temp = data.current_weather.temperature;
      const viento = data.current_weather.windspeed;
      document.getElementById('clima').textContent = `Calatayud: ${temp}°C - Viento: ${viento} km/h`;
  });



const carrusel = document.querySelectorAll(".carrusel-img");
let index = 0;

function mostrarSiguienteImagen() {
  carrusel.forEach((img, i) => img.classList.remove("active"));
  carrusel[index].classList.add("active");
  index = (index + 1) % carrusel.length;
}
setInterval(mostrarSiguienteImagen, 4000);
mostrarSiguienteImagen();


