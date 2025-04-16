from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz    # PyMuPDF para manipular PDFs
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def inicio():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # admin hardcode
        if username == 'Gsr89roja.' and password == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))
        # usuario supabase
        resp = supabase.table("verificaciondigitalcdmx")\
                       .select("*")\
                       .eq("username", username).eq("password", password)\
                       .execute()
        if resp.data:
            session['user_id'] = resp.data[0]['id']
            session['username'] = resp.data[0]['username']
            return redirect(url_for('registro_usuario'))
        flash('Credenciales incorrectas','error')
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
    if request.method=='POST':
        u = request.form['username']
        p = request.form['password']
        fol = int(request.form['folios'])
        exists = supabase.table("verificaciondigitalcdmx")\
                        .select("id").eq("username", u).execute()
        if exists.data:
            flash('Error: ese usuario ya existe','error')
        else:
            supabase.table("verificaciondigitalcdmx").insert({
                "username":u,
                "password":p,
                "folios_asignac":fol,
                "folios_usados":0
            }).execute()
            flash('Usuario creado','success')
    return render_template('crear_usuario.html')

@app.route('/registro_usuario', methods=['GET','POST'])
def registro_usuario():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method=='POST':
        # igual que antes pero sin PDF
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])
        # duplica folio?
        if supabase.table("folios_registrados")\
           .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe','error')
            return redirect(url_for('registro_usuario'))
        # folios disponibles
        ui = supabase.table("verificaciondigitalcdmx")\
                     .select("folios_asignac,folios_usados")\
                     .eq("id", user_id).execute().data[0]
        if ui['folios_asignac'] - ui['folios_usados'] <= 0:
            flash('No tienes folios disponibles','error')
            return redirect(url_for('registro_usuario'))
        # fechas
        fecha_exp = datetime.now()
        fecha_ven = fecha_exp + timedelta(days=vigencia)
        # insert
        supabase.table("folios_registrados").insert({
            "folio":folio,
            "marca":marca,
            "linea":linea,
            "anio":anio,
            "numero_serie":numero_serie,
            "numero_motor":numero_motor,
            "fecha_expedicion":fecha_exp.isoformat(),
            "fecha_vencimiento":fecha_ven.isoformat()
        }).execute()
        # actualizar usados
        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": ui['folios_usados']+1
        }).eq("id", user_id).execute()
        flash('Folio registrado correctamente','success')
        return redirect(url_for('registro_usuario'))
    fi = supabase.table("verificaciondigitalcdmx")\
                .select("folios_asignac,folios_usados")\
                .eq("id", user_id).execute().data[0]
    return render_template('registro_usuario.html', folios_info=fi)

@app.route('/registro_admin', methods=['GET','POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method=='POST':
        # 1) Leemos form
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])
        nombre       = request.form.get('nombre','')[:50]

        # 2) Validar duplicado
        if supabase.table("folios_registrados")\
           .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe','error')
            return render_template('registro_admin.html')

        # 3) Fechas
        fecha_exp  = datetime.now()
        fecha_ven  = fecha_exp + timedelta(days=vigencia)

        # 4) Insert BD
        supabase.table("folios_registrados").insert({
            "folio":folio,
            "marca":marca,
            "linea":linea,
            "anio":anio,
            "numero_serie":numero_serie,
            "numero_motor":numero_motor,
            "fecha_expedicion":fecha_exp.isoformat(),
            "fecha_vencimiento":fecha_ven.isoformat()
        }).execute()

        # 5) Generar PDF con morelosvergas1.pdf
        try:
            doc = fitz.open("morelosvergas1.pdf")
            page = doc[0]

            # 1) Nombre → (155, 245), fuente 18
            page.insert_text((155,245), nombre,
                             fontsize=18, fontname="helv", color=(0,0,0))
            # 2) Folio → (1045, 205), fuente 20
            page.insert_text((1045,205), numero_serie,
                             fontsize=20, fontname="helv", color=(0,0,0))
            # 3) Fecha → (1045, 275), fuente 20
            page.insert_text((1045,275), fecha_exp.strftime("%d/%m/%Y"),
                             fontsize=20, fontname="helv", color=(0,0,0))
            # 4) Hora → (1045, 348), fuente 20
            page.insert_text((1045,348), fecha_exp.strftime("%H:%M"),
                             fontsize=20, fontname="helv", color=(0,0,0))

            os.makedirs("documentos", exist_ok=True)
            salida = f"documentos/{folio}.pdf"
            doc.save(salida)
            doc.close()
        except Exception as e:
            flash(f"Error al generar PDF: {e}",'error')
            return render_template('registro_admin.html')

        # 6) Éxito con enlace de descarga
        return render_template("exitoso.html",
                               folio=folio,
                               enlace_pdf=url_for('descargar_pdf', folio=folio))
    # GET
    return render_template('registro_admin.html')

@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    path = f"documentos/{folio}.pdf"
    return send_file(path, as_attachment=True, download_name=f"{folio}.pdf")

# ... resto de rutas (consulta_folio, admin_folios, editar, eliminar, logout) idénticas a tu versión anterior ...

if __name__ == '__main__':
    app.run(debug=True)
