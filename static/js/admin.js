const tSel = document.getElementById('tabla');
const tbl  = document.getElementById('grid-admin');

function carga(){
 fetch('/api/admin/'+tSel.value)
   .then(r=>r.json())
   .then(data=>{
      if(!data.length){tbl.innerHTML='<tr><td>Sin datos</td></tr>';return;}
      const cols = Object.keys(data[0]);
      tbl.innerHTML = `<thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}
                       <th></th></tr></thead><tbody></tbody>`;
      const tb = tbl.querySelector('tbody');
      data.forEach(r=>{
        tb.insertAdjacentHTML('beforeend',`
          <tr>${cols.map(c=>`<td>${r[c]}</td>`).join('')}
          <td><button data-id="${r.id}">🗑️</button></td></tr>`);
      });
   });
}

tSel.onchange = carga;
tbl.onclick = e=>{
  if(!e.target.matches('[data-id]')) return;
  if(!confirm('¿Eliminar registro?')) return;
  fetch(`/api/admin/${tSel.value}?id=${e.target.dataset.id}`,{method:'DELETE'})
     .then(()=>carga());
};

carga();



document.addEventListener('DOMContentLoaded', () => {
  const table = document.querySelector('.admin-table');
  const inputName = document.getElementById('searchName');
  const selCat    = document.getElementById('filterCategory');
  const btnClear  = document.getElementById('clearFilters');
  if(!table || !inputName || !selCat) return;

  const CATEGORY_COL = 1; // columna Categoría
  const NAME_COL     = 2; // columna Nombre

  // Rellenar categorías
  const categories = new Set();
  table.querySelectorAll('tbody tr').forEach(tr => {
    const tds = tr.querySelectorAll('td');
    const cat = (tds[CATEGORY_COL]?.textContent || '').trim();
    if(cat) categories.add(cat);
  });
  [...categories].sort().forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    selCat.appendChild(opt);
  });

  // Filtrar
  const filter = () => {
    const q = inputName.value.toLowerCase().trim();
    const cat = selCat.value;
    table.querySelectorAll('tbody tr').forEach(tr => {
      const tds = tr.querySelectorAll('td');
      const nameTxt = (tds[NAME_COL]?.textContent || '').toLowerCase();
      const catTxt  = (tds[CATEGORY_COL]?.textContent || '').trim();
      const matchName = !q || nameTxt.includes(q);
      const matchCat  = !cat || catTxt === cat;
      tr.style.display = (matchName && matchCat) ? '' : 'none';
    });
  };

  inputName.addEventListener('input', filter);
  selCat.addEventListener('change', filter);
  btnClear.addEventListener('click', () => {
    inputName.value = ''; selCat.value = ''; filter();
  });
});


