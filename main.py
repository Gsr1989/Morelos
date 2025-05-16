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

# ENTIDAD FIJA PARA MORELOS
ENTIDAD = "morelos"

TZ_MX = pytz.timezone("America/Mexico_City")

def generar_pdf(folio: str, numero_serie: str, nombre: str) -> str:
    plantilla = "morelosvergas1.pdf"
    doc = fitz.open(plantilla)
    page = doc[0]

    ahora = datetime.now(TZ_MX)
    page.insert_text((155, 245), nombre,                  fontsize=18, fontname="helv")
    page.insert_text((1045, 205), folio,                  fontsize=20, fontname="helv")
    page.insert_text((1045, 275), ahora.strftime("%d/%m/%Y"), fontsize=20, fontname="helv")
    page.insert_text((1045, 348), ahora.strftime("%H:%M:%S"), fontsize=20, fontname="helv")
    os.makedirs("documentos", exist_ok=True)
    ruta = f"documentos/{folio}.pdf"
    doc.save(ruta)
    doc.close()
    return ruta

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))
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
    uid = session['user_id']

    if request.method == 'POST':
        nombre       = request.form.get('nombre','').strip()
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        serie        = request.form['serie']
        motor        = request.form['motor']
        vigencia     = int(request.form['vigencia'])

        if supabase.table("folios_registrados").select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe', 'error')
            return redirect(url_for('registro_usuario'))

        ui = supabase.table("verificaciondigitalcdmx")\
                     .select("folios_asignac,folios_usados")\
                     .eq("id", uid).execute().data[0]
        if ui['folios_asignac'] - ui['folios_usados'] <= 0:
            flash('No tienes folios disponibles', 'error')
            return redirect(url_for('registro_usuario'))

        ahora = datetime.now(TZ_MX)
        venc  = ahora + timedelta(days=vigencia)

        supabase.table("folios_registrados").insert({
            "folio":            folio,
            "marca":            marca,
            "linea":            linea,
            "anio":             anio,
            "numero_serie":     serie,
            "numero_motor":     motor,
            "fecha_expedicion": ahora.isoformat(),
            "fecha_vencimiento":venc.isoformat(),
            "entidad":          ENTIDAD
        }).execute()

        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": ui['folios_usados'] + 1
        }).eq("id", uid).execute()

        pdf_path = generar_pdf(folio, serie, nombre)
        return render_template(
            "exitoso.html",
            folio=folio,
            serie=serie,
            nombre=nombre,
            fecha_generacion=ahora.strftime("%d/%m/%Y %H:%M:%S"),
            enlace_pdf=url_for('descargar_pdf', folio=folio),
            volver_url=url_for('registro_usuario')
        )

    info = supabase.table("verificaciondigitalcdmx")\
                  .select("folios_asignac,folios_usados")\
                  .eq("id", session['user_id']).execute().data[0]
    return render_template('registro_usuario.html', folios_info=info)

@app.route('/registro_admin', methods=['GET','POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        folio    = request.form['folio']
        marca    = request.form['marca']
        linea    = request.form['linea']
        anio     = request.form['anio']
        serie    = request.form['serie']
        motor    = request.form['motor']
        vigencia = int(request.form['vigencia'])
        nombre   = request.form.get('nombre','')[:50]

        if supabase.table("folios_registrados")\
                   .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe', 'error')
            return render_template('registro_admin.html')

        ahora = datetime.now(TZ_MX)
        venc  = ahora + timedelta(days=vigencia)

        supabase.table("folios_registrados").insert({
            "folio":            folio,
            "marca":            marca,
            "linea":            linea,
            "anio":             anio,
            "numero_serie":     serie,
            "numero_motor":     motor,
            "fecha_expedicion": ahora.isoformat(),
            "fecha_vencimiento":venc.isoformat(),
            "entidad":          ENTIDAD
        }).execute()

        try:
            pdf_path = generar_pdf(folio, serie, nombre)
        except Exception as e:
            flash(f"Error al generar PDF: {e}", 'error')
            return render_template('registro_admin.html')

        return render_template(
            "exitoso.html",
            folio=folio,
            serie=serie,
            nombre=nombre,
            fecha_generacion=ahora.strftime("%d/%m/%Y %H:%M:%S"),
            enlace_pdf=url_for('descargar_pdf', folio=folio),
            volver_url=url_for('admin')
        )
    return render_template('registro_admin.html')

@app.route('/consulta_folio', methods=['GET','POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio'].strip().upper()
        row   = supabase.table("folios_registrados")\
                        .select("*").eq("folio", folio).execute().data
        if not row:
            resultado = {"estado":"NO ENCONTRADO","folio":folio}
        else:
            r    = row[0]
            fexp = datetime.fromisoformat(r['fecha_expedicion'])
            fven = datetime.fromisoformat(r['fecha_vencimiento']).astimezone(TZ_MX)
            estado = "VIGENTE" if datetime.now(TZ_MX) <= fven else "VENCIDO"
            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion":  fexp.strftime("%d/%m/%Y"),
                "fecha_vencimiento": fven.strftime("%d/%m/%Y"),
                "marca":  r['marca'],
                "linea":  r['linea'],
                "año":    r['anio'],
                "numero_serie":  r['numero_serie'],
                "numero_motor":  r['numero_motor'],
                "entidad":       r.get('entidad','')
            }
        return render_template("resultado_consulta.html", resultado=resultado)
    return render_template("consulta_folio.html")

@app.route('/admin_folios')
def admin_folios():
    if 'admin' not in session:
        return redirect(url_for('login'))
    filtro        = request.args.get('filtro','').strip()
    criterio      = request.args.get('criterio','folio')
    estado_filtro = request.args.get('estado','todos')
    ordenar       = request.args.get('ordenar','desc')
    fecha_inicio  = request.args.get('fecha_inicio','')
    fecha_fin     = request.args.get('fecha_fin','')
    q = supabase.table("folios_registrados").select("*")
    if filtro:
        campo = "folio" if criterio=="folio" else "numero_serie"
        q = q.ilike(campo, f"%{filtro}%")
    folios = q.execute().data or []
    ahora  = datetime.now(TZ_MX)
    filtrados = []
    for f in folios:
        fe = datetime.fromisoformat(f['fecha_expedicion'])
        fv = datetime.fromisoformat(f['fecha_vencimiento'])
        est = "VIGENTE" if ahora <= fv else "VENCIDO"
        f['estado'] = est
        if estado_filtro!="todos" and f['estado'].lower()!=estado_filtro:
            continue
        if fecha_inicio:
            try:
                if fe < datetime.strptime(fecha_inicio, "%Y-%m-%d"): continue
            except: pass
        if fecha_fin:
            try:
                if fe > datetime.strptime(fecha_fin, "%Y-%m-%d"): continue
            except: pass
        filtrados.append(f)
    filtrados.sort(key=lambda x: x['fecha_expedicion'], reverse=(ordenar=="desc"))
    return render_template(
        "admin_folios.html",
        folios=filtrados,
        filtro=filtro,
        criterio=criterio,
        estado=estado_filtro,
        ordenar=ordenar,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin
    )

@app.route('/editar_folio/<folio>', methods=['GET','POST'])
def editar_folio(folio):
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method=='POST':
        data = {
            "marca":            request.form['marca'],
            "linea":            request.form['linea'],
            "anio":             request.form['anio'],
            "numero_serie":     request.form['numero_serie'],
            "numero_motor":     request.form['numero_motor'],
            "fecha_expedicion": request.form['fecha_expedicion'],
            "fecha_vencimiento":request.form['fecha_vencimiento'],
            "entidad":          ENTIDAD
        }
        supabase.table("folios_registrados").update(data).eq("folio", folio).execute()
        flash("Folio actualizado correctamente","success")
        return redirect(url_for('admin_folios'))
    reg = supabase.table("folios_registrados").select("*").eq("folio",folio).execute().data
    if not reg:
        flash("Folio no encontrado","error")
        return redirect(url_for('admin_folios'))
    return render_template("editar_folio.html", folio=reg[0])

@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if 'admin' not in session:
        return redirect(url_for('login'))
    folio = request.form['folio']
    supabase.table("folios_registrados").delete().eq("folio",folio).execute()
    flash("Folio eliminado correctamente","success")
    return redirect(url_for('admin_folios'))

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    path = f"documentos/{folio}.pdf"
    return send_file(path, as_attachment=True, download_name=f"{folio}.pdf")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__=='__main__':
    app.run(debug=True)
