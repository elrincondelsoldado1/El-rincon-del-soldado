# ────────────────────────────────────────────────
#  app.py  •  El Rincón del Soldado
# ────────────────────────────────────────────────
import os, smtplib, ssl, secrets
from datetime import timedelta, date, time, datetime
import json
from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, flash
)
from datetime import datetime
import psycopg2.extras
from werkzeug.utils import secure_filename
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer
from email.message import EmailMessage
import psycopg2.extras

import stripe

from db import get_db_connection as get_connection  # tu helper DB

# ───────── Config general ─────────
app = Flask(__name__)
load_dotenv()
app.secret_key = os.getenv('SECRET_KEY', 'musa')
bcrypt = Bcrypt(app)

#PAGOS STRIPE MODO PRUEBA

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_CURRENCY = os.getenv('STRIPE_CURRENCY', 'eur')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')


# SMTP
app.config.update(
    MAIL_FROM = os.getenv('MAIL_FROM'),
    SMTP_HOST = os.getenv('SMTP_HOST'),
    SMTP_PORT = int(os.getenv('SMTP_PORT', '465')),
    SMTP_USER = os.getenv('SMTP_USER'),
    SMTP_PASS = os.getenv('SMTP_PASS'),
)
serializer = URLSafeTimedSerializer(app.secret_key)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'img')
ALLOWED_EXT   = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# Carpeta para adjuntos de contacto
CONTACT_UPLOADS = os.path.join(BASE_DIR, 'static', 'uploads', 'contacto')
os.makedirs(CONTACT_UPLOADS, exist_ok=True)

# <<< NUEVO: carpeta para avatares >>>
AVATARS_DIR = os.path.join(UPLOAD_FOLDER, 'avatars')          # img/avatars
os.makedirs(AVATARS_DIR, exist_ok=True)                       # <<< NUEVO

CATEGORIAS = [
    "Entrantes","Platos combinados","Bocadillos","Tostadas",
    "Bebidas","Postres","Vegano","Sin gluten","Extras","Salsas","General"
]
ALERGENOS = [
    "gluten","lactosa","huevo","frutos secos","marisco",
    "pescado","soja","sesamo","apio","mostaza"
]
RESERVA_DURACION = timedelta(hours=2)

# ───────── Helpers DB y util ─────────
def q(sql, *params, one=False, commit=False):
    """Ejecuta SQL y devuelve dicts (RealDictCursor)."""
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        if commit:
            conn.commit()
            return None
        return cur.fetchone() if one else cur.fetchall()

def allowed_file(fn): 
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT

def _norm_fecha(s):
    """Acepta 'YYYY-MM-DD' o 'DD/MM/YYYY' y devuelve 'YYYY-MM-DD'."""
    if not s: return None
    s = s.strip()
    if "/" in s and len(s) >= 10:
        d, m, y = s[:10].split("/")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return s

def _norm_hora(s):
    """Normaliza 'HH:MM' ó 'HH:MM:SS' a 'HH:MM'."""
    if not s: return None
    return s.strip()[:5]

def _jsonify_rows(rows):
    """Convierte date/datetime/time a string para poder hacer jsonify."""
    safe = []
    for r in rows or []:
        d = dict(r)
        for k, v in d.items():
            if isinstance(v, (datetime, date)):
                d[k] = v.isoformat()
            elif isinstance(v, time):
                d[k] = v.strftime('%H:%M')
        safe.append(d)
    return safe


def _gen_num_pedido(pedido_id: int) -> str:
    # RDS-000123, RDS-000124…
    return f"RDS-{pedido_id:06d}"


# <<< : crea tablas requeridas (favoritos, perfiles y columna avatar y stripe) >>>
def _ensure_profile_and_favs():
    try:
        q("""CREATE TABLE IF NOT EXISTS favoritos(
               usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
               producto_id INTEGER NOT NULL REFERENCES menu(id)     ON DELETE CASCADE,
               creado      TIMESTAMP NOT NULL DEFAULT now(),
               PRIMARY KEY (usuario_id, producto_id)
             );""", commit=True)
    except Exception: pass
    try:
        q("""CREATE TABLE IF NOT EXISTS perfiles_usuario(
               usuario_id INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
               telefono TEXT, direccion TEXT, ciudad TEXT,
               metodo_pago_pref TEXT, pago_alias TEXT, pago_last4 TEXT,
               updated_at TIMESTAMP DEFAULT now()
             );""", commit=True)
    except Exception: pass
    try:
        q("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS avatar VARCHAR(160);", commit=True)
    except Exception: pass

_ensure_profile_and_favs()  


def _ensure_checkout_buffers():
    q("""
    CREATE TABLE IF NOT EXISTS checkout_buffers(
      id SERIAL PRIMARY KEY,
      usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
      carrito_json JSONB NOT NULL,
      total NUMERIC(10,2) NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT NOW(),
      provider_session_id TEXT,
      consumed BOOLEAN NOT NULL DEFAULT FALSE
    );
    """, commit=True)

_ensure_checkout_buffers()




def _ensure_pedido_extras():
    try:
        q("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS metodo_pago VARCHAR(32);", commit=True)
    except Exception:
        pass
    try:
        q("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS prep_estado VARCHAR(32) DEFAULT 'pendiente';", commit=True)
    except Exception:
        pass
    try:
        q("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS email_pagado_enviado BOOLEAN DEFAULT FALSE;", commit=True)
    except Exception:
        pass

_ensure_pedido_extras()



# ===== Email =====
def send_email(to, subject, text_body, html_body=None):
    if not all([app.config.get('MAIL_FROM'), app.config.get('SMTP_HOST'),
                app.config.get('SMTP_PORT'), app.config.get('SMTP_USER'),
                app.config.get('SMTP_PASS')]):
        app.logger.warning("SMTP no configurado. Email no enviado.")
        return
    msg = EmailMessage()
    msg['From'] = app.config['MAIL_FROM']; msg['To'] = to; msg['Subject'] = subject
    msg.set_content(text_body)
    if html_body: msg.add_alternative(html_body, subtype='html')
    host = app.config['SMTP_HOST']; port = int(app.config['SMTP_PORT'])
    user = app.config['SMTP_USER'];  pwd  = app.config['SMTP_PASS']
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
            s.login(user, pwd); s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pwd); s.send_message(msg)

def send_reset_email(to_email, reset_link):
    subject = "Restablece tu contraseña – El Rincón del Soldado"
    text    = f"Has solicitado restablecer tu contraseña. Enlace: {reset_link} (caduca en 1 hora)."
    html    = render_template('emails/password_reset.html', reset_link=reset_link)
    send_email(to_email, subject, text, html)

def send_order_confirmation(to_email, order):
    subject = f"Pedido #{order['id']} confirmado – El Rincón del Soldado"
    text    = f"¡Gracias por tu pedido #{order['id']}! Total: {order['total']:.2f} €"
    html    = render_template('emails/order_confirmation.html', order=order)
    send_email(to_email, subject, text, html)



def _enviar_email_pagado_si_falta(pedido_id: int):
    """Si el pedido está 'pagado' y aún no se envió el email de pagado, lo manda y marca bandera."""
    p = q("""
        SELECT p.id, p.num_pedido, p.total, p.estado, p.items_json,
               COALESCE(u.email,'') AS email,
               COALESCE(p.email_pagado_enviado, FALSE) AS enviado
        FROM pedidos p
        LEFT JOIN usuarios u ON u.id = p.usuario_id
        WHERE p.id=%s
    """, pedido_id, one=True)

    if not p or not p.get('email') or p.get('enviado') or (p.get('estado') or '').lower() != 'pagado':
        return

    items = p.get('items_json') or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []

    try:
        try:
            html = render_template('emails/order_confirmation.html', order={
                'id': p['id'], 'num_pedido': p['num_pedido'],
                'items': items, 'total': p['total'], 'estado': 'pagado'
            })
        except Exception:
            html = None

        send_email(
            p['email'],
            f"Pedido {p['num_pedido']} PAGADO – El Rincón del Soldado",
            f"¡Gracias! Tu pedido {p['num_pedido']} está PAGADO. Total: {float(p['total']):.2f} €",
            html
        )
        q("UPDATE pedidos SET email_pagado_enviado=TRUE WHERE id=%s", pedido_id, commit=True)
    except Exception as e:
        app.logger.exception(e)





def _ensure_stripe_support():
    try:
        q("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS num_pedido VARCHAR(32);", commit=True)
    except Exception: pass
    try:
        q("CREATE UNIQUE INDEX IF NOT EXISTS pedidos_num_pedido_uniq ON pedidos(num_pedido);", commit=True)
    except Exception: pass
    try:
        q("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS stripe_session_id VARCHAR(255);", commit=True)
    except Exception: pass
    try:
        q("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS stripe_payment_intent VARCHAR(255);", commit=True)
    except Exception: pass

_ensure_stripe_support()




# ───────── Páginas básicas ─────────
@app.route('/')
def index():
    if 'usuario_id' not in session:
        flash('Por favor inicia sesión para acceder a todas las funciones','info')
    return render_template('pages/index.html')

@app.route('/quienes-somos')
@app.route('/quienes_somos')
def quienes_somos(): 
    return render_template('pages/quienes_somos.html')

@app.route('/aviso-legal')
@app.route('/aviso_legal')
def aviso_legal(): 
    return render_template('pages/aviso_legal.html')

@app.route('/politica-privacidad')
@app.route('/politica_privacidad')
def politica_privacidad(): 
    return render_template('pages/politica_privacidad.html')

@app.route('/politica-cookies')
@app.route('/politica_cookies')
def politica_cookies(): 
    return render_template('pages/politica_cookies.html')

# ───────── CONTACTO (con adjunto + respuestas) ─────────
@app.route('/contacto', methods=['GET','POST'])
def contacto():
    if request.method == 'POST':
        nombre   = (request.form.get('nombre') or '').strip()
        email    = (request.form.get('email') or '').strip()
        telefono = (request.form.get('telefono') or '').strip()
        asunto   = (request.form.get('asunto') or '').strip()
        mensaje  = (request.form.get('mensaje') or '').strip()

        if not (nombre and (email or telefono) and mensaje):
            flash('Faltan campos obligatorios (nombre, contacto y mensaje).', 'warning')
            return redirect(url_for('contacto'))

        # adjunto opcional (imagen)
        imagen_rel = None
        f = request.files.get('adjunto')
        if f and f.filename:
            if not allowed_file(f.filename):
                flash('Formato de imagen no permitido.', 'warning')
                return redirect(url_for('contacto'))
            fname = secure_filename(f.filename)
            # Evitar colisiones
            base, ext = os.path.splitext(fname)
            i = 1
            dest = os.path.join(CONTACT_UPLOADS, fname)
            while os.path.exists(dest):
                fname = f"{base}_{i}{ext}"
                dest = os.path.join(CONTACT_UPLOADS, fname)
                i += 1
            f.save(dest)
            imagen_rel = f"uploads/contacto/{fname}"  # para url_for('static', filename=imagen_rel)

        q("""INSERT INTO contacto(nombre,email,telefono,asunto,mensaje,imagen,admin_estado)
             VALUES (%s,%s,%s,%s,%s,%s,'pendiente')""",
          nombre, (email or None), (telefono or None), (asunto or None), mensaje, imagen_rel, commit=True)

        flash('¡Mensaje enviado! Te responderemos lo antes posible.', 'success')
        return redirect(url_for('contacto'))

    # === GET: pintar en la página ===
    # 1) Tus mensajes (si el usuario está logueado) con la posible respuesta del admin
    mis_contactos = []
    if session.get('email'):
        mis_contactos = q("""
            SELECT id, asunto, mensaje, admin_respuesta, admin_estado,
                   imagen, COALESCE(created_at, NOW()) AS creado_en
            FROM contacto
            WHERE email=%s
            ORDER BY id DESC
        """, session['email']) or []

    # 2) Bloque público de “Respuestas del administrador” (las que estén publicadas)
    respuestas = q("""
        SELECT id, nombre, asunto, admin_respuesta,
               COALESCE(created_at, NOW()) AS creado_en
        FROM contacto
        WHERE admin_estado = 'respondido'
          AND admin_respuesta IS NOT NULL
          AND admin_respuesta <> ''
        ORDER BY id DESC
        LIMIT 20
    """) or []

    return render_template('pages/contacto.html',
                           mis_contactos=mis_contactos,
                           respuestas=respuestas)



@app.post('/mis-consulta/<int:cid>/eliminar')
def mis_consulta_eliminar(cid):
    """Permite al usuario borrar su propia consulta (solo si fue respondida)."""
    if not session.get('email'):
        flash('Inicia sesión para continuar.', 'warning')
        return redirect(url_for('login'))

    try:
        row = q("SELECT email, COALESCE(admin_estado,'') AS estado FROM contacto WHERE id=%s", cid, one=True)
        if not row:
            flash('Consulta no encontrada.', 'warning')
            return redirect(url_for('contacto'))

        # Solo puede borrar si la consulta es suya y ya está respondida
        mi_email = (session.get('email') or '').strip().lower()
        email_db = (row.get('email') or '').strip().lower()
        if email_db != mi_email:
            flash('No puedes eliminar esta consulta.', 'danger')
            return redirect(url_for('contacto'))

        if row.get('estado') != 'respondido':
            flash('Solo puedes eliminar consultas respondidas.', 'info')
            return redirect(url_for('contacto'))

        q("DELETE FROM contacto WHERE id=%s", cid, commit=True)
        flash('Consulta eliminada.', 'success')
    except Exception as e:
        app.logger.exception(e)
        flash('No se pudo eliminar la consulta.', 'danger')

    return redirect(url_for('contacto'))



# ───────── Carta / menú normal ─────────
@app.route('/menu')
def menu():
    busc=(request.args.get('q') or '').strip()
    cat =(request.args.get('cat') or 'Todas').strip()
    sin = request.args.getlist('sin')
    cats_db=q("SELECT DISTINCT categoria FROM menu ORDER BY categoria") or []
    categorias=[r['categoria'] for r in cats_db]
    sql="SELECT * FROM menu WHERE COALESCE(visible,TRUE)=TRUE"
    p=[]
    if cat.lower()!='todas':
        sql+=" AND categoria=%s"; p.append(cat)
    if busc:
        lk=f"%{busc}%"
        sql+=" AND (nombre ILIKE %s OR descripcion ILIKE %s)"; p+= [lk,lk]
    if sin:
        sql+=" AND (alergenos IS NULL OR alergenos='{}' OR NOT (alergenos && %s::text[]))"
        p.append(sin)
    sql+=" ORDER BY categoria,nombre"
    productos=q(sql,*p) or []

    # <<< NUEVO: ids de favoritos del usuario para pintar el corazón >>>
    fav_ids = set()
    if session.get('usuario_id'):
        try:
            filas = q("SELECT producto_id FROM favoritos WHERE usuario_id=%s",
                      session['usuario_id']) or []
            fav_ids = { (r['producto_id'] if isinstance(r,dict) else r[0]) for r in filas }
        except Exception:
            fav_ids = set()

    return render_template('pages/menu.html',
        productos=productos,categorias=categorias,
        q=busc,cat=cat,alergenos=ALERGENOS,sin_sel=sin,
        fav_ids=fav_ids)   # <<< NUEVO

# ---------------------- HELPERS Diseña tu menú (no tocados) -------------------------
def _row_to_dict(row, cols=('id','nombre','imagen')):
    if not row: return None
    if isinstance(row, dict): return {k: row.get(k) for k in cols}
    out={}
    for i,k in enumerate(cols):
        try: out[k]=row[i]
        except: out[k]=None
    return out

def get_producto_info(valor):
    if valor in (None, '', 'None'): return None
    row=None
    try:
        vid=int(valor)
        row=q("SELECT id, nombre, imagen FROM menu WHERE id=%s", vid, one=True)
    except (ValueError, TypeError):
        pass
    if row is None:
        row=q("SELECT id, nombre, imagen FROM menu WHERE nombre=%s", valor, one=True)
    if not row: return None
    if isinstance(row, dict):
        return {'id': row.get('id'), 'nombre': row.get('nombre'), 'imagen': row.get('imagen')}
    return {'id': row[0], 'nombre': row[1], 'imagen': row[2] if len(row) > 2 else None}

# ===== Helpers etiquetas/categorías y slots para combos =====
def _slot_for_categoria(cat, c_row):
    """Dada la categoría del producto y la fila del combo (con *_cats),
    devuelve 'entrante'|'principal'|'bebida'|'postre' o None si no encaja."""
    if not cat or not c_row: 
        return None
    c = (cat or '').strip().lower()

    def _norm(arr):
        if not arr: return []
        # postgres text[] -> list[str]
        return [str(x).strip().lower() for x in arr]

    if c in _norm(c_row.get('entrante_cats')):  return 'entrante'
    if c in _norm(c_row.get('principal_cats')): return 'principal'
    if c in _norm(c_row.get('bebida_cats')):    return 'bebida'
    if c in _norm(c_row.get('postre_cats')):    return 'postre'
    return None

# ---------------------- DISEÑA TU MENÚ (no tocado) -------------------------
@app.route('/disena-menu', methods=['GET', 'POST'])
def disena_menu():
    import psycopg2.extras
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 1) Combos disponibles
    cur.execute("""
        SELECT id, nombre,
               COALESCE(precio_base,0) AS precio,   -- alias a 'precio'
               entrante_cats, principal_cats, bebida_cats, postre_cats
        FROM combos
        ORDER BY id
    """)
    combos = [dict(r) for r in cur.fetchall()]

    if not combos:
        flash('No hay menús configurados.', 'warning')
        return render_template("pages/disena_menu.html",
                               menus=[], menu_id=None, menu_precio=0,
                               entrantes=[], principales=[], bebidas=[], postres=[], extras=[])

    # 2) Decidir qué combo está seleccionado
    if request.method == "POST":
        # ← clave: respetar el 'formato' que llega del formulario
        menu_id = request.form.get('formato', type=int) or combos[0]['id']
    else:
        menu_id = request.args.get('formato', type=int) or combos[0]['id']

    # helper local para (re)cargar catálogos de un combo concreto
    def load_combo_data(cid):
        sel = next((c for c in combos if c['id'] == cid), combos[0])

        # asignados explícitos por slot
        cur.execute("SELECT producto_id, slot FROM combo_items WHERE combo_id=%s", (sel['id'],))
        asignados = cur.fetchall()
        ids_entr = [r['producto_id'] for r in asignados if r['slot']=='entrante']
        ids_prin = [r['producto_id'] for r in asignados if r['slot']=='principal']
        ids_beb  = [r['producto_id'] for r in asignados if r['slot']=='bebida']
        ids_post = [r['producto_id'] for r in asignados if r['slot']=='postre']

        def _fetch_ids(ids):
            if not ids: return []
            qmarks = ','.join(['%s']*len(ids))
            cur.execute(f"""SELECT id, nombre, categoria, imagen 
                            FROM menu WHERE id IN ({qmarks}) AND COALESCE(visible,TRUE)=TRUE
                            ORDER BY nombre""", ids)
            return [dict(r) for r in cur.fetchall()]

        def _fetch_cats(cats):
            if not cats: return []
            cur.execute("""SELECT id, nombre, categoria, imagen 
                           FROM menu 
                           WHERE COALESCE(visible,TRUE)=TRUE AND categoria = ANY(%s)
                           ORDER BY nombre""", (cats,))
            return [dict(r) for r in cur.fetchall()]

        entrantes   = _fetch_ids(ids_entr) if ids_entr else _fetch_cats(sel['entrante_cats'])
        principales = _fetch_ids(ids_prin) if ids_prin else _fetch_cats(sel['principal_cats'])
        bebidas     = _fetch_ids(ids_beb ) if ids_beb  else _fetch_cats(sel['bebida_cats'])
        postres     = _fetch_ids(ids_post) if ids_post else _fetch_cats(sel['postre_cats'])

        return sel, entrantes, principales, bebidas, postres

    # cargar datos del combo seleccionado (por GET o por POST)
    sel, entrantes, principales, bebidas, postres = load_combo_data(menu_id)

    # extras (comunes)
    cur.execute("SELECT id, nombre, COALESCE(precio,0) AS precio FROM extras_menu ORDER BY nombre")
    extras = [dict(r) for r in cur.fetchall()]
    extras_by_id = {e['id']: e for e in extras}

    # 3) POST: añadir al carrito usando el combo realmente seleccionado
    if request.method == "POST":
        # (ya NO hacemos redirect por cambio de combo)
        def pick(lista, pid):
            try:
                pid = int(pid)
            except:
                return None
            return next((x for x in lista if x['id'] == pid), None)

        e  = pick(entrantes,   request.form.get('entrante'))
        p  = pick(principales, request.form.get('principal'))
        b  = pick(bebidas,     request.form.get('bebida'))
        po = pick(postres,     request.form.get('postre'))

        if not all([e, p, b, po]):
            flash("Completa todas las selecciones del menú.", "warning")
            return redirect(url_for('disena_menu', formato=sel['id']))

        # extras validados
        extras_ids = request.form.getlist('extras')
        extras_ok, extras_total = [], 0.0
        for s in extras_ids:
            try:
                sid = int(s)
            except:
                continue
            ex = extras_by_id.get(sid)
            if ex:
                precio = float(ex['precio'] or 0)
                extras_ok.append({'id': sid, 'nombre': ex['nombre'], 'precio': precio})
                extras_total += precio

        precio_base = float(sel['precio'])

        # imagen preferente
        principal_img = (p.get('imagen') or e.get('imagen') or b.get('imagen') or po.get('imagen')
                         or 'img/menu_personalizado.webp')

        # item compatible con tu checkout
        item = {
            'nombre'      : f"{sel['nombre']} (personalizado)",
            'precio'      : precio_base,
            'imagen'      : principal_img,
            'precio_menu' : precio_base,
            'menu_nombre' : f"{sel['nombre']} (personalizado)",
            'extras'      : extras_ok,
            'detalle'     : {
                'entrante' :  {'id': e['id'],  'nombre': e['nombre']},
                'principal':  {'id': p['id'],  'nombre': p['nombre']},
                'bebida'   :  {'id': b['id'],  'nombre': b['nombre']},
                'postre'   :  {'id': po['id'], 'nombre': po['nombre']},
            }
        }

        carrito = session.get('carrito', [])
        carrito.append(item)
        session['carrito'] = carrito
        session.modified = True

        flash('Menú personalizado añadido al carrito.', 'success')
        return redirect(url_for('checkout'))

    # 4) GET: render
    return render_template(
        "pages/disena_menu.html",
        menus=combos,
        menu_id=sel['id'],
        menu_precio=sel['precio'],
        entrantes=entrantes, principales=principales,
        bebidas=bebidas, postres=postres,
        extras=extras
    )




# ───────── HELPERS PAGO ─────────

def _guardar_pedido_desde_items(usuario_id, items_json, lineas, total,
                                estado='pendiente', enviar_email=True,
                                metodo_pago=None):
    """
    Inserta pedido con id + num_pedido generados antes (evita NOT NULL),
    inserta líneas (si se pasan) y manda email si procede.
    """
    import psycopg2.extras
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Reservar ID y generar Nº pedido
        cur.execute("SELECT pg_get_serial_sequence('pedidos','id') AS seq")
        seq = (cur.fetchone() or {}).get('seq') or 'pedidos_id_seq'
        cur.execute("SELECT nextval(%s) AS nid", (seq,))
        pedido_id = int((cur.fetchone() or {}).get('nid'))
        num_pedido = _gen_num_pedido(pedido_id)

        # Insertar pedido (con metodo_pago y prep_estado inicial)
        cur.execute("""
            INSERT INTO pedidos
              (id, usuario_id, fecha, total, estado, creado_en, items_json,
               num_pedido, metodo_pago, prep_estado)
            OVERRIDING SYSTEM VALUE
            VALUES
              (%s, %s, now(), %s, %s, now(), %s,
               %s, %s, 'pendiente')
        """, (pedido_id, usuario_id, total, estado,
              psycopg2.extras.Json(items_json), num_pedido, metodo_pago))

        # Insertar líneas si nos las pasan ya
        if lineas:
            for ln in lineas:
                cur.execute("""
                    INSERT INTO lineas_pedido (pedido_id, plato_id, cantidad, precio)
                    VALUES (%s, %s, %s, %s)
                """, (pedido_id, ln.get('plato_id'), ln.get('cantidad', 1), ln.get('precio', 0.0)))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Email de recibido (texto distinto si es "pagar al recoger")
    if enviar_email and usuario_id:
        try:
            u = q("SELECT email FROM usuarios WHERE id=%s", usuario_id, one=True)
            if u and u.get('email'):
                html = None
                try:
                    html = render_template('emails/order_confirmation.html', order={
                        'id': pedido_id, 'num_pedido': num_pedido,
                        'items': items_json, 'total': total, 'estado': estado,
                        'metodo_pago': metodo_pago
                    })
                except Exception:
                    pass

                if metodo_pago == 'recoger':
                    asunto = f"Pedido {num_pedido} recibido – PAGA AL RECOGER"
                    texto  = (f"Gracias por tu pedido {num_pedido}. Total: {total:.2f} €.\n"
                              f"Método de pago: **pagar al recoger en el local**.")
                else:
                    asunto = f"Pedido {num_pedido} recibido – El Rincón del Soldado"
                    texto  = f"Gracias por tu pedido {num_pedido}. Total: {total:.2f} €."

                send_email(u['email'], asunto, texto, html)
        except Exception as e:
            app.logger.exception(e)

    return pedido_id, num_pedido



def _calcular_totales_y_items(carrito):
    total = 0.0
    items_json = []
    lineas = []
    for it in carrito:
        base = it.get('precio_menu') if (it.get('precio_menu') is not None) else it.get('precio', 0)
        try: base = float(base)
        except Exception: base = 0.0
        extras = it.get('extras', []) or []
        extras_total = sum(float(e.get('precio', 0) or 0) for e in extras)
        subtotal = base + extras_total
        total += subtotal

        nombre = it.get('nombre') or it.get('menu_nombre') or 'Producto'
        items_json.append({
            'nombre'   : nombre,
            'cantidad' : 1,
            'precio'   : base,
            'subtotal' : subtotal,
            'extras'   : [{'nombre': e.get('nombre',''), 'precio': float(e.get('precio',0) or 0)} for e in extras]
        })

        # plato_id (si existe en tu menú)
        info = get_producto_info(nombre)
        plato_id = None
        if info and info.get('id'):
            try: plato_id = int(info['id'])
            except Exception: plato_id = None

        lineas.append({'plato_id': plato_id, 'cantidad': 1, 'precio': subtotal})
    return total, items_json, lineas





# ───────── Carrito / checkout (no tocado) ─────────
@app.route('/agregar_al_carrito', methods=['POST'])
def agregar_al_carrito():
    try:
        nombre = request.form['nombre']
        precio = float(request.form['precio'])
        imagen = request.form.get('imagen', '')
        if not imagen:
            info = get_producto_info(nombre)
            if info and info.get('imagen'):
                imagen = info['imagen']
        extras = request.form.getlist('extras[]')
        extras_precio = request.form.getlist('extras_precio[]')
        lista_extras = []
        for i in range(len(extras)):
            try:
                lista_extras.append({'nombre': extras[i], 'precio': float(extras_precio[i])})
            except Exception:
                continue
        item = {'nombre': nombre, 'precio': precio, 'imagen': imagen, 'extras': lista_extras}
        carrito = session.get('carrito', []); carrito.append(item); session['carrito'] = carrito
        flash('Producto añadido al carrito', 'success'); return redirect(url_for('menu'))
    except Exception as e:
        flash(f'Error al añadir al carrito: {str(e)}', 'danger'); return redirect(url_for('menu'))

@app.route('/checkout')
def checkout():
    carrito = session.get('carrito', [])
    total = 0.0
    for pedido in carrito:
        base = pedido.get('precio_menu') if pedido.get('precio_menu') is not None else pedido.get('precio', 0)
        try: base = float(base)
        except: base = 0.0
        extras_total = sum(float(e.get('precio',0) or 0) for e in pedido.get('extras', []))
        total += base + extras_total
    return render_template('pages/checkout.html', carrito=carrito, total=total)

@app.route('/vaciar-carrito')
def vaciar_carrito():
    session['carrito'] = []; session.modified = True
    return redirect(url_for('checkout'))



@app.post('/pago/stripe')
def pago_stripe():
    carrito = session.get('carrito') or []
    if not carrito:
        flash('El carrito está vacío', 'warning')
        return redirect(url_for('checkout'))

    total, items_json, _lineas = _calcular_totales_y_items(carrito)

    # Generar line_items para Stripe
    line_items = []
    for it in items_json:
        nombre = it.get('nombre') or 'Producto'
        if it.get('extras'):
            extras_n = ", ".join(e.get('nombre','') for e in it['extras'] if e.get('nombre'))
            if extras_n:
                nombre = f"{nombre} (extras: {extras_n})"
        amount_cents = max(0, int(round(float(it.get('subtotal',0))*100)))
        if amount_cents > 0:
            line_items.append({
                'price_data': {
                    'currency': STRIPE_CURRENCY,
                    'unit_amount': amount_cents,
                    'product_data': {'name': nombre[:120]},
                },
                'quantity': 1
            })

    if not line_items:
        flash('No hay líneas válidas para pagar.', 'warning')
        return redirect(url_for('checkout'))

    try:
        # Guardamos pedido como PENDIENTE, sin líneas; el email y cierre lo hará el webhook / pago_exito
        pedido_id, num_pedido = _guardar_pedido_desde_items(
            usuario_id=session.get('usuario_id'),
            items_json=items_json,
            lineas=None,
            total=total,
            estado='pendiente',
            enviar_email=False,
            metodo_pago='stripe'
        )

        # IMPORTANTE: url_for ya genera ?pid=..., así que añadimos session_id con '&'
        base_success = url_for('pago_exito', pid=pedido_id, _external=True)
        success_url  = f"{base_success}&session_id={{CHECKOUT_SESSION_ID}}"

        checkout_session = stripe.checkout.Session.create(
            mode='payment',
            line_items=line_items,
            success_url=success_url,
            cancel_url=url_for('checkout', _external=True),
            metadata={
                'pedido_id': str(pedido_id),
                'num_pedido': num_pedido,
                'usuario_id': str(session.get('usuario_id') or '')
            }
        )

        q("UPDATE pedidos SET stripe_session_id=%s WHERE id=%s",
          checkout_session.id, pedido_id, commit=True)

        return redirect(checkout_session.url, code=303)

    except Exception as e:
        app.logger.exception(e)
        flash('No se pudo iniciar el pago. Revisa STRIPE_* en .env.', 'danger')
        return redirect(url_for('checkout'))




@app.get('/pago/exito')
def pago_exito():
    """Página de gracias tras Stripe Checkout. Limpia carrito y, si la sesión ya está 'paid', marca pagado/intent y dispara email."""
    pid = request.args.get('pid', type=int)
    session_id = request.args.get('session_id')

    if session_id:
        try:
            s = stripe.checkout.Session.retrieve(session_id)
            if s and s.get('payment_status') == 'paid':
                q("""UPDATE pedidos
                       SET estado='pagado',
                           prep_estado='preparando',
                           stripe_payment_intent=%s
                     WHERE stripe_session_id=%s
                       AND estado<>'pagado'""",
                  s.get('payment_intent'), session_id, commit=True)

                if pid:
                    try:
                        _enviar_email_pagado_si_falta(pid)
                    except Exception as e:
                        app.logger.warning(e)
        except Exception as e:
            app.logger.warning(f"No se pudo verificar la sesión Stripe en /pago/exito: {e}")

    session.pop('carrito', None)

    if pid:
        p = q("SELECT num_pedido, estado FROM pedidos WHERE id=%s", pid, one=True)
        if p:
            if (p['estado'] or '').lower() == 'pagado':
                flash(f"¡Pago del pedido {p['num_pedido']} confirmado!", 'success')
            else:
                flash(f"Pedido {p['num_pedido']} registrado. Estado: {p['estado']} (se actualizará).", 'info')
        else:
            flash('¡Pago realizado! Revisa tu perfil para ver el estado.', 'success')
    else:
        flash('¡Pago realizado! Revisa tu perfil para ver el estado.', 'success')

    return redirect(url_for('perfil'))




@app.post('/stripe/webhook')
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        app.logger.warning(f"Webhook Stripe inválido: {e}")
        return ("", 400)

    etype = event.get('type')
    data  = event.get('data', {}).get('object', {})

    if etype == 'checkout.session.completed':
        session_id = data.get('id')
        intent_id  = data.get('payment_intent')
        meta       = data.get('metadata') or {}
        pedido_id  = meta.get('pedido_id')

        try:
            pid = int(pedido_id) if pedido_id is not None else None
        except Exception:
            pid = None

        if pid:
            # 1) Marcar pagado + preparando (si aún no lo estaba)
            try:
                q("""UPDATE pedidos
                       SET estado='pagado',
                           prep_estado='preparando',
                           stripe_payment_intent=%s
                     WHERE id=%s AND estado<>'pagado'""",
                  intent_id, pid, commit=True)
            except Exception as e:
                app.logger.exception(e)  # seguimos; no devolvemos 500

            # 2) Crear líneas si no existen (desde items_json)
            try:
                row = q("SELECT items_json FROM pedidos WHERE id=%s", pid, one=True) or {}
                items = row.get('items_json') or []
                if isinstance(items, str):
                    try:
                        items = json.loads(items)
                    except Exception:
                        items = []

                ya = q("SELECT 1 FROM lineas_pedido WHERE pedido_id=%s LIMIT 1", pid, one=True)
                if not ya:
                    for it in items:
                        nombre = it.get('nombre') or it.get('menu_nombre') or 'Producto'
                        info = get_producto_info(nombre)
                        plato_id = None
                        if info and info.get('id'):
                            try:
                                plato_id = int(info['id'])
                            except Exception:
                                plato_id = None
                        precio = float(it.get('subtotal') or it.get('precio') or 0)
                        q("""INSERT INTO lineas_pedido (pedido_id, plato_id, cantidad, precio)
                             VALUES (%s,%s,%s,%s)""",
                          pid, plato_id, 1, precio, commit=True)
            except Exception as e:
                app.logger.exception(e)  # no romper el webhook

            # 3) Enviar correo de "PAGADO" (si falta) sin romper el webhook
            try:
                _enviar_email_pagado_si_falta(pid)
            except Exception as e:
                app.logger.exception(e)  # pero no devolvemos 500

    return ("", 200)




@app.post('/pagar-en-local')
def pagar_en_local():
    if not session.get('usuario_id'):
        flash('Inicia sesión para continuar', 'warning'); return redirect(url_for('login'))
    carrito = session.get('carrito') or []
    if not carrito:
        flash('El carrito está vacío', 'warning'); return redirect(url_for('checkout'))

    try:
        total, items_json, lineas = _calcular_totales_y_items(carrito)
        pedido_id, num_pedido = _guardar_pedido_desde_items(
            session['usuario_id'], items_json, lineas, total,
            estado='pendiente', enviar_email=True, metodo_pago='recoger'
        )
    except Exception as e:
        app.logger.exception(e)
        flash('No se pudo guardar el pedido. Inténtalo de nuevo.', 'danger')
        return redirect(url_for('checkout'))

    session.pop('carrito', None)
    flash(f'¡Pedido {num_pedido} registrado! Paga al recoger.', 'success')
    return redirect(url_for('perfil'))


# ─── Admin: PEDIDOS ───

@app.post('/admin/pedido/<int:oid>/estado')
def admin_pedido_estado(oid):
    if not admin_required():
        return redirect(url_for('index'))

    nuevo = (request.form.get('estado') or 'pendiente').strip().lower()

    try:
        if nuevo in ('preparando', 'en_preparacion'):
            # No toques estado; cambia SOLO el estado de preparación
            q("UPDATE pedidos SET prep_estado='preparando' WHERE id=%s", oid, commit=True)
            flash('Pedido marcado como PREPARANDO.', 'success')

        elif nuevo == 'completado':
            # cerrar pedido
            q("UPDATE pedidos SET estado='completado', prep_estado='listo' WHERE id=%s", oid, commit=True)
            # email al usuario (opcional) – deja tu lógica si ya la tienes
            p = q("""
                SELECT p.num_pedido, p.total, COALESCE(u.email,'') AS usuario_email, p.items_json
                FROM pedidos p LEFT JOIN usuarios u ON u.id = p.usuario_id
                WHERE p.id=%s
            """, oid, one=True)
            if p and p.get('usuario_email'):
                try:
                    try:
                        html = render_template('emails/order_completed.html', order=p)
                    except Exception:
                        html = None
                    asunto = f"Tu pedido {p['num_pedido']} está COMPLETADO – El Rincón del Soldado"
                    texto  = f"¡Listo! Tu pedido {p['num_pedido']} ha sido completado. Total: {float(p['total']):.2f} €"
                    send_email(p['usuario_email'], asunto, texto, html)
                except Exception as e:
                    app.logger.exception(e)
            flash('Pedido COMPLETADO.', 'success')

        elif nuevo == 'cancelado':
            q("UPDATE pedidos SET estado='cancelado' WHERE id=%s", oid, commit=True)
            flash('Pedido CANCELADO.', 'info')

        else:
            # fallback: cambiar estado tal cual
            q("UPDATE pedidos SET estado=%s WHERE id=%s", nuevo, oid, commit=True)
            flash('Estado actualizado.', 'success')

    except Exception as e:
        app.logger.exception(e)
        flash('No se pudo actualizar el estado.', 'danger')

    return redirect(url_for('admin_index', seccion='pedidos'))

@app.post('/admin/pedido/<int:oid>/eliminar')
def admin_pedido_eliminar(oid):
    if not admin_required(): return redirect(url_for('index'))
    q("DELETE FROM lineas_pedido WHERE pedido_id=%s", oid, commit=True)
    q("DELETE FROM pedidos WHERE id=%s", oid, commit=True)
    flash('Pedido eliminado.', 'info')
    return redirect(url_for('admin_index', seccion='pedidos'))


@app.post('/mis-pedidos/<int:oid>/eliminar')
def mis_pedidos_eliminar(oid):
    if not session.get('usuario_id'):
        return redirect(url_for('login'))
    q("DELETE FROM lineas_pedido WHERE pedido_id=%s", oid, commit=True)
    q("DELETE FROM pedidos WHERE id=%s AND usuario_id=%s",
      oid, session['usuario_id'], commit=True)
    flash('Pedido eliminado.', 'info')
    return redirect(url_for('perfil'))


# ───────── Registro / login (añadido avatar a la sesión) ─────────
@app.route('/registro',methods=['GET','POST'])
def registro():
    if request.method=='POST':
        n,e = request.form['nombre'], request.form['email']
        p   = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        q("INSERT INTO usuarios(nombre,email,password,rol,activo) VALUES(%s,%s,%s,%s,%s)",
           n,e,p,'cliente',True,commit=True)
        flash('Registro correcto. Inicia sesión.','success')
        return redirect(url_for('login'))
    return render_template('pages/registro.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    err = None
    if request.method == 'POST':
        e = request.form['email']
        u = q("SELECT * FROM usuarios WHERE email=%s", e, one=True)
        if not u or not bcrypt.check_password_hash(u['password'], request.form['password']):
            err = "Usuario o contraseña incorrectos"
        elif not u.get('activo', True):
            err = "Cuenta desactivada"
        else:
            avatar = u.get('avatar')
            if avatar and not avatar.startswith('img/'):
                avatar = f"img/avatars/{avatar}"
            session.update(
                usuario_id=u['id'],
                nombre=u['nombre'],
                rol=u.get('rol', 'cliente'),
                email=u['email'],
                avatar=avatar or 'img/avatars/default.webp'
            )
            return redirect(url_for('index'))  # <-- solo si todo fue bien

    return render_template('pages/login.html', error=err)

@app.route('/logout')
def logout(): 
    session.clear(); 
    return redirect(url_for('index'))

# ===== Forgot/Reset (no tocado) =====
@app.route('/forgot', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        if not email:
            flash('Introduce tu correo.', 'warning')
            return redirect(url_for('forgot_password'))
        u = q("SELECT id,email,nombre FROM usuarios WHERE LOWER(email)=%s", email, one=True)
        if u:
            token = serializer.dumps(email, salt='password-reset')
            link  = url_for('reset_password', token=token, _external=True)
            try: send_reset_email(email, link)
            except Exception as e: app.logger.exception(e)
        flash('Si el correo existe, te hemos enviado un enlace para restablecer la contraseña.', 'success')
        return redirect(url_for('login'))
    return render_template('pages/forgot.html')

@app.route('/reset/<token>', methods=['GET','POST'])
def reset_password(token):
    from itsdangerous import BadSignature, SignatureExpired
    try:
        email = serializer.loads(token, salt='password-reset', max_age=3600)  # 1 hora
    except (BadSignature, SignatureExpired):
        flash('El enlace ha caducado o no es válido.', 'danger')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        p1 = request.form.get('password') or ''
        p2 = request.form.get('password2') or ''
        if len(p1) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'warning'); return redirect(request.url)
        if p1 != p2:
            flash('Las contraseñas no coinciden.', 'warning'); return redirect(request.url)
        new_hash = bcrypt.generate_password_hash(p1).decode('utf-8')
        q("UPDATE usuarios SET password=%s WHERE LOWER(email)=%s", new_hash, email, commit=True)
        flash('¡Contraseña actualizada! Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('pages/reset.html', email=email)

# ───────── Admin: permiso ─────────
def admin_required():
    if session.get('rol')!='admin':
        flash('Acceso restringido','warning'); return False
    return True

# === DISPONIBILIDAD DE MESAS (helper + API) ===
def mesas_disponibilidad(fecha, hora, personas=None):
    disponibles = q("""
        SELECT m.id, m.nombre, m.capacidad
        FROM mesas m
        WHERE m.activo = TRUE
          AND (%s IS NULL OR m.capacidad >= %s)
          AND NOT EXISTS (
            SELECT 1 FROM reservas r
            WHERE r.mesa_id = m.id
              AND r.fecha = %s::date
              AND r.estado IN ('pendiente','confirmada')
              AND r.hora BETWEEN (%s::time - interval '2 hour')
                             AND (%s::time + interval '2 hour')
          )
        ORDER BY m.capacidad, m.nombre
    """, personas, personas, fecha, hora, hora)

    ocupadas = q("""
        SELECT m.id, m.nombre, m.capacidad,
               r.id AS reserva_id, r.nombre AS cliente, r.hora, r.personas, r.estado
        FROM mesas m
        JOIN reservas r ON r.mesa_id = m.id
        WHERE m.activo = TRUE
          AND r.fecha = %s::date
          AND r.estado IN ('pendiente','confirmada')
          AND r.hora BETWEEN (%s::time - interval '2 hour')
                         AND (%s::time + interval '2 hour')
        ORDER BY m.capacidad, m.nombre
    """, fecha, hora, hora)
    return disponibles, ocupadas

@app.get('/api/mesas/disponibilidad')
def api_mesas_disponibilidad():
    fecha = _norm_fecha(request.args.get('fecha'))
    hora  = _norm_hora(request.args.get('hora'))
    personas = request.args.get('personas', type=int)
    if not fecha or not hora:
        return jsonify(error="Parámetros requeridos: fecha, hora"), 400
    av, oc = mesas_disponibilidad(fecha, hora, personas)
    return jsonify(available=_jsonify_rows(av), occupied=_jsonify_rows(oc))

# ───────── Panel Admin (incluye reservas + contacto) ─────────
@app.route('/admin')
def admin_index():
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning'); return redirect(url_for('index'))

    seccion = request.args.get('seccion', 'carta')
    ctx = {'seccion': seccion}

    if seccion == 'carta':
        qtxt = (request.args.get('q') or '').strip()
        cat  = (request.args.get('cat') or 'Todas').strip()
        cats_db = q("SELECT DISTINCT categoria FROM menu ORDER BY categoria") or []
        categorias = [r['categoria'] for r in cats_db]
        ctx.update(categorias=categorias, qtxt=qtxt, cat=cat)
        sql = """SELECT id,categoria,nombre,descripcion,precio,imagen,
                        COALESCE(agotado,FALSE) AS agotado,
                        COALESCE(visible,TRUE)  AS visible
                 FROM menu WHERE 1=1"""
        params=[]
        if cat and cat.lower()!='todas':
            sql+=" AND categoria=%s"; params.append(cat)
        if qtxt:
            like=f"%{qtxt}%"
            sql+=" AND (nombre ILIKE %s OR descripcion ILIKE %s)"; params+=[like,like]
        sql+=" ORDER BY categoria, nombre"
        ctx['items']=q(sql,*params)

    elif seccion == 'usuarios':
        ctx['usuarios'] = q("SELECT id,nombre,email,rol,COALESCE(activo,TRUE) activo FROM usuarios ORDER BY id DESC")

    elif seccion == 'reservas':
        ctx['reservas'] = q("""
            SELECT r.id, r.nombre, r.email, r.telefono,
                   r.fecha, r.hora, r.personas, r.tipo,
                   r.comentarios, r.estado,
                   r.mesa_id, m.nombre AS mesa_nombre, m.capacidad AS mesa_capacidad
            FROM reservas r
            LEFT JOIN mesas m ON m.id = r.mesa_id
            ORDER BY r.fecha DESC, r.hora DESC, r.id DESC
        """)

    elif seccion == 'pedidos':
        rows = q("""
            SELECT p.id, p.num_pedido, p.fecha, p.total, p.estado,
                    COALESCE(p.metodo_pago,'') AS metodo_pago,
                    COALESCE(p.prep_estado,'pendiente') AS prep_estado,
                    COALESCE(p.items_json, '[]'::jsonb) AS items_json,
                    p.stripe_session_id, p.stripe_payment_intent,
                    u.nombre AS usuario_nombre, u.email AS usuario_email
            FROM pedidos p
            LEFT JOIN usuarios u ON u.id = p.usuario_id
            ORDER BY p.fecha DESC, p.id DESC
        """) or []

        for r in rows:
            est  = (r.get('estado') or '').lower()
            met  = (r.get('metodo_pago') or '').lower()
            prep = (r.get('prep_estado') or 'pendiente').lower()
            if est == 'pagado' and met == 'stripe':
                r['estado_label'] = f"Pagado (Stripe) · {'preparando' if prep in ('preparando','en_preparacion') else 'pendiente de preparación'}"
            elif est == 'pagado':
                r['estado_label'] = f"Pagado · {'preparando' if prep in ('preparando','en_preparacion') else 'pendiente de preparación'}"
            elif est == 'pendiente' and met == 'recoger':
                r['estado_label'] = "Pendiente · pagar al recoger"
            elif est == 'pendiente' and met == 'stripe':
                r['estado_label'] = "Pendiente de pago (Stripe)"
            elif est == 'completado':
                r['estado_label'] = "Completado"
            elif est == 'cancelado':
                r['estado_label'] = "Cancelado"
            else:
                r['estado_label'] = est.capitalize() if est else "—"

        ctx['pedidos'] = rows




    elif seccion == 'contacto':
        ctx['contactos'] = q("""
            SELECT id, nombre, email, telefono, asunto, mensaje, imagen,
                    admin_respuesta, admin_estado,
                    COALESCE(created_at, NOW()) AS creado_en
            FROM contacto
            ORDER BY id DESC
        """)

        
    elif seccion == 'disena':
        # combos disponibles y seleccionado
        combos = q("""SELECT id, nombre, COALESCE(precio_base,0) AS precio_base,
                         entrante_cats, principal_cats, bebida_cats, postre_cats
                  FROM combos ORDER BY id""") or []
        sel_id = request.args.get('combo_id', type=int) or (combos[0]['id'] if combos else None)
        sel    = next((c for c in combos if c['id']==sel_id), None)

        # productos asignados explícitamente a este combo (ids)
        asignados = set(x[0] if not isinstance(x, dict) else x['producto_id']
                    for x in (q("SELECT producto_id FROM combo_items WHERE combo_id=%s", sel_id) or []))

        # filtros
        d_q   = (request.args.get('q') or '').strip()
        d_cat = (request.args.get('cat') or 'Todas').strip()
        solo  = request.args.get('solo', '0')  # 0=ver todos; 1=solo asignados

        cats_db = q("SELECT DISTINCT categoria FROM menu ORDER BY categoria") or []
        d_categorias = [r['categoria'] for r in cats_db]

        where = ["1=1"]; params=[]
        if d_cat.lower()!='todas':
            where.append("categoria=%s"); params.append(d_cat)
        if d_q:
            like=f"%{d_q}%"; where.append("(nombre ILIKE %s OR descripcion ILIKE %s)"); params+=[like,like]

        sql = f"""SELECT id,categoria,nombre,descripcion,precio,imagen,
                     COALESCE(agotado,FALSE) AS agotado,
                     COALESCE(visible,TRUE)  AS visible
              FROM menu
              WHERE {' AND '.join(where)}
              ORDER BY categoria,nombre"""
        items = q(sql, *params) or []

        # marcar si está asignado y si encaja en algún slot del combo
        for it in items:
            it['en_combo']  = it['id'] in asignados
            it['slot_ok']   = _slot_for_categoria(it['categoria'], sel) if sel else None

        if solo=='1':
            items = [it for it in items if it['en_combo']]

        ctx.update({
            'd_combos': combos, 'd_sel': sel, 'd_sel_id': sel_id,
            'd_q': d_q, 'd_cat': d_cat, 'd_solo': solo, 'd_categorias': d_categorias,
            'd_items': items
    })

    return render_template('admin/admin_panel.html', **ctx)

# ====== MODAL: listar productos de un combo ======
@app.get('/admin/combos/<int:cid>/productos')
def admin_combo_productos(cid):
    if session.get('rol') != 'admin':
        return "Acceso restringido", 403

    # Filtros (opcionales) desde la query del modal
    d_q   = (request.args.get('q') or '').strip()
    d_cat = (request.args.get('cat') or 'Todas').strip()
    solo  = (request.args.get('solo') or '0').strip()  # 1 = solo asignados

    combo = q("""SELECT id,nombre,COALESCE(precio_base,0) AS precio_base
                 FROM combos WHERE id=%s""", cid, one=True)
    if not combo:
        return "Menú no encontrado", 404

    # asignados (pid -> slot)
    asignados_rows = q("SELECT producto_id,slot FROM combo_items WHERE combo_id=%s", cid) or []
    asignados = { (r['producto_id'] if isinstance(r,dict) else r[0]) :
                   (r['slot']        if isinstance(r,dict) else r[1])
                 for r in asignados_rows }

    # categorías disponibles para filtro
    cats_db = q("SELECT DISTINCT categoria FROM menu ORDER BY categoria") or []
    d_categorias = [r['categoria'] for r in cats_db]

    # construir consulta de productos
    where = ["1=1"]; params=[]
    if d_cat.lower()!='todas':
        where.append("categoria=%s"); params.append(d_cat)
    if d_q:
        like=f"%{d_q}%"; where.append("(nombre ILIKE %s OR descripcion ILIKE %s)"); params += [like,like]
    sql = f"""SELECT id,categoria,nombre,descripcion,COALESCE(precio,0) AS precio,
                     imagen,COALESCE(agotado,FALSE) agotado,COALESCE(visible,TRUE) visible
              FROM menu
              WHERE {' AND '.join(where)}
              ORDER BY categoria,nombre"""
    items = q(sql, *params) or []
    if solo=='1':
        items = [it for it in items if (it['id'] if isinstance(it,dict) else it[0]) in asignados]

    return render_template('admin/_combo_productos.html',
                           combo=combo, items=items, asignados=asignados,
                           d_q=d_q, d_cat=d_cat, d_categorias=d_categorias, solo=solo)

# ====== Asignar / actualizar slot de un producto en combo ======
@app.post('/admin/combos/<int:cid>/producto/<int:pid>/set')
def admin_combo_producto_set(cid, pid):
    if session.get('rol') != 'admin':
        return "Acceso restringido", 403
    slot = (request.form.get('slot') or '').strip().lower()
    if slot not in ('entrante','principal','bebida','postre'):
        return "slot inválido", 400

    exists = q("SELECT 1 FROM combo_items WHERE combo_id=%s AND producto_id=%s",
               cid, pid, one=True)
    if exists:
        q("UPDATE combo_items SET slot=%s WHERE combo_id=%s AND producto_id=%s",
          slot, cid, pid, commit=True)
    else:
        q("INSERT INTO combo_items(combo_id,producto_id,slot) VALUES(%s,%s,%s)",
          cid, pid, slot, commit=True)
    return "", 204



# ====== Quitar producto de combo ======
@app.post('/admin/combos/<int:cid>/producto/<int:pid>/remove')
def admin_combo_producto_remove(cid, pid):
    if session.get('rol') != 'admin':
        return "Acceso restringido", 403
    q("DELETE FROM combo_items WHERE combo_id=%s AND producto_id=%s", cid, pid, commit=True)
    return "", 204

# ───────── Admin: CRUD producto (Carta) ─────────
from werkzeug.utils import secure_filename


@app.route('/admin/producto/nuevo', methods=['GET','POST'])
def admin_producto_nuevo():
    if not admin_required(): return redirect(url_for('index'))
    if request.method=='POST':
        cat  = request.form.get('categoria')
        nombre = request.form.get('nombre')
        desc = request.form.get('descripcion')
        precio = request.form.get('precio')
        alergenos = request.form.getlist('alergenos')

        f = request.files.get('imagen')
        if not f or not allowed_file(f.filename):
            flash('Imagen obligatoria y de formato permitido','warning')
            return redirect(request.url)
        fname = secure_filename(f.filename)
        f.save(os.path.join(UPLOAD_FOLDER, fname))

        q("""INSERT INTO menu(categoria,nombre,descripcion,precio,imagen,alergenos,agotado,visible)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
          cat,nombre,desc,precio,fname,alergenos,False,True,commit=True)

        flash('Producto añadido','success')
        return redirect(url_for('admin_index', seccion='carta'))

    return render_template('admin/admin_producto_nuevo.html',
                           categorias=CATEGORIAS, alergenos=ALERGENOS)

@app.route('/admin/menu/eliminar/<int:pid>', methods=['POST'])
def admin_menu_eliminar(pid):
    if not admin_required(): return redirect(url_for('index'))
    q("DELETE FROM menu WHERE id=%s", pid, commit=True)
    flash('Producto eliminado','info')
    return redirect(url_for('admin_index', seccion='carta'))

@app.route('/admin/producto/<int:pid>/editar', methods=['GET','POST'])
def admin_producto_editar(pid):
    if not admin_required(): return redirect(url_for('index'))
    p = q("SELECT * FROM menu WHERE id=%s", pid, one=True)
    if not p:
        flash('Producto no encontrado','warning')
        return redirect(url_for('admin_index', seccion='carta'))

    if request.method=='POST':
        cat  = request.form.get('categoria')
        nombre = request.form.get('nombre')
        desc = request.form.get('descripcion')
        precio = request.form.get('precio')
        alergenos = request.form.getlist('alergenos')
        agotado = bool(request.form.get('agotado'))
        visible = bool(request.form.get('visible'))

        fname = p['imagen']
        f = request.files.get('imagen')
        if f and f.filename:
            if not allowed_file(f.filename):
                flash('Formato de imagen no permitido','warning')
                return redirect(request.url)
            fname = secure_filename(f.filename)
            f.save(os.path.join(UPLOAD_FOLDER, fname))

        q("""UPDATE menu
             SET categoria=%s, nombre=%s, descripcion=%s, precio=%s,
                 imagen=%s, alergenos=%s, agotado=%s, visible=%s
             WHERE id=%s""",
          cat,nombre,desc,precio,fname,alergenos,agotado,visible,pid,commit=True)

        flash('Producto actualizado','success')
        return redirect(url_for('admin_index', seccion='carta'))

    return render_template('admin/admin_producto_editar.html',
                           p=p, categorias=CATEGORIAS, alergenos=ALERGENOS)

@app.route('/admin/producto/<int:pid>/toggle-agotado', methods=['POST'])
def admin_producto_toggle_agotado(pid):
    if not admin_required(): return redirect(url_for('index'))
    q("UPDATE menu SET agotado = NOT COALESCE(agotado,FALSE) WHERE id=%s", pid, commit=True)
    return redirect(url_for('admin_index', seccion='carta'))

@app.route('/admin/producto/<int:pid>/toggle-visible', methods=['POST'])
def admin_producto_toggle_visible(pid):
    if not admin_required(): return redirect(url_for('index'))
    q("UPDATE menu SET visible = NOT COALESCE(visible,TRUE) WHERE id=%s", pid, commit=True)
    return redirect(url_for('admin_index', seccion='carta'))

# ─── Admin: CRUD combos (renombrar, precio, categorías) ───
@app.post('/admin/combos/crear')
def admin_combo_crear():
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning'); return redirect(url_for('index'))

    nombre = (request.form.get('nombre') or '').strip()
    precio = request.form.get('precio_base') or 0

    def to_array(s): 
        s = (s or '').strip()
        return [v.strip() for v in s.split(',')] if s else None

    entr = to_array(request.form.get('entrante_cats'))
    prin = to_array(request.form.get('principal_cats'))
    bebi = to_array(request.form.get('bebida_cats'))
    post = to_array(request.form.get('postre_cats'))

    if not nombre:
        flash('Nombre es obligatorio.', 'warning')
        return redirect(url_for('admin_index', seccion='disena'))

    q("""INSERT INTO combos(nombre, precio_base, entrante_cats, principal_cats, bebida_cats, postre_cats)
         VALUES(%s,%s,%s,%s,%s,%s)""",
      nombre, precio, entr, prin, bebi, post, commit=True)
    flash('Menú creado.', 'success')
    return redirect(url_for('admin_index', seccion='disena'))

@app.post('/admin/combos/<int:cid>/actualizar')
def admin_combo_actualizar(cid):
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning'); return redirect(url_for('index'))

    nombre = (request.form.get('nombre') or '').strip()
    precio = request.form.get('precio_base') or 0

    def to_array(s): 
        s = (s or '').strip()
        return [v.strip() for v in s.split(',')] if s else None

    entr = to_array(request.form.get('entrante_cats'))
    prin = to_array(request.form.get('principal_cats'))
    bebi = to_array(request.form.get('bebida_cats'))
    post = to_array(request.form.get('postre_cats'))

    q("""UPDATE combos 
         SET nombre=%s, precio_base=%s, entrante_cats=%s, principal_cats=%s, bebida_cats=%s, postre_cats=%s
         WHERE id=%s""",
      nombre, precio, entr, prin, bebi, post, cid, commit=True)
    flash('Menú actualizado.', 'success')
    return redirect(url_for('admin_index', seccion='disena', combo_id=cid))

@app.post('/admin/combos/<int:cid>/eliminar')
def admin_combo_eliminar(cid):
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning'); return redirect(url_for('index'))
    q("DELETE FROM combos WHERE id=%s", cid, commit=True)
    flash('Menú eliminado.', 'info')
    return redirect(url_for('admin_index', seccion='disena'))

# ─── Admin: añadir/quitar producto explícito al combo (tabla combo_items) ───
@app.post('/admin/combos/<int:cid>/producto/<int:pid>/toggle')
def admin_combo_producto_toggle(cid, pid):
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning'); return redirect(url_for('index'))

    combo = q("""SELECT id, nombre, precio_base, 
                        entrante_cats, principal_cats, bebida_cats, postre_cats
                 FROM combos WHERE id=%s""", cid, one=True)
    prod  = q("SELECT id, categoria FROM menu WHERE id=%s", pid, one=True)
    if not combo or not prod:
        flash('Menú o producto no encontrado.', 'warning')
        return redirect(url_for('admin_index', seccion='disena'))

    slot = _slot_for_categoria(prod['categoria'] if isinstance(prod, dict) else prod[1], combo)
    if not slot:
        flash('La categoría del producto no encaja en ningún slot del menú.', 'warning')
        return redirect(url_for('admin_index', seccion='disena', combo_id=cid))

    ya = q("SELECT 1 FROM combo_items WHERE combo_id=%s AND producto_id=%s", cid, pid, one=True)
    if ya:
        q("DELETE FROM combo_items WHERE combo_id=%s AND producto_id=%s", cid, pid, commit=True)
        flash('Producto quitado del menú.', 'info')
    else:
        q("INSERT INTO combo_items(combo_id, producto_id, slot) VALUES(%s,%s,%s)", cid, pid, slot, commit=True)
        flash('Producto añadido al menú.', 'success')

    return redirect(url_for('admin_index', seccion='disena', combo_id=cid,
                            q=(request.form.get('q') or ''), cat=(request.form.get('cat') or '')))

# ───────── Admin: acciones sobre usuarios (asegúrate de tener esto) ─────────
@app.route('/admin/usuario/<int:uid>/rol', methods=['POST'])
def admin_usuario_cambiar_rol(uid):
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning')
        return redirect(url_for('index'))

    nuevo = request.form.get('rol')
    if not nuevo:
        flash('No se indicó rol para actualizar.', 'warning')
        return redirect(url_for('admin_index', seccion='usuarios'))

    q("UPDATE usuarios SET rol=%s WHERE id=%s", nuevo, uid, commit=True)
    flash('Rol actualizado', 'success')
    return redirect(url_for('admin_index', seccion='usuarios'))

# ───────── Admin: activar/desactivar y eliminar usuario ─────────
@app.route('/admin/usuario/<int:uid>/toggle', methods=['POST'])
def admin_usuario_toggle(uid):
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning')
        return redirect(url_for('index'))
    q("UPDATE usuarios SET activo = NOT COALESCE(activo, TRUE) WHERE id=%s", uid, commit=True)
    return redirect(url_for('admin_index', seccion='usuarios'))

@app.route('/admin/usuario/<int:uid>/eliminar', methods=['POST'])
def admin_usuario_eliminar(uid):
    if session.get('rol') != 'admin':
        flash('Acceso restringido', 'warning')
        return redirect(url_for('index'))
    q("DELETE FROM usuarios WHERE id=%s", uid, commit=True)
    return redirect(url_for('admin_index', seccion='usuarios'))

# ───────── Admin: acciones sobre reservas (sin asignar mesa) ─────────
@app.post('/admin/reserva/<int:rid>/confirmar')
def admin_reserva_confirmar(rid):
    if not admin_required(): return redirect(url_for('index'))
    q("UPDATE reservas SET estado='confirmada' WHERE id=%s", rid, commit=True)
    r = q("SELECT nombre, email, fecha, hora FROM reservas WHERE id=%s", rid, one=True)
    if r and r.get('email'):
        try:
            asunto = f"Reserva #{rid} confirmada – El Rincón del Soldado"
            texto  = f"Hola {r['nombre']}, tu reserva para el {r['fecha']} a las {r['hora']} ha sido CONFIRMADA."
            send_email(r['email'], asunto, texto)
        except Exception as e:
            app.logger.warning(f"No se pudo enviar email de confirmación: {e}")
    flash('Reserva confirmada', 'success')
    return redirect(url_for('admin_index', seccion='reservas'))

@app.post('/admin/reserva/<int:rid>/cancelar')
def admin_reserva_cancelar(rid):
    if not admin_required(): return redirect(url_for('index'))
    q("UPDATE reservas SET estado='cancelada' WHERE id=%s", rid, commit=True)
    r = q("SELECT nombre, email, fecha, hora FROM reservas WHERE id=%s", rid, one=True)
    if r and r.get('email'):
        try:
            asunto = f"Reserva #{rid} cancelada – El Rincón del Soldado"
            texto  = f"Hola {r['nombre']}, tu reserva para el {r['fecha']} a las {r['hora']} ha sido CANCELADA."
            send_email(r['email'], asunto, texto)
        except Exception as e:
            app.logger.warning(f"No se pudo enviar email de cancelación: {e}")
    flash('Reserva cancelada', 'info')
    return redirect(url_for('admin_index', seccion='reservas'))

@app.post('/admin/reserva/<int:rid>/responder')
def admin_reserva_responder(rid):
    if not admin_required(): return redirect(url_for('index'))
    asunto  = request.form.get('asunto') or f"Respuesta a tu reserva #{rid}"
    mensaje = request.form.get('mensaje') or ''
    r = q("SELECT nombre, email FROM reservas WHERE id=%s", rid, one=True)
    if not r or not r.get('email'):
        flash('La reserva no tiene email para responder.','warning')
        return redirect(url_for('admin_index',seccion='reservas'))
    try:
        cuerpo = f"Hola {r['nombre']},\n\n{mensaje}\n\n— El Rincón del Soldado"
        send_email(r['email'], asunto, cuerpo)
        flash('Respuesta enviada','success')
    except Exception as e:
        app.logger.warning(f"Error enviando respuesta: {e}")
        flash('No se pudo enviar el correo.','danger')
    return redirect(url_for('admin_index',seccion='reservas'))


@app.post('/admin/reserva/<int:rid>/eliminar')
def admin_reserva_eliminar(rid):
    if not admin_required(): 
        return redirect(url_for('index'))
    q("DELETE FROM reservas WHERE id=%s", rid, commit=True)
    flash('Reserva eliminada.', 'info')
    return redirect(url_for('admin_index', seccion='reservas'))



# ───────── Admin: acciones sobre CONTACTO (tabla 'contacto') ─────────
@app.post('/admin/contacto/<int:cid>/publicar')
def admin_contacto_publicar(cid):
    if not admin_required(): return redirect(url_for('index'))
    resp = (request.form.get('respuesta') or '').strip()
    if not resp:
        flash('Escribe una respuesta.','warning')
        return redirect(url_for('admin_index', seccion='contacto'))
    q("UPDATE contacto SET admin_respuesta=%s, admin_estado='respondido' WHERE id=%s",
      resp, cid, commit=True)
    flash('Respuesta publicada en la web del usuario.','success')
    return redirect(url_for('admin_index', seccion='contacto'))

@app.post('/admin/contacto/<int:cid>/email')
def admin_contacto_email(cid):
    if not admin_required(): return redirect(url_for('index'))
    resp = (request.form.get('respuesta') or '').strip()
    c = q("SELECT nombre, email FROM contacto WHERE id=%s", cid, one=True)
    if not c or not c.get('email'):
        flash('No hay email para responder.','warning')
        return redirect(url_for('admin_index', seccion='contacto'))
    try:
        asunto = request.form.get('asunto') or 'Respuesta a tu mensaje'
        cuerpo = f"Hola {c['nombre']},\n\n{resp}\n\n— El Rincón del Soldado"
        send_email(c['email'], asunto, cuerpo)
        q("UPDATE contacto SET admin_estado='respondido' WHERE id=%s", cid, commit=True)
        flash('Correo enviado.','success')
    except Exception as e:
        app.logger.warning(f"Error enviando respuesta de contacto: {e}")
        flash('No se pudo enviar el correo.','danger')
    return redirect(url_for('admin_index', seccion='contacto'))

@app.post('/admin/contacto/<int:cid>/eliminar')
def admin_contacto_eliminar(cid):
    if not admin_required(): return redirect(url_for('index'))
    q("DELETE FROM contacto WHERE id=%s", cid, commit=True)
    flash('Mensaje de contacto eliminado.','info')
    return redirect(url_for('admin_index', seccion='contacto'))

# --- RESERVAS (form clásico con POST) ---
@app.route('/reservas', methods=['GET', 'POST'], endpoint='reservas')
@app.route('/reservas', methods=['GET', 'POST'])
def pagina_reservas():
    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        telefono = (request.form.get('telefono') or '').strip()
        email = (request.form.get('email') or '').strip()  # opcional
        fecha = _norm_fecha(request.form.get('fecha'))
        hora  = _norm_hora(request.form.get('hora'))
        personas = request.form.get('personas')
        tipo = (request.form.get('tipo') or '').strip()    # opcional
        comentarios = request.form.get('comentarios')
        mesa_id = request.form.get('mesa_id', type=int)    # OBLIGATORIA (según tu UI)

        if not (nombre and telefono and fecha and hora and personas and mesa_id):
            flash("Faltan campos obligatorios (incluye la mesa).", "warning")
            return redirect(url_for('pagina_reservas'))

        # comprobar que la mesa esté libre
        conflict = q("""
            SELECT 1 FROM reservas
            WHERE mesa_id=%s AND fecha=%s::date
              AND estado IN ('pendiente','confirmada')
              AND hora BETWEEN (%s::time - interval '2 hour') AND (%s::time + interval '2 hour')
            LIMIT 1
        """, mesa_id, fecha, hora, hora, one=True)
        if conflict:
            flash("Esa mesa ya está ocupada en ese tramo horario. Elige otra.", "danger")
            return redirect(url_for('pagina_reservas'))

        try:
            q("""INSERT INTO reservas (nombre, telefono, email, fecha, hora, personas, tipo, comentarios, mesa_id, estado)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pendiente')""",
              nombre, telefono, (email or None), fecha, hora, personas, (tipo or None), comentarios, mesa_id,
              commit=True)
            if email:
                try:
                    asunto = "Solicitud de reserva recibida – El Rincón del Soldado"
                    texto  = f"Hola {nombre}, hemos recibido tu solicitud para el {fecha} a las {hora} (personas: {personas}). En breve la confirmaremos."
                    send_email(email, asunto, texto)
                except Exception as e:
                    app.logger.warning(f"No se pudo enviar email de reserva: {e}")
            flash("Reserva enviada. Te contactaremos para confirmar.", "success")
            return redirect(url_for('pagina_reservas'))
        except Exception as e:
            app.logger.exception(e)
            flash("Hubo un problema al guardar la reserva.", "danger")
            return redirect(url_for('pagina_reservas'))

    return render_template("pages/reservas.html")

# ──────────── PERFIL + AVATAR (NUEVO) ────────────
@app.get("/perfil")
def perfil():
    if not session.get('usuario_id'):
        flash('Inicia sesión para ver tu perfil', 'warning')
        return redirect(url_for('login'))

    uid = session['usuario_id']
    user = q("SELECT id, nombre, email, avatar FROM usuarios WHERE id=%s", uid, one=True)

    perfil = q("""SELECT telefono, direccion, ciudad, metodo_pago_pref, pago_alias, pago_last4
                  FROM perfiles_usuario WHERE usuario_id=%s""", uid, one=True) or {}

    favoritos = q("""
        SELECT p.id, p.nombre, p.categoria, p.precio, p.imagen
        FROM favoritos f
        JOIN menu p ON p.id = f.producto_id
        WHERE f.usuario_id=%s
        ORDER BY f.creado DESC
    """, uid) or []

    pedidos = q("""
        SELECT id, num_pedido, total, estado, creado_en, fecha,
               COALESCE(metodo_pago,'') AS metodo_pago,
               COALESCE(prep_estado,'pendiente') AS prep_estado
        FROM pedidos WHERE usuario_id=%s
        ORDER BY creado_en DESC
    """, uid) or []

    # Etiqueta amigable
    for p in pedidos:
        est  = (p.get('estado') or '').lower()
        met  = (p.get('metodo_pago') or '').lower()
        prep = (p.get('prep_estado') or 'pendiente').lower()

        if est == 'pagado':
            # mostrar si se está preparando
            prep_txt = 'preparando' if prep in ('preparando','en_preparacion') else 'pendiente de preparación'
            p['estado_label'] = f"Pagado · {prep_txt}"
        elif est == 'pendiente' and met == 'recoger':
            p['estado_label'] = "Pendiente · pagar al recoger"
        elif est == 'pendiente':
            p['estado_label'] = "Pendiente de pago"
        elif est == 'completado':
            p['estado_label'] = "Completado"
        elif est == 'cancelado':
            p['estado_label'] = "Cancelado"
        else:
            p['estado_label'] = est.capitalize() if est else "—"

    return render_template("pages/perfil.html",
                           user=user, perfil=perfil,
                           favoritos=favoritos, pedidos=pedidos)





@app.post("/perfil/datos")
def perfil_datos():
    if not session.get('usuario_id'):
        return redirect(url_for('login'))
    uid = session['usuario_id']

    tel = request.form.get("telefono")
    dirc= request.form.get("direccion")
    ciu = request.form.get("ciudad")
    met = request.form.get("metodo_pago_pref")
    ali = request.form.get("pago_alias")
    l4  = request.form.get("pago_last4")

    ex = q("SELECT 1 FROM perfiles_usuario WHERE usuario_id=%s", uid, one=True)
    if ex:
        q("""UPDATE perfiles_usuario
             SET telefono=%s, direccion=%s, ciudad=%s,
                 metodo_pago_pref=%s, pago_alias=%s, pago_last4=%s, updated_at=now()
             WHERE usuario_id=%s""", tel, dirc, ciu, met, ali, l4, uid, commit=True)
    else:
        q("""INSERT INTO perfiles_usuario(usuario_id,telefono,direccion,ciudad,metodo_pago_pref,pago_alias,pago_last4)
             VALUES(%s,%s,%s,%s,%s,%s,%s)""", uid, tel, dirc, ciu, met, ali, l4, commit=True)

    flash("Datos de perfil actualizados.", "success")
    return redirect(url_for("perfil"))

@app.post("/perfil/password")
def perfil_password():
    if not session.get('usuario_id'):
        return redirect(url_for('login'))
    uid   = session['usuario_id']
    actual = request.form.get("password_actual", "")
    nueva  = request.form.get("password_nueva", "")
    if len(nueva) < 6:
        flash("La nueva contraseña debe tener al menos 6 caracteres.", "warning")
        return redirect(url_for("perfil"))

    u = q("SELECT id, password FROM usuarios WHERE id=%s", uid, one=True)
    if not u or not bcrypt.check_password_hash(u["password"], actual):
        flash("La contraseña actual no es correcta.", "danger")
        return redirect(url_for("perfil"))

    new_hash = bcrypt.generate_password_hash(nueva).decode()
    q("UPDATE usuarios SET password=%s WHERE id=%s", new_hash, uid, commit=True)
    flash("Contraseña actualizada.", "success")
    return redirect(url_for("perfil"))

@app.post("/perfil/avatar")
def perfil_avatar():
    if not session.get('usuario_id'):
        return redirect(url_for('login'))
    uid = session['usuario_id']
    f = request.files.get("avatar")
    if not f or not f.filename:
        flash("No seleccionaste imagen.", "warning")
        return redirect(url_for("perfil"))
    ext = (f.filename.rsplit(".",1)[-1] or "").lower()
    if ext not in ALLOWED_EXT:
        flash("Formato no permitido (usa png, jpg, jpeg, webp o gif).", "warning")
        return redirect(url_for("perfil"))

    import time as _t
    safe_base = secure_filename(f.filename.rsplit(".",1)[0])[:36] or "avatar"
    fname = f"{uid}_{int(_t.time())}_{safe_base}.{ext}"
    path  = os.path.join(AVATARS_DIR, fname)
    f.save(path)

    rel = f"img/avatars/{fname}"
    q("UPDATE usuarios SET avatar=%s WHERE id=%s", rel, uid, commit=True)
    session["avatar"] = rel  # para el navbar
    flash("Avatar actualizado.", "success")
    return redirect(url_for("perfil"))

# ──────────── FAVORITOS (NUEVO) ────────────
@app.post("/favoritos/<int:pid>")
def favoritos_toggle(pid):
    if not session.get('usuario_id'):
        return redirect(url_for('login'))
    uid = session['usuario_id']
    ex = q("SELECT 1 FROM favoritos WHERE usuario_id=%s AND producto_id=%s", uid, pid, one=True)
    if ex:
        q("DELETE FROM favoritos WHERE usuario_id=%s AND producto_id=%s", uid, pid, commit=True)
        flash("Quitado de favoritos.", "success")
    else:
        q("INSERT INTO favoritos(usuario_id, producto_id) VALUES(%s,%s)", uid, pid, commit=True)
        flash("Añadido a favoritos.", "success")
    return redirect(request.referrer or url_for("menu"))

@app.get("/favoritos")
def favoritos_page():
    if not session.get('usuario_id'):
        return redirect(url_for('login'))
    uid = session['usuario_id']
    productos = q("""
      SELECT p.id, p.nombre, p.categoria, p.precio, p.descripcion, p.imagen
      FROM favoritos f
      JOIN menu p ON p.id = f.producto_id
      WHERE f.usuario_id=%s
      ORDER BY f.creado DESC
    """, uid) or []
    # 👇 Enviar con el nombre que espera el template:
    return render_template("pages/favoritos.html", favoritos=productos)



# ───────── Run ─────────
if __name__ == '__main__':
    app.run(debug=True)
