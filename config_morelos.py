"""
MORELOS — Configuración compartida entre el panel web (Flask) y el bot (aiogram).

Aquí vive una sola vez todo lo que estaba duplicado: cliente de Supabase,
coordenadas del PDF, constantes, los candados globales y — muy importante —
UN SOLO generador de folios 456.
"""

import os
import sys
import logging
import string
import threading
from datetime import datetime, date
from zoneinfo import ZoneInfo
from supabase import create_client, Client

# ===================== LOGGING =====================
sys.dont_write_bytecode = True
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("morelos")

# ===================== ZONA HORARIA =====================
TZ_MORELOS = ZoneInfo("America/Mexico_City")
TZ_MEXICO  = TZ_MORELOS      # alias que usaba el bot


def now_morelos() -> datetime:
    return datetime.now(TZ_MORELOS)


def today_morelos() -> date:
    return now_morelos().date()


def parse_date_any(value) -> date:
    import re
    if not value:
        raise ValueError("Fecha vacía")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ_MORELOS)
        else:
            value = value.astimezone(TZ_MORELOS)
        return value.date()
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_MORELOS)
    else:
        dt = dt.astimezone(TZ_MORELOS)
    return dt.date()


# ===================== SUPABASE =====================
SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://xsagwqepoljfsogusubw.supabase.co"
)
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzYWd3cWVwb2xqZnNvZ3VzdWJ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM5NjM3NTUsImV4cCI6MjA1OTUzOTc1NX0.NUixULn0m2o49At8j6X58UqbXre2O2_JStqzls_8Gws"
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===================== CONFIG GENERAL =====================
BOT_TOKEN            = os.getenv("BOT_TOKEN", "")
BASE_URL             = os.getenv(
    "BASE_URL",
    "https://morelosgobmovilidad-y-transporte.onrender.com"
).rstrip("/")
URL_CONSULTA_BASE    = BASE_URL      # el QR apunta al mismo servicio fusionado

OUTPUT_DIR           = "documentos"
PLANTILLA_PRINCIPAL  = "morelos_hoja1_imagen.pdf"
PLANTILLA_SECUNDARIA = "morelosvergas1.pdf"

ENTIDAD              = "morelos"
DIAS_PERMISO         = 30
HORAS_TIMER_BOT      = 36
PRECIO_PERMISO       = 200
PAGE_SIZE            = 100

ADMIN_USER           = os.getenv("ADMIN_USER", "Serg890105tm3")
ADMIN_PASS           = os.getenv("ADMIN_PASS", "Serg890105tm3")
SECRET_KEY           = os.getenv("SECRET_KEY", "morelos_segura_123456")

# ⚠️ PANEL Y BOT COMPARTEN LA SERIE 456.
# Antes cada uno la generaba distinto:
#   · Panel: escaneaba TODOS los folios de la entidad (lento y sin watermark)
#   · Bot:   usaba el watermark "MOR"
# Con dos procesos eso ya era riesgoso; juntos sería peor. Ahora hay UN solo
# generador (abajo) con watermark y candado.
FOLIO_NUM_PREFIJO    = "456"
FOLIO_WATERMARK_KEY  = "MOR"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===================== CANDADOS GLOBALES =====================
# ⚠️ PDF_LOCK — NINGUNO de los dos archivos tenía candado de PDF. PyMuPDF no es
# thread-safe: el panel genera en hilos (threading.Thread) y el bot en el
# threadpool de asyncio. Al compartir proceso, sin este candado dos permisos
# simultáneos se corrompen entre sí. Es el fix más importante de esta fusión.
PDF_LOCK   = threading.Lock()

# Serializa la asignación de folios 456 entre panel y bot
FOLIO_LOCK = threading.Lock()

# Serializa la asignación de placas digitales
PLACA_LOCK = threading.Lock()

# ===================== COORDENADAS PDF MORELOS =====================
COORDS_MORELOS = {
    "folio":       (665, 282, 18, (1, 0, 0)),
    "placa":       (200, 200, 60, (0, 0, 0)),
    "fecha":       (200, 340, 14, (0, 0, 0)),
    "vigencia":    (600, 340, 14, (0, 0, 0)),
    "marca":       (110, 425, 14, (0, 0, 0)),
    "serie":       (460, 420, 14, (0, 0, 0)),
    "linea":       (110, 455, 14, (0, 0, 0)),
    "motor":       (460, 445, 14, (0, 0, 0)),
    "anio":        (110, 485, 14, (0, 0, 0)),
    "color":       (460, 395, 14, (0, 0, 0)),
    "tipo":        (510, 470, 14, (0, 0, 0)),
    "nombre":      (150, 370, 14, (0, 0, 0)),
    "fecha_hoja2": (126, 310, 15, (0, 0, 0)),
    "qr_hoja1":    (400, 500, 70, 70),
}

# ===================== TABLAS EDITABLES DESDE EL PANEL =====================
# Cada tabla que quieras administrar desde tu página va aquí.
# 'pk_col' es la columna que identifica el renglón (para editar/borrar).
TABLAS_DISPONIBLES = {
    'folios_registrados': {
        'nombre':   'Folios Registrados',
        'pk_col':   'folio',
        'columnas': ['folio', 'marca', 'linea', 'anio', 'numero_serie', 'numero_motor',
                     'color', 'tipo', 'nombre', 'fecha_expedicion', 'fecha_vencimiento',
                     'entidad', 'estado', 'creado_por'],
    },
    'usuarios_morelos': {
        'nombre':   'Usuarios del Sistema',
        'pk_col':   'id',
        'columnas': ['id', 'username', 'password', 'folios_asignados', 'folios_usados'],
    },
    'folio_watermark': {
        'nombre':   'Watermark de Folios y Placas',
        'pk_col':   'prefijo',
        'columnas': ['prefijo', 'ultimo_asignado'],
    },
    'borradores_registros': {
        'nombre':   'Borradores (del bot)',
        'pk_col':   'folio',
        'columnas': ['folio', 'entidad', 'numero_serie', 'marca', 'linea', 'numero_motor',
                     'anio', 'color', 'fecha_expedicion', 'fecha_vencimiento',
                     'contribuyente', 'estado', 'user_id'],
    },
}

# Columnas que el editor trata como fecha (le pone selector de calendario)
COLUMNAS_FECHA = {
    'fecha_expedicion', 'fecha_vencimiento', 'fecha_comprobante',
    'fecha_detencion', 'created_at',
}


# ===================== GENERADOR DE FOLIO COMPARTIDO =====================
def _leer_watermark() -> int | None:
    try:
        r = supabase.table("folio_watermark") \
            .select("ultimo_asignado").eq("prefijo", FOLIO_WATERMARK_KEY).execute()
        return r.data[0]["ultimo_asignado"] if r.data else None
    except Exception as e:
        logger.error(f"[WATERMARK] leer: {e}")
        return None


def _guardar_watermark(numero: int):
    try:
        supabase.table("folio_watermark").upsert({
            "prefijo":         FOLIO_WATERMARK_KEY,
            "ultimo_asignado": numero
        }).execute()
    except Exception as e:
        logger.error(f"[WATERMARK] guardar: {e}")


def generar_folio_morelos() -> str:
    """
    ÚNICO generador de folios 456 — lo usan el panel Y el bot.
    Bloques de 500 con una sola consulta (.in_) + watermark + candado.
    """
    with FOLIO_LOCK:
        wm = _leer_watermark()
        if wm is not None:
            inicio = wm + 1
        else:
            # Primera vez: buscar el máximo existente en la BD
            try:
                r = supabase.table("folios_registrados").select("folio") \
                    .eq("entidad", ENTIDAD).like("folio", f"{FOLIO_NUM_PREFIJO}%").execute()
                nums = []
                for row in r.data or []:
                    f = str(row.get("folio", ""))
                    if f.startswith(FOLIO_NUM_PREFIJO):
                        suf = f[len(FOLIO_NUM_PREFIJO):]
                        if suf.isdigit():
                            nums.append(int(suf))
                inicio = (max(nums) + 1) if nums else 1
            except Exception as e:
                logger.error(f"[FOLIO] init: {e}")
                inicio = 1

        BLOQUE = 500
        for _ in range(0, 10_000_000, BLOQUE):
            candidatos = [f"{FOLIO_NUM_PREFIJO}{inicio + i}" for i in range(BLOQUE)]
            try:
                resp = supabase.table("folios_registrados") \
                    .select("folio").in_("folio", candidatos).execute()
                ocupados = {r["folio"] for r in (resp.data or [])}
            except Exception as e:
                logger.error(f"[FOLIO] bloque: {e}")
                ocupados = set()

            logger.info(f"[FOLIO] bloque {inicio}–{inicio+BLOQUE-1}, ocupados={len(ocupados)}")
            for i, folio in enumerate(candidatos):
                if folio not in ocupados:
                    numero_final = inicio + i
                    _guardar_watermark(numero_final)
                    logger.info(f"[FOLIO] ✅ Asignado: {folio}")
                    return folio
            inicio += BLOQUE

        raise Exception("Sin folio disponible tras 10,000,000 intentos")


def leer_siguiente_folio() -> str:
    """Sólo informativo (para /health) — no reserva nada."""
    wm = _leer_watermark()
    n = (wm + 1) if wm is not None else 1
    return f"{FOLIO_NUM_PREFIJO}{n}"


# ===================== PLACA DIGITAL =====================
_ABC           = string.ascii_uppercase
PLACA_PREFIJO  = "MOR_PLACA"
PLACA_INICIO   = "GZR1999"
_placa_counter = {"ultimo": None}


def placa_a_numero(placa: str) -> int:
    l1 = _ABC.index(placa[0])
    l2 = _ABC.index(placa[1])
    l3 = _ABC.index(placa[2])
    return (l1 * 676 + l2 * 26 + l3) * 10000 + int(placa[3:])


def numero_a_placa(n: int) -> str:
    digitos = n % 10000
    idx     = n // 10000
    l3 = idx % 26
    l2 = (idx // 26) % 26
    l1 = idx // 676
    return f"{_ABC[l1]}{_ABC[l2]}{_ABC[l3]}{digitos:04d}"


def _leer_watermark_placa() -> int | None:
    try:
        r = supabase.table("folio_watermark") \
            .select("ultimo_asignado").eq("prefijo", PLACA_PREFIJO).execute()
        return r.data[0]["ultimo_asignado"] if r.data else None
    except Exception as e:
        logger.error(f"[PLACA] leer watermark: {e}")
        return None


def _guardar_watermark_placa(numero: int):
    try:
        supabase.table("folio_watermark").upsert({
            "prefijo":         PLACA_PREFIJO,
            "ultimo_asignado": numero
        }).execute()
        logger.info(f"[PLACA] Watermark: {numero_a_placa(numero)}")
    except Exception as e:
        logger.error(f"[PLACA] guardar watermark: {e}")


def inicializar_placa():
    if _placa_counter["ultimo"] is not None:
        return
    wm = _leer_watermark_placa()
    if wm is not None:
        _placa_counter["ultimo"] = wm
        logger.info(f"[PLACA] Desde watermark: {numero_a_placa(wm)}")
        return
    # Fallback: archivo local
    try:
        with open("placas_digitales.txt") as f:
            ultima = f.read().strip().split("\n")[-1].strip()
        if ultima and len(ultima) == 7:
            n = placa_a_numero(ultima)
            _placa_counter["ultimo"] = n
            _guardar_watermark_placa(n)
            logger.info(f"[PLACA] Desde archivo local: {ultima}")
            return
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"[PLACA] leyendo archivo: {e}")
    n = placa_a_numero(PLACA_INICIO)
    _placa_counter["ultimo"] = n
    _guardar_watermark_placa(n)
    logger.info(f"[PLACA] Sin historial, empezando en {PLACA_INICIO}")


def generar_placa_morelos() -> str:
    """Siguiente placa. Con candado — lo usan panel y bot."""
    with PLACA_LOCK:
        if _placa_counter["ultimo"] is None:
            inicializar_placa()
        nuevo_n = _placa_counter["ultimo"] + 1
        maximo  = placa_a_numero("ZZZ9999")
        if nuevo_n > maximo:
            nuevo_n = placa_a_numero("AAA0000")
        _placa_counter["ultimo"] = nuevo_n
        nueva = numero_a_placa(nuevo_n)
        _guardar_watermark_placa(nuevo_n)
        try:
            with open("placas_digitales.txt", "a") as f:
                f.write(nueva + "\n")
        except Exception as e:
            logger.warning(f"[PLACA] no se pudo guardar en archivo: {e}")
        logger.info(f"[PLACA] Asignada: {nueva}")
        return nueva


def placa_actual() -> str:
    return numero_a_placa(_placa_counter["ultimo"]) if _placa_counter["ultimo"] else "N/A"
