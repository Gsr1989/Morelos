from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import pytz
import fitz
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6"
    "InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6"
    "MjA1OTUzOTc1NX0."
    "NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Zona horaria CDMX/Morelos
TZ_MX = pytz.timezone("America/Mexico_City")

# ----------------------------------------------------------------------
# Función para generar PDF (plantilla morelosvergas1.pdf)
# ----------------------------------------------------------------------
def generar_pdf(folio: str, numero_serie: str) -> str:
    plantilla = "morelosvergas1.pdf"
    doc = fitz.open(plantilla)
    page = doc[0]

    ahora = datetime.now(TZ_MX)
    # Inserta folio, fecha y hora en la plantilla
    page.insert_text((1045, 205), folio,               fontsize=20, fontname="helv")
    page.insert_text((1045, 275), ahora.strftime("%d/%m/%Y"), fontsize=20, fontname="helv")
    page.insert_text((1045, 348), ahora.strftime("%H:%M:%S"), fontsize=20, fontname="helv")

    os.makedirs("documentos", exist_ok=True)
    ruta = f"documentos/{folio}.pdf"
    doc.save(ruta)
    doc.close()
    return ruta

# ----------------------------------------------------------------------
# RUTAS
# ----------------------------------------------------------------------
@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # Admin hardcode
        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))
        # Usuario Supabase
        resp = supabase.table("verificaciondigitalcdmx") \
                      .select("*") \
                      .eq("username", username) \
                      .eq("password", password) \
                      .execute()
        if resp.data:
            session['user_id'] = resp.data[0]['id']
            session['username'] = resp.data[0]['username']
            return redirect(url_for('registro_usuario'))
        flash('Credenciales incorrectas', 'error')
    return render_template('login.html')

@app.route('/admin')
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    return render_template('panel.html')

@app.route('/crear_usuario', methods=['GET','POST'])
def crear_usuario():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        fol = int(request.form['folios'])
        exists = supabase.table("verificaciondigitalcdmx") \
                         .select("id") \
                         .eq("username", u) \
                         .execute()
        if exists.data:
            flash('Error: ese usuario ya existe', 'error')
        else:
            supabase.table("verificaciondigitalcdmx").insert({
                "username": u,
                "password": p,
                "folios_asignac": fol,
                "folios_usados": 0
            }).execute()
            flash('Usuario creado', 'success')
    return render_template('crear_usuario.html')

@app.route('/registro_usuario', methods=['GET','POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']

    if request.method == 'POST':
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])

        # 1) Validar duplicado
        if supabase.table("folios_registrados") \
                  .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe', 'error')
            return redirect(url_for('registro_usuario'))

        # 2) Verificar folios disponibles
        ui = supabase.table("verificaciondigitalcdmx") \
                     .select("folios_asignac,folios_usados") \
                     .eq("id", user_id).execute().data[0]
        if ui['folios_asignac'] - ui['folios_usados'] <= 0:
            flash('No tienes folios disponibles', 'error')
            return redirect(url_for('registro_usuario'))

        # 3) Fechas con zona horaria
        fecha_exp = datetime.now(TZ_MX)
        fecha_ven = fecha_exp + timedelta(days=vigencia)

        # 4) Insert en DB
        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_exp.isoformat(),
            "fecha_vencimiento": fecha_ven.isoformat()
        }).execute()

        # 5) Actualizar contador
        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": ui['folios_usados'] + 1
        }).eq("id", user_id).execute()

        # 6) Generar PDF y mostrar éxito con botón de descarga
        pdf_path = generar_pdf(folio, numero_serie)
        return render_template(
            "exitoso.html",
            folio=folio,
            serie=numero_serie,
            fecha_generacion=fecha_exp.strftime("%d/%m/%Y %H:%M:%S"),
            enlace_pdf=url_for('descargar_pdf', folio=folio),
            volver_url=url_for('registro_usuario')
        )

    # GET: info de folios disponibles
    info = supabase.table("verificaciondigitalcdmx") \
                  .select("folios_asignac,folios_usados") \
                  .eq("id", user_id).execute().data[0]
    return render_template('registro_usuario.html', folios_info=info)

@app.route('/registro_admin', methods=['GET','POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])
        nombre       = request.form.get('nombre', '')[:50]

        # Validar duplicado
        if supabase.table("folios_registrados") \
                  .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe', 'error')
            return render_template('registro_admin.html')

        fecha_exp = datetime.now(TZ_MX)
        fecha_ven = fecha_exp + timedelta(days=vigencia)

        # Insert en DB
        supabase.table("folios_registrados").insert({
            "folio": folio,
            "marca": marca,
            "linea": linea,
            "anio": anio,
            "numero_serie": numero_serie,
            "numero_motor": numero_motor,
            "fecha_expedicion": fecha_exp.isoformat(),
            "fecha_vencimiento": fecha_ven.isoformat()
        }).execute()

        # Generar PDF con datos de admin
        try:
            doc = fitz.open("morelosvergas1.pdf")
            page = doc[0]
            page.insert_text((155, 245), nombre,                  fontsize=18, fontname="helv")
            page.insert_text((1045, 205), folio,                  fontsize=20, fontname="helv")
            page.insert_text((1045, 275), fecha_exp.strftime("%d/%m/%Y"), fontsize=20, fontname="helv")
            page.insert_text((1045, 348), fecha_exp.strftime("%H:%M:%S"), fontsize=20, fontname="helv")
            os.makedirs("documentos", exist_ok=True)
            salida = f"documentos/{folio}.pdf"
            doc.save(salida)
            doc.close()
        except Exception as e:
            flash(f"Error al generar PDF: {e}", 'error')
            return render_template('registro_admin.html')

        return render_template(
            "exitoso.html",
            folio=folio,
            serie=numero_serie,
            fecha_generacion=fecha_exp.strftime("%d/%m/%Y %H:%M:%S"),
            enlace_pdf=url_for('descargar_pdf', folio=folio),
            volver_url=url_for('admin')
        )

    return render_template('registro_admin.html')

@app.route('/consulta_folio', methods=['GET','POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio']
        regs = supabase.table("folios_registrados").select("*").eq("folio", folio).execute().data
        if not regs:
            resultado = {"estado": "NO SE ENCUENTRA REGISTRADO", "folio": folio}
        else:
            r    = regs[0]
            fexp = datetime.fromisoformat(r['fecha_expedicion'])
            fven = datetime.fromisoformat(r['fecha_vencimiento']).astimezone(TZ_MX)
            ahora = datetime.now(TZ_MX)
            estado = "VIGENTE" if ahora <= fven else "VENCIDO"
            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": fexp.strftime("%d/%m/%Y"),
                "fecha_vencimiento": fven.strftime("%d/%m/%Y"),
                "marca": r['marca'],
                "linea": r['linea'],
                "año": r['anio'],
                "numero_serie": r['numero_serie'],
                "numero_motor": r['numero_motor']
            }
        return render_template("resultado_consulta.html", resultado=resultado)
    return render_template("consulta_folio.html")

@app.route('/admin_folios')
def admin_folios():
    if 'admin' not in session:
        return redirect(url_for('login'))
    filtro        = request.args.get('filtro', '').strip()
    criterio      = request.args.get('criterio', 'folio')
    ordenar       = request.args.get('ordenar', 'desc')
    estado_filtro = request.args.get('estado', 'todos')
    fi            = request.args.get('fecha_inicio', '')
    ff            = request.args.get('fecha_fin', '')

    q = supabase.table("folios_registrados").select("*")
    if filtro:
        campo = "folio" if criterio == "folio" else "numero_serie"
        q = q.ilike(campo, f"%{filtro}%")

    datos = q.execute().data or []
    ahora = datetime.now(TZ_MX)
    out   = []
    for f in datos:
        try:
            fe = datetime.fromisoformat(f["fecha_expedicion"])
            fv = datetime.fromisoformat(f["fecha_vencimiento"])
        except:
            continue
        est = "VIGENTE" if ahora <= fv else "VENCIDO"
        f["estado"] = est
        if estado_filtro == "vigente" and est != "VIGENTE": continue
        if estado_filtro == "vencido"  and est != "VENCIDO": continue
        if fi:
            try:
                if fe < datetime.strptime(fi, "%Y-%m-%d"): continue
            except: pass
        if ff:
            try:
                if fe > datetime.strptime(ff, "%Y-%m-%d"): continue
            except: pass
        out.append(f)

    out.sort(key=lambda x: x["fecha_expedicion"], reverse=(ordenar=="desc"))
    return render_template(
        "admin_folios.html",
        folios=out,
        filtro=filtro,
        criterio=criterio,
        ordenar=ordenar,
        estado=estado_filtro,
        fecha_inicio=fi,
        fecha_fin=ff
    )

@app.route('/editar_folio/<folio>', methods=['GET','POST'])
def editar_folio(folio):
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        data = {
            "marca": request.form['marca'],
            "linea": request.form['linea'],
            "anio": request.form['anio'],
            "numero_serie": request.form['numero_serie'],
            "numero_motor": request.form['numero_motor'],
            "fecha_expedicion": request.form['fecha_expedicion'],
            "fecha_vencimiento": request.form['fecha_vencimiento']
        }
        supabase.table("folios_registrados").update(data).eq("folio", folio).execute()
        flash("Folio actualizado correctamente", "success")
        return redirect(url_for('admin_folios'))

    reg = supabase.table("folios_registrados").select("*").eq("folio", folio).execute().data
    if not reg:
        flash("Folio no encontrado", "error")
        return redirect(url_for('admin_folios'))
    return render_template("editar_folio.html", folio=reg[0])

@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if 'admin' not in session:
        return redirect(url_for('login'))
    folio = request.form['folio']
    supabase.table("folios_registrados").delete().eq("folio", folio).execute()
    flash("Folio eliminado correctamente", "success")
    return redirect(url_for('admin_folios'))

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    path = f"documentos/{folio}.pdf"
    return send_file(path, as_attachment=True, download_name=f"{folio}.pdf")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
