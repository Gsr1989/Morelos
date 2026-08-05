"""
MORELOS — Panel web (Flask).

Cambios respecto a tu archivo original:
  · Config compartida en config_morelos (Supabase, candados, coordenadas)
  · ⚠️ El PDF ahora se genera DENTRO del candado compartido (antes no había)
  · Generación de folio unificada con el bot (watermark + candado)
  · FECHAS LIBRES: puedes poner cualquier fecha, pasada o futura, sin límite
  · NUEVO: editor de tablas /admin/editor — edita CUALQUIER celda de Supabase
    desde aquí, sin entrar al sitio de Supabase
  · Se quitó el app.run(); lo monta main.py
"""

from flask import Flask, render_template, request, redirect, url_for, flash, \
    session, send_file, abort, jsonify, Response
from datetime import datetime, timedelta, date
import fitz
import os
import qrcode
import threading
from io import BytesIO
import html as _html
from werkzeug.middleware.proxy_fix import ProxyFix

from config_morelos import (
    supabase, logger, TZ_MORELOS, now_morelos, today_morelos, parse_date_any,
    OUTPUT_DIR, PLANTILLA_PRINCIPAL, PLANTILLA_SECUNDARIA, URL_CONSULTA_BASE,
    ENTIDAD, DIAS_PERMISO, PAGE_SIZE, PDF_LOCK, COORDS_MORELOS,
    TABLAS_DISPONIBLES, COLUMNAS_FECHA, FOLIO_NUM_PREFIJO,
    ADMIN_USER, ADMIN_PASS, SECRET_KEY,
    generar_folio_morelos, generar_placa_morelos,
)

# ===================== FLASK CONFIG =====================
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=2, x_host=2, x_prefix=1)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    SEND_FILE_MAX_AGE_DEFAULT=0,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
)

flask_app = app                    # alias que usa main.py
PREFIJO_MORELOS = FOLIO_NUM_PREFIJO


# ===================== FOLIOS =====================
def generar_folio_automatico_morelos():
    """Ahora delega en el generador compartido (watermark + candado con el bot)."""
    return generar_folio_morelos()


def guardar_folio_con_reintento(datos, username):
    """Guarda el folio en la BD. Si choca por duplicado, pide otro."""
    if not datos.get("folio"):
        try:
            datos["folio"] = generar_folio_automatico_morelos()
        except Exception as e:
            logger.error(f"[ERROR] No se pudo generar folio: {e}")
            return False

    fexp_date = parse_date_any(datos["fecha_exp"])
    fven_date = parse_date_any(datos["fecha_ven"])

    def _row(folio):
        return {
            "folio":             folio,
            "marca":             datos["marca"],
            "linea":             datos["linea"],
            "anio":              datos["anio"],
            "numero_serie":      datos["numero_serie"],
            "numero_motor":      datos["numero_motor"],
            "nombre":            datos.get("nombre", "SIN NOMBRE"),
            "color":             datos.get("color", "N/A"),
            "tipo":              datos.get("tipo", "N/A"),
            "fecha_expedicion":  fexp_date.isoformat(),
            "fecha_vencimiento": fven_date.isoformat(),
            "entidad":           ENTIDAD,
            "estado":            "ACTIVO",
            "creado_por":        username,
        }

    folio_actual = datos["folio"]
    for intento in range(50):
        try:
            supabase.table("folios_registrados").insert(_row(folio_actual)).execute()
            datos["folio"] = folio_actual
            logger.info(f"[DB] ✅ Folio {folio_actual} guardado (intento {intento + 1})")
            return True
        except Exception as e:
            em = str(e).lower()
            if any(k in em for k in ("duplicate", "unique", "23505")):
                logger.warning(f"[DUP] {folio_actual} existe, pidiendo otro...")
                try:
                    folio_actual = generar_folio_automatico_morelos()
                except Exception as e2:
                    logger.error(f"[ERROR] {e2}")
                    return False
                continue
            logger.error(f"[ERROR BD] {e}")
            return False

    logger.error("[ERROR] No se encontró folio disponible tras 50 intentos")
    return False


# ===================== QR =====================
def generar_qr_dinamico_morelos(folio):
    try:
        url_directa = f"{URL_CONSULTA_BASE}/consulta/{folio}"
        qr = qrcode.QRCode(version=2,
                           error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=4, border=1)
        qr.add_data(url_directa)
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        logger.info(f"[QR] ✅ {folio}")
        return img_qr, url_directa
    except Exception as e:
        logger.error(f"[ERROR QR] {e}")
        return None, None


# ===================== PDF =====================
def generar_pdf_unificado_morelos(datos: dict) -> str:
    """
    ⚠️ FIX DE LA FUSIÓN: todo va dentro del candado compartido.
    Antes NI el panel NI el bot tenían lock; con dos permisos simultáneos
    PyMuPDF corrompía los documentos.
    """
    with PDF_LOCK:
        fol = datos["folio"]
        fecha_exp_dt = datos["fecha_exp"]
        fecha_ven_dt = datos["fecha_ven"]

        if isinstance(fecha_exp_dt, date) and not isinstance(fecha_exp_dt, datetime):
            fecha_exp_dt = datetime.combine(fecha_exp_dt, datetime.min.time()).replace(tzinfo=TZ_MORELOS)
        elif fecha_exp_dt.tzinfo is None:
            fecha_exp_dt = fecha_exp_dt.replace(tzinfo=TZ_MORELOS)
        else:
            fecha_exp_dt = fecha_exp_dt.astimezone(TZ_MORELOS)

        if isinstance(fecha_ven_dt, str):
            fecha_ven_str = fecha_ven_dt
        else:
            if isinstance(fecha_ven_dt, date) and not isinstance(fecha_ven_dt, datetime):
                fecha_ven_dt = datetime.combine(fecha_ven_dt, datetime.min.time()).replace(tzinfo=TZ_MORELOS)
            elif fecha_ven_dt.tzinfo is None:
                fecha_ven_dt = fecha_ven_dt.replace(tzinfo=TZ_MORELOS)
            else:
                fecha_ven_dt = fecha_ven_dt.astimezone(TZ_MORELOS)
            fecha_ven_str = fecha_ven_dt.strftime("%d/%m/%Y")

        out = os.path.join(OUTPUT_DIR, f"{fol}.pdf")
        c = COORDS_MORELOS

        try:
            doc1 = fitz.open(PLANTILLA_PRINCIPAL)
            pg1 = doc1[0]

            f1 = fecha_exp_dt.strftime("%d/%m/%Y")

            pg1.insert_text(c["folio"][:2], str(fol), fontsize=c["folio"][2], color=c["folio"][3])
            pg1.insert_text(c["fecha"][:2], f1, fontsize=c["fecha"][2], color=c["fecha"][3])
            pg1.insert_text(c["vigencia"][:2], fecha_ven_str, fontsize=c["vigencia"][2], color=c["vigencia"][3])
            pg1.insert_text(c["marca"][:2], str(datos["marca"]), fontsize=c["marca"][2], color=c["marca"][3])
            pg1.insert_text(c["serie"][:2], str(datos["numero_serie"]), fontsize=c["serie"][2], color=c["serie"][3])
            pg1.insert_text(c["linea"][:2], str(datos["linea"]), fontsize=c["linea"][2], color=c["linea"][3])
            pg1.insert_text(c["motor"][:2], str(datos["numero_motor"]), fontsize=c["motor"][2], color=c["motor"][3])
            pg1.insert_text(c["anio"][:2], str(datos["anio"]), fontsize=c["anio"][2], color=c["anio"][3])
            pg1.insert_text(c["color"][:2], str(datos.get("color", "N/A")), fontsize=c["color"][2], color=c["color"][3])
            pg1.insert_text(c["tipo"][:2], str(datos.get("tipo", "N/A")), fontsize=c["tipo"][2], color=c["tipo"][3])
            pg1.insert_text(c["nombre"][:2], str(datos.get("nombre", "")), fontsize=c["nombre"][2], color=c["nombre"][3])

            # Placa digital (si viene)
            if datos.get("placa"):
                pg1.insert_text(c["placa"][:2], str(datos["placa"]),
                                fontsize=c["placa"][2], color=c["placa"][3])

            img_qr, _ = generar_qr_dinamico_morelos(fol)
            if img_qr:
                buf = BytesIO()
                img_qr.save(buf, format="PNG")
                buf.seek(0)
                qr_pix = fitz.Pixmap(buf.read())
                pg1.insert_image(fitz.Rect(595, 148, 595 + 115, 148 + 115),
                                 pixmap=qr_pix, overlay=True)
                logger.info("[MORELOS] QR insertado en página 1")

            if os.path.exists(PLANTILLA_SECUNDARIA):
                doc2 = fitz.open(PLANTILLA_SECUNDARIA)
                pg2 = doc2[0]
                pg2.insert_text((155, 245), str(datos.get("nombre", "")).upper(), fontsize=18, fontname="helv")
                pg2.insert_text((1045, 205), str(fol), fontsize=20, fontname="helv")
                pg2.insert_text((1045, 275), fecha_exp_dt.strftime("%d/%m/%Y"), fontsize=20, fontname="helv")
                pg2.insert_text((1045, 348), fecha_exp_dt.strftime("%H:%M:%S"), fontsize=20, fontname="helv")
                doc1.insert_pdf(doc2)
                doc2.close()

            doc1.save(out)
            doc1.close()
            logger.info(f"[PDF] ✅ {out}")

        except Exception as e:
            logger.error(f"[ERROR PDF] {e}")
            doc_fallback = fitz.open()
            page = doc_fallback.new_page()
            page.insert_text((50, 50), f"ERROR - Folio: {fol}", fontsize=12)
            doc_fallback.save(out)
            doc_fallback.close()

        return out


# ===================== RUTAS =====================
@app.route('/')
def inicio():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username == ADMIN_USER and password == ADMIN_PASS:
            session['admin'] = True
            session['username'] = ADMIN_USER
            logger.info("[LOGIN] Admin")
            return redirect(url_for('admin'))

        resp = supabase.table("usuarios_morelos")\
            .select("*").eq("username", username).eq("password", password).execute()

        if resp.data:
            session['user_id'] = resp.data[0].get('id')
            session['username'] = resp.data[0]['username']
            session['admin'] = False
            logger.info(f"[LOGIN] {username}")
            return redirect(url_for('registro_usuario'))

        flash('Usuario o contraseña incorrectos', 'error')

    return render_template('login.html')


@app.route('/admin')
def admin():
    if not session.get('admin'):
        return redirect(url_for('login'))
    return render_template('panel.html')


@app.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        folios = int(request.form['folios'])

        existe = supabase.table("usuarios_morelos")\
            .select("id").eq("username", username).limit(1).execute()

        if existe.data:
            flash('Error: el usuario ya existe.', 'error')
        else:
            supabase.table("usuarios_morelos").insert({
                "username": username,
                "password": password,
                "folios_asignados": folios,
                "folios_usados": 0
            }).execute()
            flash('Usuario creado.', 'success')

    return render_template('crear_usuario.html')


@app.route('/registro_usuario', methods=['GET', 'POST'])
def registro_usuario():
    if not session.get('username'):
        return redirect(url_for('login'))
    if session.get('admin'):
        return redirect(url_for('admin'))

    user_data = supabase.table("usuarios_morelos")\
        .select("*").eq("username", session['username']).limit(1).execute()

    if not user_data.data:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for('login'))

    usuario = user_data.data[0]
    folios_asignados = int(usuario.get('folios_asignados', 0))
    folios_usados = int(usuario.get('folios_usados', 0))
    folios_disponibles = folios_asignados - folios_usados
    porcentaje = (folios_usados / folios_asignados * 100) if folios_asignados > 0 else 0

    ctx = dict(folios_asignados=folios_asignados,
               folios_usados=folios_usados,
               folios_disponibles=folios_disponibles,
               porcentaje=porcentaje,
               fecha_hoy=today_morelos().isoformat())

    if request.method == 'POST':
        if folios_disponibles <= 0:
            flash("⚠️ Sin folios disponibles.", "error")
            return render_template('registro_usuario.html', **ctx)

        marca        = request.form.get('marca', '').strip().upper()
        linea        = request.form.get('linea', '').strip().upper()
        anio         = request.form.get('anio', '').strip()
        numero_serie = request.form.get('serie', '').strip().upper()
        numero_motor = request.form.get('motor', '').strip().upper()
        color        = request.form.get('color', '').strip().upper()
        tipo         = request.form.get('tipo', '').strip().upper()
        nombre       = request.form.get('nombre', '').strip().upper() or 'SIN NOMBRE'
        fecha_inicio_str = request.form.get('fecha_inicio', '').strip()
        # FECHA DE VENCIMIENTO LIBRE (opcional). Si no la mandan, +30 días.
        fecha_ven_str    = request.form.get('fecha_vencimiento', '').strip()

        if not all([marca, linea, anio, numero_serie, numero_motor, fecha_inicio_str]):
            flash("❌ Faltan campos obligatorios.", "error")
            return render_template('registro_usuario.html', **ctx)

        try:
            # FECHAS LIBRES: se acepta cualquier fecha, pasada o futura
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').replace(tzinfo=TZ_MORELOS)
        except Exception:
            flash("❌ Fecha inválida.", "error")
            return render_template('registro_usuario.html', **ctx)

        if fecha_ven_str:
            try:
                venc = datetime.strptime(fecha_ven_str, '%Y-%m-%d').replace(tzinfo=TZ_MORELOS)
            except Exception:
                venc = fecha_inicio + timedelta(days=DIAS_PERMISO)
        else:
            venc = fecha_inicio + timedelta(days=DIAS_PERMISO)

        # PLACA DIGITAL — igual que el bot. Sale del contador compartido en
        # Supabase (watermark MOR_PLACA), así que nunca se repite entre el
        # panel y el bot.
        try:
            placa = generar_placa_morelos()
        except Exception as e:
            logger.error(f"[PLACA] {e}")
            placa = ""

        datos = {
            "folio": None,
            "placa": placa,
            "marca": marca, "linea": linea, "anio": anio,
            "numero_serie": numero_serie, "numero_motor": numero_motor,
            "color": color, "tipo": tipo, "nombre": nombre,
            "fecha_exp": fecha_inicio, "fecha_ven": venc,
        }

        if not guardar_folio_con_reintento(datos, session['username']):
            flash("❌ Error al registrar.", "error")
            return render_template('registro_usuario.html', **ctx)

        folio_final = datos["folio"]
        threading.Thread(target=generar_pdf_unificado_morelos,
                         args=(dict(datos),), daemon=True).start()

        supabase.table("usuarios_morelos")\
            .update({"folios_usados": folios_usados + 1})\
            .eq("username", session['username']).execute()

        flash(f'✅ Folio: {folio_final}  ·  Placa digital: {placa}', 'success')
        return render_template('exitoso.html',
                               folio=folio_final, serie=numero_serie, placa=placa,
                               fecha_generacion=fecha_inicio.strftime('%d/%m/%Y %H:%M'))

    return render_template('registro_usuario.html', **ctx)


@app.route('/mis_permisos')
def mis_permisos():
    if not session.get('username') or session.get('admin'):
        flash('Acceso denegado.', 'error')
        return redirect(url_for('login'))

    permisos = supabase.table("folios_registrados")\
        .select("*").eq("creado_por", session['username'])\
        .order("fecha_expedicion", desc=True).execute().data or []

    hoy = today_morelos()
    for p in permisos:
        try:
            fe = parse_date_any(p.get('fecha_expedicion'))
            fv = parse_date_any(p.get('fecha_vencimiento'))
            p['fecha_formateada'] = fe.strftime('%d/%m/%Y')
            p['hora_formateada'] = "00:00:00"
            p['estado'] = "VIGENTE" if hoy <= fv else "VENCIDO"
        except Exception:
            p['fecha_formateada'] = 'Error'
            p['hora_formateada'] = 'Error'
            p['estado'] = 'ERROR'

    usr_data = supabase.table("usuarios_morelos")\
        .select("folios_asignados, folios_usados")\
        .eq("username", session['username']).limit(1).execute().data
    usr_row = usr_data[0] if usr_data else {"folios_asignados": 0, "folios_usados": 0}

    return render_template('mis_permisos.html',
                           permisos=permisos, total_generados=len(permisos),
                           folios_asignados=int(usr_row.get('folios_asignados', 0)),
                           folios_usados=int(usr_row.get('folios_usados', 0)))


@app.route('/registro_admin', methods=['GET', 'POST'])
def registro_admin():
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        folio_manual = request.form.get('folio', '').strip()
        marca        = request.form.get('marca', '').strip().upper()
        linea        = request.form.get('linea', '').strip().upper()
        anio         = request.form.get('anio', '').strip()
        numero_serie = request.form.get('serie', '').strip().upper()
        numero_motor = request.form.get('motor', '').strip().upper()
        color        = request.form.get('color', '').strip().upper()
        tipo         = request.form.get('tipo', '').strip().upper()
        nombre       = request.form.get('nombre', '').strip().upper() or 'SIN NOMBRE'
        fecha_inicio_str = request.form.get('fecha_inicio', '').strip()
        fecha_ven_str    = request.form.get('fecha_vencimiento', '').strip()

        if not all([marca, linea, anio, numero_serie, numero_motor, fecha_inicio_str]):
            flash("❌ Faltan campos.", "error")
            return redirect(url_for('registro_admin'))

        try:
            # FECHAS LIBRES: cualquier fecha, pasada o futura
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').replace(tzinfo=TZ_MORELOS)
        except Exception:
            flash("❌ Fecha inválida.", "error")
            return redirect(url_for('registro_admin'))

        if fecha_ven_str:
            try:
                venc = datetime.strptime(fecha_ven_str, '%Y-%m-%d').replace(tzinfo=TZ_MORELOS)
            except Exception:
                venc = fecha_inicio + timedelta(days=DIAS_PERMISO)
        else:
            venc = fecha_inicio + timedelta(days=DIAS_PERMISO)

        # PLACA DIGITAL — mismo contador compartido que usa el bot
        try:
            placa = generar_placa_morelos()
        except Exception as e:
            logger.error(f"[PLACA] {e}")
            placa = ""

        datos = {
            "folio": folio_manual if folio_manual else None,
            "placa": placa,
            "marca": marca, "linea": linea, "anio": anio,
            "numero_serie": numero_serie, "numero_motor": numero_motor,
            "color": color, "tipo": tipo, "nombre": nombre,
            "fecha_exp": fecha_inicio, "fecha_ven": venc,
        }

        if not guardar_folio_con_reintento(datos, "ADMIN"):
            flash("❌ Error al registrar.", "error")
            return redirect(url_for('registro_admin'))

        folio_final = datos["folio"]
        threading.Thread(target=generar_pdf_unificado_morelos,
                         args=(dict(datos),), daemon=True).start()

        flash(f'✅ Permiso generado.  Placa digital: {placa}', 'success')
        return render_template('exitoso.html',
                               folio=folio_final, serie=numero_serie, placa=placa,
                               fecha_generacion=fecha_inicio.strftime('%d/%m/%Y %H:%M'))

    return render_template('registro_admin.html', fecha_hoy=today_morelos().isoformat())


@app.route('/consulta_folio', methods=['GET', 'POST'])
def consulta_folio():
    if request.method == 'POST':
        folio = request.form['folio'].strip()
        registros = supabase.table("folios_registrados")\
            .select("*").eq("folio", folio).limit(1).execute().data

        if not registros:
            resultado = {"estado": "NO REGISTRADO", "color": "rojo", "folio": folio}
        else:
            r = registros[0]
            fexp = parse_date_any(r.get('fecha_expedicion'))
            fven = parse_date_any(r.get('fecha_vencimiento'))
            hoy = today_morelos()
            estado = "VIGENTE" if hoy <= fven else "VENCIDO"
            resultado = {
                "estado": estado,
                "color": "verde" if estado == "VIGENTE" else "cafe",
                "folio": folio,
                "fecha_expedicion": fexp.strftime('%d/%m/%Y'),
                "fecha_vencimiento": fven.strftime('%d/%m/%Y'),
                "marca": r.get('marca', ''), "linea": r.get('linea', ''),
                "año": r.get('anio', ''),
                "numero_serie": r.get('numero_serie', ''),
                "numero_motor": r.get('numero_motor', ''),
                "color": r.get('color', 'N/A'), "tipo": r.get('tipo', 'N/A'),
                "entidad": r.get('entidad', ENTIDAD)
            }
        return render_template('resultado_consulta.html', resultado=resultado)

    return render_template('consulta_folio.html')


@app.route('/consulta/<folio>')
def consulta_folio_directo(folio):
    row = supabase.table("folios_registrados")\
        .select("*").eq("folio", folio).limit(1).execute().data

    if not row:
        return render_template("resultado_consulta.html", resultado={
            "estado": "NO REGISTRADO", "color": "rojo", "folio": folio})

    r = row[0]
    fe = parse_date_any(r.get('fecha_expedicion'))
    fv = parse_date_any(r.get('fecha_vencimiento'))
    hoy = today_morelos()
    estado = "VIGENTE" if hoy <= fv else "VENCIDO"

    resultado = {
        "estado": estado,
        "color": "verde" if estado == "VIGENTE" else "cafe",
        "folio": folio,
        "fecha_expedicion": fe.strftime("%d/%m/%Y"),
        "fecha_vencimiento": fv.strftime("%d/%m/%Y"),
        "marca": r.get('marca', ''), "linea": r.get('linea', ''),
        "año": r.get('anio', ''),
        "numero_serie": r.get('numero_serie', ''),
        "numero_motor": r.get('numero_motor', ''),
        "color": r.get('color', 'N/A'), "tipo": r.get('tipo', 'N/A'),
        "entidad": r.get('entidad', ENTIDAD)
    }
    return render_template("resultado_consulta.html", resultado=resultado)


@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    # El panel guarda {folio}.pdf y el bot {folio}_completo.pdf — buscar ambos
    for nombre in (f"{folio}.pdf", f"{folio}_completo.pdf"):
        ruta = os.path.join(OUTPUT_DIR, nombre)
        if os.path.exists(ruta):
            return send_file(ruta, as_attachment=True,
                             download_name=f"{folio}_morelos.pdf",
                             mimetype='application/pdf')
    abort(404)


@app.route('/admin_folios')
def admin_folios():
    if not session.get('admin'):
        return redirect(url_for('login'))

    filtro        = request.args.get('filtro', '').strip()
    criterio      = request.args.get('criterio', 'folio')
    estado_filtro = request.args.get('estado', 'todos')
    fecha_inicio  = request.args.get('fecha_inicio', '')
    fecha_fin     = request.args.get('fecha_fin', '')
    ordenar       = request.args.get('ordenar', 'desc')

    query = supabase.table("folios_registrados").select("*").eq("entidad", ENTIDAD)

    if filtro:
        if criterio == 'folio':
            query = query.ilike('folio', f'%{filtro}%')
        elif criterio == 'numero_serie':
            query = query.ilike('numero_serie', f'%{filtro}%')

    if fecha_inicio:
        query = query.gte('fecha_expedicion', fecha_inicio)
    if fecha_fin:
        query = query.lte('fecha_expedicion', fecha_fin)

    query = query.order('fecha_expedicion', desc=(ordenar == 'desc'))
    folios = query.execute().data or []

    hoy = today_morelos()
    folios_filtrados = []
    for f in folios:
        try:
            fv = parse_date_any(f.get('fecha_vencimiento'))
            f['estado'] = "VIGENTE" if hoy <= fv else "VENCIDO"
            if estado_filtro == 'todos':
                folios_filtrados.append(f)
            elif estado_filtro == 'vigente' and f['estado'] == 'VIGENTE':
                folios_filtrados.append(f)
            elif estado_filtro == 'vencido' and f['estado'] == 'VENCIDO':
                folios_filtrados.append(f)
        except Exception:
            f['estado'] = 'ERROR'
            if estado_filtro == 'todos':
                folios_filtrados.append(f)

    return render_template('admin_folios.html',
                           folios=folios_filtrados, filtro=filtro, criterio=criterio,
                           estado=estado_filtro, fecha_inicio=fecha_inicio,
                           fecha_fin=fecha_fin, ordenar=ordenar)


@app.route('/editar_folio/<folio>', methods=['GET', 'POST'])
def editar_folio(folio):
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        data = {
            "folio": request.form['folio'],
            "marca": request.form['marca'],
            "linea": request.form['linea'],
            "anio": request.form['anio'],
            "numero_serie": request.form['serie'],
            "numero_motor": request.form['motor'],
            "color": request.form.get('color', 'N/A'),
            "tipo": request.form.get('tipo', 'N/A'),
            "nombre": request.form.get('nombre', 'SIN NOMBRE'),
            # FECHAS LIBRES — cualquier fecha, sin validación de rango
            "fecha_expedicion": request.form['fecha_expedicion'],
            "fecha_vencimiento": request.form['fecha_vencimiento'],
        }
        supabase.table("folios_registrados").update(data).eq("folio", folio).execute()
        flash("Folio actualizado.", "success")
        return redirect(url_for('admin_folios'))

    resp = supabase.table("folios_registrados").select("*").eq("folio", folio).execute()
    if not resp.data:
        flash("Folio no encontrado.", "error")
        return redirect(url_for('admin_folios'))

    return render_template("editar_folio.html", folio=resp.data[0])


@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if not session.get('admin'):
        return redirect(url_for('login'))
    folio = request.form['folio']
    try:
        import bot_morelos
        bot_morelos.cancelar_timer_folio(folio)
    except Exception:
        pass
    supabase.table("folios_registrados").delete().eq("folio", folio).execute()
    flash("Folio eliminado.", "success")
    return redirect(url_for('admin_folios'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ═══════════════════════════════════════════════════════════════════════════
#  EDITOR DE TABLAS — administra Supabase desde AQUÍ, sin entrar al sitio
#  · Click en cualquier celda → se edita en línea y se guarda solo
#  · Las columnas de fecha abren calendario (fechas pasadas Y futuras libres)
#  · Botón para borrar renglón y para agregar uno nuevo
#  · No necesita plantillas nuevas: el HTML va aquí mismo
# ═══════════════════════════════════════════════════════════════════════════

_EDITOR_CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f2f4f7;color:#1d1d1b}
.bar{background:#0d5c3d;color:#fff;padding:13px 18px;font-weight:700;font-size:15px;
     display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:50}
.bar a{color:#cfe9dd;text-decoration:none;font-size:13px;font-weight:600}
.bar a:hover{color:#fff}
.wrap{max-width:1400px;margin:18px auto;padding:0 14px}
.card{background:#fff;border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(0,0,0,.07);margin-bottom:16px}
.nota{background:#eef7f2;border-left:4px solid #0d5c3d;border-radius:8px;padding:12px 14px;
      font-size:13px;line-height:1.7;margin-bottom:16px}
.tablas-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.tabla-card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;
            text-decoration:none;color:inherit;display:block;transition:.15s}
.tabla-card:hover{border-color:#0d5c3d;transform:translateY(-2px);box-shadow:0 4px 14px rgba(13,92,61,.15)}
.tabla-card h3{margin:0 0 6px;font-size:15px;color:#0d5c3d}
.tabla-card p{margin:0;font-size:12px;color:#777}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
input[type=text],input[type=search]{padding:9px 12px;border:1.5px solid #d6dae0;border-radius:8px;font-size:14px;font-family:inherit}
input:focus{outline:none;border-color:#0d5c3d}
.btn{padding:9px 16px;border:none;border-radius:8px;font-weight:700;font-size:13px;
     cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px;font-family:inherit}
.btn-p{background:#0d5c3d;color:#fff}.btn-p:hover{background:#0a4a31}
.btn-o{background:#fff;border:1.5px solid #d6dae0;color:#444}.btn-o:hover{border-color:#0d5c3d;color:#0d5c3d}
.btn-d{background:#dc3545;color:#fff}
.tabla-wrap{overflow-x:auto;background:#fff;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.07)}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
thead th{background:#0d5c3d;color:#fff;padding:11px 10px;text-align:left;position:sticky;top:0}
tbody td{padding:6px 10px;border-bottom:1px solid #eef0f2;vertical-align:middle}
tbody tr:hover td{background:#f7fbf9}
.cv{display:inline-block;min-width:60px;max-width:240px;overflow:hidden;text-overflow:ellipsis;
    cursor:pointer;padding:4px 7px;border-radius:5px;border:1px solid transparent}
.cv:hover{border-color:#c8ccd2;background:#fff}
.cv.nv{color:#bbb;font-style:italic}
.cv.fecha{background:#fff8e6;border-color:#f0dfae}
.cell-input{border:2px solid #0d5c3d;border-radius:5px;padding:4px 7px;font-size:13px;
            min-width:130px;outline:none;background:#f7fbf9;font-family:inherit}
.del{background:#fff;border:1px solid #e0c3c3;color:#c0392b;border-radius:5px;
     padding:4px 9px;font-size:12px;cursor:pointer}
.del:hover{background:#c0392b;color:#fff}
.toast{position:fixed;bottom:22px;right:18px;z-index:999;padding:11px 18px;border-radius:9px;
       font-size:13px;font-weight:600;opacity:0;transition:opacity .25s;pointer-events:none;
       box-shadow:0 4px 14px rgba(0,0,0,.15)}
.toast.show{opacity:1}
.toast.ok{background:#e7f8ee;color:#0a6b3d;border:1px solid #9ad9b8}
.toast.err{background:#fdeaea;color:#a32020;border:1px solid #eba9a9}
.paginacion{display:flex;gap:8px;justify-content:center;align-items:center;padding:14px}
.pg{background:#0d5c3d;color:#fff;padding:7px 13px;border-radius:6px;font-size:13px;font-weight:700}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:900;display:flex;
       align-items:center;justify-content:center;padding:16px}
.modal-box{background:#fff;border-radius:12px;padding:24px;max-width:520px;width:100%;
           max-height:85vh;overflow-y:auto}
.modal-box h3{margin:0 0 16px;color:#0d5c3d}
.campo{margin-bottom:12px}
.campo label{display:block;font-size:13px;font-weight:600;margin-bottom:4px}
.campo input{width:100%;padding:9px 12px;border:1.5px solid #d6dae0;border-radius:7px;
             font-size:14px;font-family:inherit}
"""

_EDITOR_JS = """
let TABLA='', PK='';
function initEditor(t,p){TABLA=t;PK=p;}

function editCell(span){
  if(span.dataset.editing==='1') return;
  span.dataset.editing='1';
  const col=span.dataset.col, pk=span.dataset.pk, orig=span.dataset.val;
  const esFecha = span.classList.contains('fecha');
  const inp=document.createElement('input');
  inp.type = esFecha ? 'date' : 'text';
  inp.className='cell-input';
  // Para columnas de fecha: aceptar CUALQUIER fecha, pasada o futura (sin min/max)
  inp.value = esFecha ? (orig||'').substring(0,10) : orig;
  inp._span=span; inp._orig=orig; inp._col=col; inp._pk=pk;
  span.parentNode.insertBefore(inp,span);
  span.style.display='none';
  inp.focus(); if(!esFecha) inp.select();
  inp.addEventListener('blur',()=>fin(inp));
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();inp.blur();}
    if(e.key==='Escape'){inp._cancel=true;inp.blur();}
  });
}

function fin(inp){
  const span=inp._span, nv=inp.value.trim(), orig=inp._orig;
  inp.remove(); span.style.display=''; span.dataset.editing='0';
  if(inp._cancel||nv===orig) return;
  span.textContent = nv||'null';
  span.dataset.val = nv;
  span.classList.toggle('nv',!nv);
  fetch('/api/update_cell',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,pk_col:PK,pk_val:inp._pk,col:inp._col,val:nv})})
   .then(r=>r.json()).then(d=>{
      if(d.ok) toast('✓ Guardado en Supabase',true);
      else{span.textContent=orig||'null';span.dataset.val=orig;toast('Error: '+(d.error||'?'),false);}
   }).catch(()=>{span.textContent=orig||'null';span.dataset.val=orig;toast('Error de red',false);});
}

function delRow(btn,pk,rowId){
  if(!confirm('¿Eliminar este registro de Supabase?\\n\\nEsto NO se puede deshacer.')) return;
  btn.disabled=true; btn.textContent='...';
  fetch('/api/delete_row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,pk_col:PK,pk_val:pk})})
   .then(r=>r.json()).then(d=>{
      if(d.ok){const tr=document.getElementById(rowId);
        if(tr){tr.style.opacity='0';setTimeout(()=>tr.remove(),250);}
        toast('Eliminado',true);}
      else{btn.disabled=false;btn.textContent='Borrar';toast('Error: '+(d.error||'?'),false);}
   }).catch(()=>{btn.disabled=false;btn.textContent='Borrar';toast('Error de red',false);});
}

function abrirNuevo(){document.getElementById('modalNuevo').style.display='flex';}
function cerrarNuevo(){document.getElementById('modalNuevo').style.display='none';}

function guardarNuevo(){
  const campos={};
  document.querySelectorAll('#formNuevo [data-campo]').forEach(el=>{
    const v=el.value.trim(); if(v) campos[el.dataset.campo]=v;
  });
  if(Object.keys(campos).length===0){toast('Llena al menos un campo',false);return;}
  fetch('/api/add_row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tabla:TABLA,datos:campos})})
   .then(r=>r.json()).then(d=>{
      if(d.ok){toast('Agregado — recargando...',true);setTimeout(()=>location.reload(),700);}
      else toast('Error: '+(d.error||'?'),false);
   }).catch(()=>toast('Error de red',false));
}

let tt;
function toast(msg,ok){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show '+(ok?'ok':'err');
  clearTimeout(tt); tt=setTimeout(()=>t.classList.remove('show'),2600);
}
"""


def _editor_head(titulo: str) -> str:
    return f"""<!DOCTYPE html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo} — Morelos</title><style>{_EDITOR_CSS}</style></head><body>"""


@app.route('/admin/editor')
def admin_editor():
    """Índice del editor: lista las tablas que puedes administrar."""
    if not session.get('admin'):
        return redirect(url_for('login'))

    cards = "".join(
        f"""<a class="tabla-card" href="/admin/editor/{nombre}">
              <h3>🗄️ {info['nombre']}</h3>
              <p><code>{nombre}</code> · {len(info['columnas'])} columnas</p>
            </a>"""
        for nombre, info in TABLAS_DISPONIBLES.items()
    )

    return Response(_editor_head("Editor de Tablas") + f"""
<div class="bar">🛠️ Editor de Base de Datos — Morelos
  <span style="margin-left:auto"><a href="/admin">← Panel</a></span>
</div>
<div class="wrap">
  <div class="nota">
    Desde aquí administras <strong>Supabase directamente</strong>, sin entrar a su sitio.<br>
    Entra a una tabla, haz click en cualquier celda y edítala: se guarda sola.<br>
    Las columnas de fecha abren calendario y aceptan <strong>cualquier fecha,
    pasada o futura</strong>.
  </div>
  <div class="tablas-grid">{cards}</div>
</div>
</body></html>""", mimetype="text/html")


@app.route('/admin/editor/<nombre_tabla>')
def admin_editor_tabla(nombre_tabla):
    """Tabla editable celda por celda."""
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        return redirect(url_for('admin_editor'))

    info   = TABLAS_DISPONIBLES[nombre_tabla]
    pk_col = info['pk_col']
    q      = request.args.get('q', '').strip()
    page   = max(1, int(request.args.get('page', 1) or 1))

    try:
        todos = supabase.table(nombre_tabla).select("*").limit(20000).execute().data or []
        if q:
            ql = q.lower()
            filtrados = [r for r in todos
                         if any(ql in str(v).lower() for v in r.values() if v is not None)]
        else:
            filtrados = todos
        total     = len(filtrados)
        offset    = (page - 1) * PAGE_SIZE
        registros = filtrados[offset: offset + PAGE_SIZE]
    except Exception as e:
        logger.error(f"[EDITOR] {e}")
        todos = filtrados = registros = []
        total = offset = 0

    columnas = list(registros[0].keys()) if registros else (
        list(todos[0].keys()) if todos else info['columnas'])
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    def esc(v):
        return _html.escape(str(v), quote=True)

    th = "".join(f"<th>{esc(c)}</th>" for c in columnas) + "<th>Acción</th>"

    filas = ""
    for i, reg in enumerate(registros):
        celdas = f'<td style="color:#bbb;font-size:11px">{offset + i + 1}</td>'
        pk_val = esc(reg.get(pk_col, ""))
        for col in columnas:
            val = reg.get(col)
            disp = str(val) if val is not None else "null"
            clases = "cv"
            if val is None:
                clases += " nv"
            if col in COLUMNAS_FECHA:
                clases += " fecha"
            celdas += (
                f'<td><span class="{clases}" data-col="{esc(col)}" data-pk="{pk_val}" '
                f'data-val="{esc(val if val is not None else "")}" '
                f'onclick="editCell(this)">{esc(disp[:40])}</span></td>'
            )
        celdas += (f'<td><button class="del" onclick="delRow(this,\'{pk_val}\',\'r{i}\')">'
                   f'Borrar</button></td>')
        filas += f'<tr id="r{i}">{celdas}</tr>'

    if not filas:
        filas = (f'<tr><td colspan="{len(columnas)+2}" '
                 f'style="text-align:center;padding:26px;color:#999">Sin registros</td></tr>')

    pag = ""
    if total_pages > 1:
        pag = '<div class="paginacion">'
        if page > 1:
            pag += f'<a class="btn btn-o" href="?q={q}&page={page-1}">← Anterior</a>'
        pag += f'<span class="pg">{page} / {total_pages}</span>'
        if page < total_pages:
            pag += f'<a class="btn btn-o" href="?q={q}&page={page+1}">Siguiente →</a>'
        pag += '</div>'

    # Formulario del modal para agregar registro
    campos_nuevo = "".join(
        f"""<div class="campo"><label>{esc(c)}</label>
            <input type="{'date' if c in COLUMNAS_FECHA else 'text'}" data-campo="{esc(c)}"></div>"""
        for c in info['columnas'] if c != 'id'
    )

    return Response(_editor_head(info['nombre']) + f"""
<div class="bar">🗄️ {esc(info['nombre'])}
  <span style="margin-left:auto">
    <a href="/admin/editor">← Tablas</a> &nbsp;·&nbsp; <a href="/admin">Panel</a>
  </span>
</div>
<div class="wrap">
  <div class="nota">
    Click en cualquier celda para editarla. Se guarda sola en Supabase al salir
    del campo o al presionar Enter. <strong>Esc</strong> cancela.<br>
    Las celdas amarillas son fechas: abren calendario y aceptan
    <strong>cualquier fecha, pasada o futura</strong>.
  </div>

  <div class="toolbar">
    <form method="GET" style="display:contents">
      <input type="search" name="q" value="{esc(q)}" placeholder="Buscar en toda la tabla...">
      <button class="btn btn-p" type="submit">Buscar</button>
      {'<a class="btn btn-o" href="/admin/editor/' + nombre_tabla + '">Limpiar</a>' if q else ''}
    </form>
    <button class="btn btn-p" onclick="abrirNuevo()">+ Agregar registro</button>
    <span style="margin-left:auto;font-size:13px;color:#777">{total} registros</span>
  </div>

  <div class="tabla-wrap">
    <table><thead><tr><th>#</th>{th}</tr></thead><tbody>{filas}</tbody></table>
    {pag}
  </div>
</div>

<div class="modal" id="modalNuevo" style="display:none">
  <div class="modal-box">
    <h3>Agregar registro a {esc(info['nombre'])}</h3>
    <div id="formNuevo">{campos_nuevo}</div>
    <div style="display:flex;gap:8px;margin-top:18px">
      <button class="btn btn-p" onclick="guardarNuevo()">Guardar</button>
      <button class="btn btn-o" onclick="cerrarNuevo()">Cancelar</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>
<script>{_EDITOR_JS}
initEditor("{nombre_tabla}","{pk_col}");
</script>
</body></html>""", mimetype="text/html")


# ── API del editor ───────────────────────────────────────────────────────────

@app.route('/api/update_cell', methods=['POST'])
def api_update_cell():
    """Actualiza UNA celda. Acepta cualquier valor, incluidas fechas libres."""
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')
    col    = d.get('col')
    val    = d.get('val', '')

    if tabla not in TABLAS_DISPONIBLES or not col or pk_val in (None, ""):
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).update({col: val if val != "" else None}) \
            .eq(pk_col, pk_val).execute()
        logger.info(f"[EDITOR] {tabla}.{col} de {pk_val} → {val!r}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[EDITOR] update: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/delete_row', methods=['POST'])
def api_delete_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d      = request.get_json(force=True)
    tabla  = d.get('tabla')
    pk_col = d.get('pk_col')
    pk_val = d.get('pk_val')

    if tabla not in TABLAS_DISPONIBLES or pk_val in (None, ""):
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        if tabla == 'folios_registrados':
            try:
                import bot_morelos
                bot_morelos.cancelar_timer_folio(str(pk_val))
            except Exception:
                pass
        supabase.table(tabla).delete().eq(pk_col, pk_val).execute()
        logger.info(f"[EDITOR] borrado {tabla} pk={pk_val}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/add_row', methods=['POST'])
def api_add_row():
    if not session.get('admin'):
        return jsonify({"ok": False, "error": "no autorizado"}), 403
    d     = request.get_json(force=True)
    tabla = d.get('tabla')
    datos = d.get('datos') or {}

    if tabla not in TABLAS_DISPONIBLES or not datos:
        return jsonify({"ok": False, "error": "datos inválidos"}), 400
    try:
        supabase.table(tabla).insert(datos).execute()
        logger.info(f"[EDITOR] insertado en {tabla}: {list(datos.keys())}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  AJUSTE RÁPIDO DE FECHAS — pon cualquier fecha a un folio, pasada o futura
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/admin/fechas', methods=['GET', 'POST'])
def admin_fechas():
    if not session.get('admin'):
        return redirect(url_for('login'))

    msg = ""
    folio_buscar = request.args.get('folio', '').strip()
    registro = None

    if request.method == 'POST':
        folio   = request.form.get('folio', '').strip()
        accion  = request.form.get('accion', '')
        f_exp   = request.form.get('fecha_expedicion', '').strip()
        f_ven   = request.form.get('fecha_vencimiento', '').strip()

        try:
            if accion == 'manual':
                parches = {}
                if f_exp:
                    parches['fecha_expedicion'] = f_exp
                if f_ven:
                    parches['fecha_vencimiento'] = f_ven
                if parches:
                    supabase.table("folios_registrados").update(parches).eq("folio", folio).execute()
                    msg = f"Fechas del folio {folio} actualizadas."
                else:
                    msg = "No mandaste ninguna fecha."
            elif accion == 'vencer':
                ayer = (now_morelos() - timedelta(days=1)).date().isoformat()
                supabase.table("folios_registrados").update(
                    {"fecha_vencimiento": ayer}).eq("folio", folio).execute()
                msg = f"Folio {folio} marcado VENCIDO."
            elif accion == 'restaurar':
                hoy = now_morelos()
                supabase.table("folios_registrados").update({
                    "fecha_expedicion":  hoy.date().isoformat(),
                    "fecha_vencimiento": (hoy + timedelta(days=DIAS_PERMISO)).date().isoformat(),
                }).eq("folio", folio).execute()
                msg = f"Folio {folio} restaurado a {DIAS_PERMISO} días desde hoy."
            elif accion == 'retro':
                dias = int(request.form.get('dias_atras', '30') or 30)
                base = now_morelos() - timedelta(days=dias)
                supabase.table("folios_registrados").update({
                    "fecha_expedicion":  base.date().isoformat(),
                    "fecha_vencimiento": (base + timedelta(days=DIAS_PERMISO)).date().isoformat(),
                }).eq("folio", folio).execute()
                msg = f"Folio {folio} expedido {dias} días atrás."
        except Exception as e:
            msg = f"Error: {e}"
        folio_buscar = folio

    if folio_buscar:
        try:
            r = supabase.table("folios_registrados").select("*").eq("folio", folio_buscar).execute()
            registro = r.data[0] if r.data else None
            if not registro and not msg:
                msg = f"Folio {folio_buscar} no encontrado."
        except Exception as e:
            msg = f"Error: {e}"

    def esc(v):
        return _html.escape(str(v), quote=True)

    ficha = ""
    if registro:
        fe = str(registro.get('fecha_expedicion', ''))[:10]
        fv = str(registro.get('fecha_vencimiento', ''))[:10]
        ficha = f"""
        <div class="card">
          <div style="font-size:15px;font-weight:700;color:#0d5c3d;margin-bottom:12px">
            Folio {esc(registro.get('folio',''))}
          </div>
          <div style="font-size:13px;line-height:2;margin-bottom:16px">
            <strong>Titular:</strong> {esc(registro.get('nombre',''))}<br>
            <strong>Vehículo:</strong> {esc(registro.get('marca',''))} {esc(registro.get('linea',''))} {esc(registro.get('anio',''))}<br>
            <strong>Expedición actual:</strong> {esc(fe)}<br>
            <strong>Vencimiento actual:</strong> {esc(fv)}<br>
            <a href="/consulta/{esc(registro.get('folio',''))}" target="_blank"
               style="color:#0d5c3d">🔗 Ver consulta pública</a>
          </div>

          <form method="POST">
            <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
            <input type="hidden" name="accion" value="manual">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
              <div class="campo"><label>Nueva fecha de expedición</label>
                <input type="date" name="fecha_expedicion" value="{esc(fe)}"></div>
              <div class="campo"><label>Nueva fecha de vencimiento</label>
                <input type="date" name="fecha_vencimiento" value="{esc(fv)}"></div>
            </div>
            <button class="btn btn-p" type="submit">Guardar estas fechas</button>
            <p style="font-size:12px;color:#777;margin-top:8px">
              Sin límite: puedes poner fechas de años atrás o muy adelante.
            </p>
          </form>

          <hr style="margin:20px 0;border:none;border-top:1px solid #eee">
          <div style="font-size:13px;font-weight:700;margin-bottom:10px">Atajos</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <form method="POST" style="display:inline">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="vencer">
              <button class="btn" style="background:#b38b00;color:#fff">⏰ Marcar vencido</button>
            </form>
            <form method="POST" style="display:inline">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="restaurar">
              <button class="btn" style="background:#1a6e2e;color:#fff">✅ Restaurar {DIAS_PERMISO} días</button>
            </form>
            <form method="POST" style="display:inline;display:flex;gap:6px;align-items:center">
              <input type="hidden" name="folio" value="{esc(registro.get('folio',''))}">
              <input type="hidden" name="accion" value="retro">
              <input type="text" name="dias_atras" value="30" style="width:70px">
              <button class="btn btn-o">📅 Días atrás</button>
            </form>
          </div>
        </div>"""

    msg_html = (f'<div class="nota" style="border-left-color:#0d5c3d">{esc(msg)}</div>'
                if msg else "")

    return Response(_editor_head("Ajuste de Fechas") + f"""
<div class="bar">📅 Ajuste de Fechas — Morelos
  <span style="margin-left:auto">
    <a href="/admin/editor">Editor de Tablas</a> &nbsp;·&nbsp; <a href="/admin">Panel</a>
  </span>
</div>
<div class="wrap">
  <div class="nota">
    Cambia las fechas de cualquier folio sin restricción: <strong>pasadas o
    futuras</strong>. Útil para permisos retroactivos o para pruebas.
  </div>
  {msg_html}
  <div class="card">
    <form method="GET">
      <div class="toolbar">
        <input type="text" name="folio" value="{esc(folio_buscar)}"
               placeholder="{FOLIO_NUM_PREFIJO}1234" style="min-width:220px">
        <button class="btn btn-p" type="submit">Buscar folio</button>
      </div>
    </form>
  </div>
  {ficha}
</div>
<div class="toast" id="toast"></div>
<script>{_EDITOR_JS}</script>
</body></html>""", mimetype="text/html")


# ===================== TABLAS BD (rutas viejas, con plantillas) ===============
# Se conservan por si tus plantillas siguen enlazándolas.
# El editor nuevo de /admin/editor es más completo y no necesita plantillas.

@app.route('/admin_tablas')
def admin_tablas():
    if not session.get('admin'):
        return redirect(url_for('login'))
    return render_template('admin_tablas.html', tablas=TABLAS_DISPONIBLES)


@app.route('/admin_tabla/<nombre_tabla>')
def admin_tabla(nombre_tabla):
    if not session.get('admin'):
        return redirect(url_for('login'))
    # Redirige al editor nuevo, que sí permite editar celda por celda
    return redirect(url_for('admin_editor_tabla', nombre_tabla=nombre_tabla))


@app.route('/admin_editar_registro/<nombre_tabla>/<registro_id>', methods=['GET', 'POST'])
def admin_editar_registro(nombre_tabla, registro_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        return redirect(url_for('admin_editor'))

    info   = TABLAS_DISPONIBLES[nombre_tabla]
    pk_col = info['pk_col']

    if request.method == 'POST':
        datos = {}
        for columna in info['columnas']:
            if columna in request.form:
                valor = request.form[columna].strip()
                if valor:
                    datos[columna] = valor
        try:
            supabase.table(nombre_tabla).update(datos).eq(pk_col, registro_id).execute()
            flash('Registro actualizado correctamente', 'success')
            return redirect(url_for('admin_editor_tabla', nombre_tabla=nombre_tabla))
        except Exception as e:
            flash(f'Error al actualizar: {e}', 'error')

    try:
        registro = supabase.table(nombre_tabla).select("*").eq(pk_col, registro_id).execute().data
        if not registro:
            flash('Registro no encontrado', 'error')
            return redirect(url_for('admin_editor_tabla', nombre_tabla=nombre_tabla))
        registro = registro[0]
    except Exception as e:
        flash(f'Error al cargar registro: {e}', 'error')
        return redirect(url_for('admin_editor_tabla', nombre_tabla=nombre_tabla))

    return render_template('admin_editar_registro.html',
                           nombre_tabla=nombre_tabla, info_tabla=info,
                           registro=registro, registro_id=registro_id)


@app.route('/admin_eliminar_registro/<nombre_tabla>/<registro_id>', methods=['POST'])
def admin_eliminar_registro(nombre_tabla, registro_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        return redirect(url_for('admin_editor'))
    pk_col = TABLAS_DISPONIBLES[nombre_tabla]['pk_col']
    try:
        supabase.table(nombre_tabla).delete().eq(pk_col, registro_id).execute()
        flash('Registro eliminado correctamente', 'success')
    except Exception as e:
        flash(f'Error al eliminar: {e}', 'error')
    return redirect(url_for('admin_editor_tabla', nombre_tabla=nombre_tabla))


@app.route('/admin_agregar_registro/<nombre_tabla>', methods=['GET', 'POST'])
def admin_agregar_registro(nombre_tabla):
    if not session.get('admin'):
        return redirect(url_for('login'))
    if nombre_tabla not in TABLAS_DISPONIBLES:
        return redirect(url_for('admin_editor'))
    info = TABLAS_DISPONIBLES[nombre_tabla]

    if request.method == 'POST':
        datos = {}
        for columna in info['columnas']:
            if columna != 'id' and columna in request.form:
                valor = request.form[columna].strip()
                if valor:
                    datos[columna] = valor
        try:
            supabase.table(nombre_tabla).insert(datos).execute()
            flash('Registro agregado correctamente', 'success')
            return redirect(url_for('admin_editor_tabla', nombre_tabla=nombre_tabla))
        except Exception as e:
            flash(f'Error al agregar: {e}', 'error')

    return render_template('admin_agregar_registro.html',
                           nombre_tabla=nombre_tabla, info_tabla=info)


# ===================== TIMERS DEL BOT (nuevo con la fusión) ===================
@app.route('/admin/timers_bot')
def admin_timers_bot():
    if not session.get('admin'):
        return redirect(url_for('login'))
    try:
        import bot_morelos
        activos = bot_morelos.snapshot_timers()
    except Exception as e:
        logger.error(f"[TIMERS BOT] {e}")
        activos = []

    filas = "".join(
        f"<tr><td><strong>{t['folio']}</strong></td><td>{t['nombre']}</td>"
        f"<td>{t['restante']}</td>"
        f"<td><form method='POST' action='/admin/timer_bot_detener/{t['folio']}' style='display:inline'>"
        f"<button class='del' onclick=\"return confirm('¿Detener timer de {t['folio']}?')\">Detener</button>"
        f"</form></td></tr>"
        for t in activos
    ) or "<tr><td colspan='4' style='text-align:center;color:#999;padding:22px'>Sin timers activos</td></tr>"

    return Response(_editor_head("Timers del Bot") + f"""
<div class="bar">⏱️ Timers del Bot — Morelos
  <span style="margin-left:auto"><a href="/admin">← Panel</a></span>
</div>
<div class="wrap">
  <div class="nota">
    Timers de 36h de los folios generados por el bot de Telegram. Ahora que el
    bot y el panel corren en el mismo servicio, puedes detenerlos desde aquí.
  </div>
  <div class="tabla-wrap">
    <table><thead><tr><th>Folio</th><th>Titular</th><th>Restante</th><th>Acción</th></tr></thead>
    <tbody>{filas}</tbody></table>
  </div>
</div></body></html>""", mimetype="text/html")


@app.route('/admin/timer_bot_detener/<folio>', methods=['POST'])
def admin_timer_bot_detener(folio):
    if not session.get('admin'):
        return redirect(url_for('login'))
    try:
        import bot_morelos
        bot_morelos.cancelar_timer_folio(folio.strip())
        supabase.table("folios_registrados").update(
            {"estado": "TIMER_DETENIDO"}).eq("folio", folio.strip()).execute()
    except Exception as e:
        logger.error(f"[TIMER BOT] {e}")
    return redirect(url_for('admin_timers_bot'))
