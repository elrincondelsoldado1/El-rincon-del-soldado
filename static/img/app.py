from flask import Flask, render_template, request, redirect, url_for, session, flash
app = Flask(__name__)
app.secret_key = 'clave_secreta'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/menu')
def menu():
    return render_template('menu.html')

@app.route('/disena_menu')
def disena_menu():
    carrito = session.get('carrito', [])
    return render_template("disena_menu.html", carrito=carrito)

@app.route('/agregar_al_carrito', methods=['POST'])
def agregar_al_carrito():
    item = {
        'nombre': request.form['nombre'],
        'precio_menu': float(request.form['precio']),
        'imagen': request.form.get('imagen', ''),
        'extras': []
    }

    extras = request.form.getlist('extras')
    for extra in extras:
        nombre_extra, precio_extra = extra.split('|')
        item['extras'].append({
            'nombre': nombre_extra,
            'precio': float(precio_extra)
        })

    carrito = session.get('carrito', [])
    carrito.append(item)
    session['carrito'] = carrito
    flash('Producto añadido al carrito', 'success')
    return redirect(url_for('menu'))

@app.route('/checkout')
def checkout():
    carrito = session.get('carrito', [])
    total = 0
    for pedido in carrito:
        precio_menu = float(pedido.get('precio_menu', 0))
        total_extras = sum(float(extra.get('precio', 0)) for extra in pedido.get('extras', []))
        total += precio_menu + total_extras
    return render_template('checkout.html', carrito=carrito, total=total)

@app.route('/vaciar-carrito')
def vaciar_carrito():
    session['carrito'] = []
    flash('Carrito vaciado correctamente', 'info')
    return redirect(url_for('menu'))