import os
import sys
import sqlite3
import threading
import webbrowser
from flask import Flask, g, jsonify, request, render_template

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_db_path():
    folder = os.path.join(os.path.expanduser("~"), "Documents", "JCM_Inventario")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "datos.db")


def get_template_folder():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "templates")


def get_static_folder():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "static")


app = Flask(__name__,
            template_folder=get_template_folder(),
            static_folder=get_static_folder())

# ---------------------------------------------------------------------------
# DB connection helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(get_db_path())
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# ---------------------------------------------------------------------------
# Schema & seeding
# ---------------------------------------------------------------------------
# REGLA ARQUITECTÓNICA: entidad_id usa 0 (INTEGER) para referirse a la bodega
# y nunca NULL, ya que SQLite no evalúa NULL=NULL como TRUE en constraints UNIQUE.
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalogo_gases (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS clientes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS proveedores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT NOT NULL UNIQUE,
    tipo_operacion TEXT NOT NULL CHECK(tipo_operacion IN ('intercambio', 'recarga'))
);

CREATE TABLE IF NOT EXISTS inventario_actual (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ubicacion  TEXT    NOT NULL CHECK(ubicacion IN ('bodega', 'cliente', 'proveedor')),
    entidad_id INTEGER NOT NULL DEFAULT 0,
    gas_id     INTEGER NOT NULL REFERENCES catalogo_gases(id),
    propiedad  TEXT    NOT NULL CHECK(propiedad IN ('propio', 'arrendado')),
    estado     TEXT    NOT NULL CHECK(estado IN ('lleno', 'vacio')),
    cantidad   INTEGER NOT NULL DEFAULT 0 CHECK(cantidad >= 0),
    UNIQUE(ubicacion, entidad_id, gas_id, propiedad, estado)
);

CREATE TABLE IF NOT EXISTS historial_movimientos (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha              TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    tipo_movimiento    TEXT    NOT NULL,
    entidad_origen_id  INTEGER,
    entidad_destino_id INTEGER,
    gas_id             INTEGER NOT NULL REFERENCES catalogo_gases(id),
    propiedad          TEXT    NOT NULL,
    estado_movido      TEXT    NOT NULL,
    cantidad           INTEGER NOT NULL,
    notas              TEXT
);
"""

GASES_INICIALES = ["Oxígeno", "Atal", "Argón", "Nitrógeno", "CO2", "Acetileno"]
PROVEEDORES_INICIALES = [
    ("Proveedor 1 - Arrendados", "intercambio"),
    ("Proveedor 2 - Rellenos JCM", "recarga"),
]
CLIENTES_INICIALES = ["RGM", "SIMAEF"]


def init_db():
    conn = sqlite3.connect(get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for nombre in GASES_INICIALES:
        conn.execute("INSERT OR IGNORE INTO catalogo_gases (nombre) VALUES (?)", (nombre,))
    for nombre, tipo in PROVEEDORES_INICIALES:
        conn.execute(
            "INSERT OR IGNORE INTO proveedores (nombre, tipo_operacion) VALUES (?, ?)",
            (nombre, tipo),
        )
    for nombre in CLIENTES_INICIALES:
        conn.execute("INSERT OR IGNORE INTO clientes (nombre) VALUES (?)", (nombre,))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

# entidad_id=0 representa siempre la Bodega JCM.
BODEGA_ID = 0


def _get_cantidad(db, ubicacion, entidad_id, gas_id, propiedad, estado):
    row = db.execute(
        """SELECT cantidad FROM inventario_actual
           WHERE ubicacion=? AND entidad_id=? AND gas_id=? AND propiedad=? AND estado=?""",
        (ubicacion, entidad_id, gas_id, propiedad, estado),
    ).fetchone()
    return row["cantidad"] if row else 0


def _upsert_inventario(db, ubicacion, entidad_id, gas_id, propiedad, estado, delta):
    """Añade delta (puede ser negativo) a la cantidad. Lanza ValueError si queda < 0."""
    actual = _get_cantidad(db, ubicacion, entidad_id, gas_id, propiedad, estado)
    nueva = actual + delta
    if nueva < 0:
        raise ValueError(
            f"Stock insuficiente: hay {actual} cilindro(s) y se requieren {abs(delta)}"
        )
    db.execute(
        """INSERT INTO inventario_actual
               (ubicacion, entidad_id, gas_id, propiedad, estado, cantidad)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(ubicacion, entidad_id, gas_id, propiedad, estado)
           DO UPDATE SET cantidad = excluded.cantidad""",
        (ubicacion, entidad_id, gas_id, propiedad, estado, nueva),
    )


def _registrar_historial(db, tipo, origen_id, destino_id, gas_id, propiedad, estado, cantidad, notas=None):
    db.execute(
        """INSERT INTO historial_movimientos
               (tipo_movimiento, entidad_origen_id, entidad_destino_id,
                gas_id, propiedad, estado_movido, cantidad, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tipo, origen_id, destino_id, gas_id, propiedad, estado, cantidad, notas),
    )

# ---------------------------------------------------------------------------
# Routes — vistas
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

# ---------------------------------------------------------------------------
# Routes — catálogos
# ---------------------------------------------------------------------------

@app.route("/api/gases", methods=["GET"])
def get_gases():
    db = get_db()
    rows = db.execute("SELECT id, nombre FROM catalogo_gases ORDER BY nombre").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/clientes", methods=["GET"])
def get_clientes():
    db = get_db()
    rows = db.execute("SELECT id, nombre FROM clientes ORDER BY nombre").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/clientes", methods=["POST"])
def crear_cliente():
    data = request.get_json(force=True)
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "El nombre del cliente es requerido"}), 400
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO clientes (nombre) VALUES (?)", (nombre,))
        db.commit()
        return jsonify({"id": cursor.lastrowid, "nombre": nombre}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": f"El cliente '{nombre}' ya existe"}), 409


@app.route("/api/proveedores", methods=["GET"])
def get_proveedores():
    db = get_db()
    rows = db.execute(
        "SELECT id, nombre, tipo_operacion FROM proveedores ORDER BY nombre"
    ).fetchall()
    return jsonify([dict(r) for r in rows])

# ---------------------------------------------------------------------------
# Routes — inventario
# ---------------------------------------------------------------------------

@app.route("/api/inventario/bodega", methods=["GET"])
def inventario_bodega():
    db = get_db()
    rows = db.execute(
        """SELECT g.nombre AS gas, i.propiedad, i.estado, i.cantidad
           FROM inventario_actual i
           JOIN catalogo_gases g ON g.id = i.gas_id
           WHERE i.ubicacion = 'bodega' AND i.cantidad > 0
           ORDER BY g.nombre, i.propiedad, i.estado"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/inventario/completo", methods=["GET"])
def inventario_completo():
    db = get_db()
    rows = db.execute(
        """SELECT i.ubicacion,
                  CASE i.ubicacion
                      WHEN 'cliente'   THEN (SELECT nombre FROM clientes   WHERE id = i.entidad_id)
                      WHEN 'proveedor' THEN (SELECT nombre FROM proveedores WHERE id = i.entidad_id)
                      ELSE 'Bodega JCM'
                  END AS entidad_nombre,
                  g.nombre AS gas, i.propiedad, i.estado, i.cantidad
           FROM inventario_actual i
           JOIN catalogo_gases g ON g.id = i.gas_id
           WHERE i.cantidad > 0
           ORDER BY i.ubicacion, entidad_nombre, g.nombre, i.propiedad, i.estado"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/inventario/ajuste", methods=["POST"])
def ajuste_inventario():
    """Establece cantidad absoluta para una combinación dada (ingreso inicial / corrección)."""
    data = request.get_json(force=True)
    for field in ["gas_id", "propiedad", "estado", "cantidad"]:
        if field not in data:
            return jsonify({"error": f"Campo requerido: {field}"}), 400

    ubicacion  = data.get("ubicacion", "bodega")
    entidad_id = int(data.get("entidad_id", BODEGA_ID))
    gas_id     = int(data["gas_id"])
    propiedad  = data["propiedad"]
    estado     = data["estado"]
    cantidad   = int(data["cantidad"])
    notas      = data.get("notas", "Ajuste manual")

    if cantidad < 0:
        return jsonify({"error": "La cantidad no puede ser negativa"}), 400

    db = get_db()
    try:
        db.execute(
            """INSERT INTO inventario_actual
                   (ubicacion, entidad_id, gas_id, propiedad, estado, cantidad)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(ubicacion, entidad_id, gas_id, propiedad, estado)
               DO UPDATE SET cantidad = excluded.cantidad""",
            (ubicacion, entidad_id, gas_id, propiedad, estado, cantidad),
        )
        _registrar_historial(db, "ajuste_inventario", None, entidad_id,
                             gas_id, propiedad, estado, cantidad, notas)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Routes — movimientos
# ---------------------------------------------------------------------------

@app.route("/api/movimiento/despacho", methods=["POST"])
def despacho_cliente():
    """Bodega → Cliente (cilindros llenos)."""
    data = request.get_json(force=True)
    for f in ["cliente_id", "gas_id", "propiedad", "cantidad"]:
        if f not in data:
            return jsonify({"error": f"Campo requerido: {f}"}), 400

    cliente_id = int(data["cliente_id"])
    gas_id     = int(data["gas_id"])
    propiedad  = data["propiedad"]
    cantidad   = int(data["cantidad"])

    if cantidad <= 0:
        return jsonify({"error": "La cantidad debe ser mayor a 0"}), 400

    db = get_db()
    try:
        _upsert_inventario(db, "bodega",   BODEGA_ID,  gas_id, propiedad, "lleno", -cantidad)
        _upsert_inventario(db, "cliente",  cliente_id, gas_id, propiedad, "lleno",  cantidad)
        _registrar_historial(db, "despacho_cliente", BODEGA_ID, cliente_id,
                             gas_id, propiedad, "lleno", cantidad, data.get("notas"))
        db.commit()
        return jsonify({"ok": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/movimiento/retorno", methods=["POST"])
def retorno_cliente():
    """Cliente → Bodega (cilindros devueltos)."""
    data = request.get_json(force=True)
    for f in ["cliente_id", "gas_id", "propiedad", "estado", "cantidad"]:
        if f not in data:
            return jsonify({"error": f"Campo requerido: {f}"}), 400

    cliente_id = int(data["cliente_id"])
    gas_id     = int(data["gas_id"])
    propiedad  = data["propiedad"]
    estado     = data["estado"]
    cantidad   = int(data["cantidad"])

    if cantidad <= 0:
        return jsonify({"error": "La cantidad debe ser mayor a 0"}), 400

    db = get_db()
    try:
        _upsert_inventario(db, "cliente", cliente_id, gas_id, propiedad, estado, -cantidad)
        _upsert_inventario(db, "bodega",  BODEGA_ID,  gas_id, propiedad, estado,  cantidad)
        _registrar_historial(db, "retorno_cliente", cliente_id, BODEGA_ID,
                             gas_id, propiedad, estado, cantidad, data.get("notas"))
        db.commit()
        return jsonify({"ok": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/movimiento/intercambio", methods=["POST"])
def intercambio_proveedor():
    """Proveedor 1: vacíos arrendados → llenos arrendados en bodega (atómico)."""
    data = request.get_json(force=True)
    for f in ["gas_id", "cantidad"]:
        if f not in data:
            return jsonify({"error": f"Campo requerido: {f}"}), 400

    gas_id   = int(data["gas_id"])
    cantidad = int(data["cantidad"])

    if cantidad <= 0:
        return jsonify({"error": "La cantidad debe ser mayor a 0"}), 400

    db = get_db()
    prov = db.execute(
        "SELECT id FROM proveedores WHERE tipo_operacion='intercambio' LIMIT 1"
    ).fetchone()
    if not prov:
        return jsonify({"error": "Proveedor de intercambio no encontrado"}), 404

    try:
        _upsert_inventario(db, "bodega", BODEGA_ID, gas_id, "arrendado", "vacio", -cantidad)
        _upsert_inventario(db, "bodega", BODEGA_ID, gas_id, "arrendado", "lleno",  cantidad)
        _registrar_historial(db, "intercambio_proveedor", prov["id"], BODEGA_ID,
                             gas_id, "arrendado", "lleno", cantidad, data.get("notas"))
        db.commit()
        return jsonify({"ok": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/movimiento/envio_proveedor", methods=["POST"])
def envio_proveedor():
    """Bodega → Proveedor 2 (vacíos propios, diferido — fase 1)."""
    data = request.get_json(force=True)
    for f in ["gas_id", "cantidad"]:
        if f not in data:
            return jsonify({"error": f"Campo requerido: {f}"}), 400

    gas_id   = int(data["gas_id"])
    cantidad = int(data["cantidad"])

    if cantidad <= 0:
        return jsonify({"error": "La cantidad debe ser mayor a 0"}), 400

    db = get_db()
    prov = db.execute(
        "SELECT id FROM proveedores WHERE tipo_operacion='recarga' LIMIT 1"
    ).fetchone()
    if not prov:
        return jsonify({"error": "Proveedor de recarga no encontrado"}), 404

    prov_id = prov["id"]
    try:
        _upsert_inventario(db, "bodega",    BODEGA_ID, gas_id, "propio", "vacio", -cantidad)
        _upsert_inventario(db, "proveedor", prov_id,   gas_id, "propio", "vacio",  cantidad)
        _registrar_historial(db, "envio_proveedor", BODEGA_ID, prov_id,
                             gas_id, "propio", "vacio", cantidad, data.get("notas"))
        db.commit()
        return jsonify({"ok": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/movimiento/retorno_proveedor", methods=["POST"])
def retorno_proveedor():
    """Proveedor 2 → Bodega (llenos propios, diferido — fase 2)."""
    data = request.get_json(force=True)
    for f in ["gas_id", "cantidad"]:
        if f not in data:
            return jsonify({"error": f"Campo requerido: {f}"}), 400

    gas_id   = int(data["gas_id"])
    cantidad = int(data["cantidad"])

    if cantidad <= 0:
        return jsonify({"error": "La cantidad debe ser mayor a 0"}), 400

    db = get_db()
    prov = db.execute(
        "SELECT id FROM proveedores WHERE tipo_operacion='recarga' LIMIT 1"
    ).fetchone()
    if not prov:
        return jsonify({"error": "Proveedor de recarga no encontrado"}), 404

    prov_id = prov["id"]
    try:
        _upsert_inventario(db, "proveedor", prov_id,   gas_id, "propio", "vacio", -cantidad)
        _upsert_inventario(db, "bodega",    BODEGA_ID, gas_id, "propio", "lleno",  cantidad)
        _registrar_historial(db, "retorno_proveedor", prov_id, BODEGA_ID,
                             gas_id, "propio", "lleno", cantidad, data.get("notas"))
        db.commit()
        return jsonify({"ok": True})
    except ValueError as e:
        db.rollback()
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Routes — historial
# ---------------------------------------------------------------------------

@app.route("/api/historial", methods=["GET"])
def get_historial():
    limite = int(request.args.get("limite", 100))
    offset = int(request.args.get("offset", 0))
    db = get_db()
    rows = db.execute(
        """SELECT h.id, h.fecha, h.tipo_movimiento,
                  g.nombre AS gas, h.propiedad, h.estado_movido, h.cantidad, h.notas,
                  co.nombre AS origen_nombre, cd.nombre AS destino_nombre
           FROM historial_movimientos h
           JOIN catalogo_gases g ON g.id = h.gas_id
           LEFT JOIN (
               SELECT id, nombre FROM clientes
               UNION ALL SELECT id, nombre FROM proveedores
           ) co ON co.id = h.entidad_origen_id
           LEFT JOIN (
               SELECT id, nombre FROM clientes
               UNION ALL SELECT id, nombre FROM proveedores
           ) cd ON cd.id = h.entidad_destino_id
           ORDER BY h.id DESC
           LIMIT ? OFFSET ?""",
        (limite, offset),
    ).fetchall()
    return jsonify([dict(r) for r in rows])

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def open_browser():
    import time
    time.sleep(1.2)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    init_db()
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
