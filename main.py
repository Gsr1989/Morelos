from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
from supabase import create_client, Client
import fitz    # PyMuPDF para manipular PDFs
import os

app = Flask(__name__)
app.secret_key = 'clave_muy_segura_123456'

SUPABASE_URL = "https://xsagwqepoljfsogusubw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwbXre2O2_JStqzls_8Gws"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@app.route('/')
def inicio():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        # login admin
        if u == 'Gsr89roja.' and p == 'serg890105':
            session['admin'] = True
            return redirect(url_for('admin'))
        # login usuario
        resp = supabase.table("verificaciondigitalcdmx")\
                       .select("*")\
                       .eq("username", u).eq("password", p).execute()
        if resp.data:
            session['user_id']   = resp.data[0]['id']
            session['username']  = resp.data[0]['username']
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
        u   = request.form['username']
        p   = request.form['password']
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
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])

        # validar duplicado
        if supabase.table("folios_registrados")\
           .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe','error')
            return redirect(url_for('registro_usuario'))

        # validar folios disponibles
        usuario = supabase.table("verificaciondigitalcdmx")\
                          .select("folios_asignac,folios_usados")\
                          .eq("id", user_id).execute().data[0]
        restantes = usuario['folios_asignac'] - usuario['folios_usados']
        if restantes <= 0:
            flash("No tienes folios disponibles",'error')
            return redirect(url_for('registro_usuario'))

        # fechas
        fecha_exp = datetime.now()
        fecha_ven = fecha_exp + timedelta(days=vigencia)

        # insertar BD
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
        # actualizar contador
        supabase.table("verificaciondigitalcdmx").update({
            "folios_usados": usuario["folios_usados"] + 1
        }).eq("id", user_id).execute()

        flash('Folio registrado correctamente.','success')
        return redirect(url_for('registro_usuario'))

    # GET: muestro form
    folios_info = supabase.table("verificaciondigitalcdmx")\
                         .select("folios_asignac,folios_usados")\
                         .eq("id", user_id).execute().data[0]
    return render_template('registro_usuario.html', folios_info=folios_info)


@app.route('/registro_admin', methods=['GET','POST'])
def registro_admin():
    if 'admin' not in session:
        return redirect(url_for('login'))

    if request.method=='POST':
        # 1) leo form
        folio        = request.form['folio']
        marca        = request.form['marca']
        linea        = request.form['linea']
        anio         = request.form['anio']
        numero_serie = request.form['serie']
        numero_motor = request.form['motor']
        vigencia     = int(request.form['vigencia'])
        nombre       = request.form.get('nombre','')[:50]  # nuevo campo

        # 2) validación duplicado
        if supabase.table("folios_registrados")\
           .select("*").eq("folio", folio).execute().data:
            flash('Error: folio ya existe','error')
            return render_template('registro_admin.html')

        # 3) calcular fechas
        fecha_exp = datetime.now()
        fecha_ven = fecha_exp + timedelta(days=vigencia)

        # 4) insertar en BD
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

        # 5) generar PDF
        try:
            plantilla = "morelosvergas1.pdf"
            doc = fitz.open(plantilla)
            page = doc[0]

            # insertar Nombre
            page.insert_text((155, 245), nombre,
                             fontsize=18, fontname="helv", color=(0, 0, 0))
            # insertar Folio (solo números)
            page.insert_text((1045, 205), folio,
                             fontsize=20, fontname="helv", color=(0, 0, 0))
            # insertar Fecha
            page.insert_text((1045, 275), fecha_exp.strftime("%d/%m/%Y"),
                             fontsize=20, fontname="helv", color=(0, 0, 0))
            # insertar Hora
            page.insert_text((1045, 348), fecha_exp.strftime("%H:%M"),
                             fontsize=20, fontname="helv", color=(0, 0, 0))

            os.makedirs("documentos", exist_ok=True)
            salida = f"documentos/{folio}.pdf"
            doc.save(salida)
            doc.close()
        except Exception as e:
            flash(f"Error al generar PDF: {e}", 'error')
            return render_template('registro_admin.html')

        # 6) éxito: muestro botón de descarga
        return render_template("exitoso.html",
                               folio=folio,
                               enlace_pdf=url_for('descargar_pdf', folio=folio))

    # GET
    return render_template('registro_admin.html')


@app.route('/consulta_folio', methods=['GET','POST'])
def consulta_folio():
    resultado = None
    if request.method == 'POST':
        folio = request.form['folio']
        resp  = supabase.table("folios_registrados")\
                        .select("*").eq("folio", folio).execute()
        if not resp.data:
            resultado = {"estado":"No registrado","folio":folio}
        else:
            reg = resp.data[0]
            fe = datetime.fromisoformat(reg['fecha_expedicion'])
            fv = datetime.fromisoformat(reg['fecha_vencimiento'])
            hoy= datetime.now()
            estado = "VIGENTE" if hoy<=fv else "VENCIDO"
            resultado = {
                "estado": estado,
                "folio": folio,
                "fecha_expedicion": fe.strftime("%d/%m/%Y"),
                "fecha_vencimiento": fv.strftime("%d/%m/%Y"),
                "marca": reg['marca'],
                "linea": reg['linea'],
                "año": reg['anio'],
                "numero_serie": reg['numero_serie'],
                "numero_motor": reg['numero_motor']
            }
        return render_template("resultado_consulta.html", resultado=resultado)
    return render_template("consulta_folio.html")


@app.route('/admin_folios')
def admin_folios():
    if 'admin' not in session:
        return redirect(url_for('login'))
    resp = supabase.table("folios_registrados").select("*").execute()
    return render_template("admin_folios.html", folios=resp.data or [])


@app.route('/editar_folio/<folio>', methods=['GET','POST'])
def editar_folio(folio):
    if 'admin' not in session:
        return redirect(url_for('login'))
    if request.method=='POST':
        supabase.table("folios_registrados").update({
            "marca":request.form['marca'],
            "linea":request.form['linea'],
            "anio":request.form['anio'],
            "numero_serie":request.form['numero_serie'],
            "numero_motor":request.form['numero_motor'],
            "fecha_expedicion":request.form['fecha_expedicion'],
            "fecha_vencimiento":request.form['fecha_vencimiento']
        }).eq("folio", folio).execute()
        flash("Folio actualizado","success")
        return redirect(url_for('admin_folios'))
    reg = supabase.table("folios_registrados").select("*")\
                   .eq("folio",folio).execute().data
    if not reg:
        flash("No encontrado","error")
        return redirect(url_for('admin_folios'))
    return render_template("editar_folio.html", folio=reg[0])


@app.route('/eliminar_folio', methods=['POST'])
def eliminar_folio():
    if 'admin' not in session:
        return redirect(url_for('login'))
    folio = request.form['folio']
    supabase.table("folios_registrados").delete().eq("folio",folio).execute()
    flash("Folio eliminado","success")
    return redirect(url_for('admin_folios'))


@app.route('/descargar_pdf/<folio>')
def descargar_pdf(folio):
    return send_file(f"documentos/{folio}.pdf",
                     as_attachment=True,
                     download_name=f"{folio}.pdf")


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__=='__main__':
    app.run(debug=True)
