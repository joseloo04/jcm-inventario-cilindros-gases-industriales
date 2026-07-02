# PLAN_PROYECTO.md — JCM Inventario de Cilindros de Gas

## 1. Visión General

Aplicación web local para el control de inventario de cilindros de gas de la empresa **JCM**.  
Se distribuye como un `.exe` autocontenido (PyInstaller) que levanta un servidor Flask local y abre automáticamente el navegador en `http://localhost:5000`.

---

## 2. Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.x + Flask |
| Frontend | HTML5 + CSS3 + Vanilla JavaScript |
| Base de Datos | SQLite (archivo `datos.db`) |
| Empaquetado | PyInstaller (modo `--onefile --noconsole`) |

---

## 3. Estructura de Archivos del Proyecto

```
JCM/
├── app.py                  # Servidor Flask + toda la lógica de negocio
├── templates/
│   └── index.html          # Interfaz completa (tabla de inventario + formularios)
├── PLAN_PROYECTO.md        # Este documento
└── build_exe.spec          # Spec de PyInstaller (generado con el comando indicado)
```

**Ruta de la base de datos en producción:**
```
C:\Users\<Usuario>\Documents\JCM_Inventario\datos.db
```
La carpeta se crea automáticamente si no existe. El `.exe` nunca modifica el archivo de la base de datos.

---

## 4. Esquema de Base de Datos (Implementación Exacta Exigida)

### 4.1 `catalogo_gases`
```sql
CREATE TABLE catalogo_gases (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);
```
**Datos iniciales (seeding):** Oxígeno, Atal, Argón, Nitrógeno, CO2, Acetileno.

---

### 4.2 `clientes`
```sql
CREATE TABLE clientes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);
```
**Datos iniciales:** RGM, SIMAEF.

---

### 4.3 `proveedores`
```sql
CREATE TABLE proveedores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL UNIQUE,
    tipo_operacion  TEXT NOT NULL CHECK(tipo_operacion IN ('intercambio', 'recarga'))
);
```
**Datos iniciales:**

| nombre | tipo_operacion |
|--------|---------------|
| Proveedor 1 - Arrendados | intercambio |
| Proveedor 2 - Rellenos JCM | recarga |

**Semántica de `tipo_operacion`:**
- `intercambio`: Proveedor 1. Recibe cilindros **vacíos arrendados** y los cambia de forma instantánea por **llenos arrendados**. El movimiento es atómico (una sola transacción que descuenta vacíos arrendados de bodega y añade llenos arrendados a bodega).
- `recarga`: Proveedor 2. Recibe cilindros **vacíos propios**, los retiene (salen del inventario de bodega), y en una fecha posterior los devuelve **llenos propios** (entran al inventario de bodega). Requiere dos movimientos separados: `envio_proveedor` y `retorno_proveedor`.

---

### 4.4 `inventario_actual` (Tabla de Saldos — Fuente de Verdad)
```sql
CREATE TABLE inventario_actual (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ubicacion   TEXT NOT NULL CHECK(ubicacion IN ('bodega', 'cliente', 'proveedor')),
    entidad_id  INTEGER,               -- NULL si ubicacion = 'bodega'
    gas_id      INTEGER NOT NULL REFERENCES catalogo_gases(id),
    propiedad   TEXT NOT NULL CHECK(propiedad IN ('propio', 'arrendado')),
    estado      TEXT NOT NULL CHECK(estado IN ('lleno', 'vacio')),
    cantidad    INTEGER NOT NULL DEFAULT 0 CHECK(cantidad >= 0),
    UNIQUE(ubicacion, entidad_id, gas_id, propiedad, estado)
);
```
**Regla clave:** cada combinación `(ubicacion, entidad_id, gas_id, propiedad, estado)` es única. Los movimientos hacen `INSERT OR REPLACE` / `UPDATE` sobre esta tabla. La cantidad nunca puede ser negativa (la constraint `CHECK` lo garantiza a nivel DB, y la lógica de Python lo valida antes).

---

### 4.5 `historial_movimientos` (Registro Inmutable — Auditoría)
```sql
CREATE TABLE historial_movimientos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    tipo_movimiento     TEXT NOT NULL,
    entidad_origen_id   INTEGER,
    entidad_destino_id  INTEGER,
    gas_id              INTEGER NOT NULL REFERENCES catalogo_gases(id),
    propiedad           TEXT NOT NULL,
    estado_movido       TEXT NOT NULL,
    cantidad            INTEGER NOT NULL,
    notas               TEXT
);
```
**Tipos de movimiento permitidos:**
- `despacho_cliente` — Bodega → Cliente
- `retorno_cliente` — Cliente → Bodega
- `intercambio_proveedor` — Vacíos arrendados → Bodega (llenos arrendados, Proveedor 1)
- `envio_proveedor` — Bodega → Proveedor 2 (vacíos propios)
- `retorno_proveedor` — Proveedor 2 → Bodega (llenos propios)
- `ajuste_inventario` — Corrección manual de saldos

---

## 5. Lógica de Negocio Detallada

### 5.1 Variables del Sistema

```
Tipo de Gas:  Oxígeno | Atal | Argón | Nitrógeno | CO2 | Acetileno
Propiedad:    Propio  | Arrendado
Estado:       Lleno   | Vacío
Ubicación:    Bodega  | Cliente | Proveedor
```

### 5.2 Actores

| Actor | Rol |
|-------|-----|
| Bodega (JCM) | Stock central. `ubicacion='bodega'`, `entidad_id=NULL` |
| Clientes | Reciben cilindros llenos, devuelven vacíos |
| Proveedor 1 - Arrendados | Intercambia vacíos arrendados → llenos arrendados (instantáneo) |
| Proveedor 2 - Rellenos JCM | Recibe vacíos propios, devuelve llenos propios (diferido) |

### 5.3 Flujos de Movimiento

#### Despacho a Cliente
- **Entrada:** `gas_id`, `propiedad`, `cantidad`, `cliente_id`
- **Validación:** `inventario_actual` en bodega tiene `estado='lleno'` con suficiente cantidad
- **Acción:**
  1. `inventario_actual[bodega, lleno]` -= cantidad
  2. `inventario_actual[cliente, lleno]` += cantidad
  3. INSERT en `historial_movimientos` tipo `despacho_cliente`

#### Retorno de Cliente
- **Entrada:** `gas_id`, `propiedad`, `cantidad`, `cliente_id`
- **Acción:**
  1. `inventario_actual[cliente, lleno o vacio]` -= cantidad
  2. `inventario_actual[bodega, vacio]` += cantidad
  3. INSERT en `historial_movimientos` tipo `retorno_cliente`

#### Intercambio con Proveedor 1 (Arrendados)
- Solo aplica a `propiedad='arrendado'`
- **Acción atómica:**
  1. `inventario_actual[bodega, arrendado, vacio]` -= cantidad
  2. `inventario_actual[bodega, arrendado, lleno]` += cantidad
  3. INSERT en `historial_movimientos` tipo `intercambio_proveedor`

#### Envío a Proveedor 2 (Propios, diferido — Fase 1)
- **Acción:**
  1. `inventario_actual[bodega, propio, vacio]` -= cantidad
  2. `inventario_actual[proveedor_2, propio, vacio]` += cantidad
  3. INSERT tipo `envio_proveedor`

#### Retorno de Proveedor 2 (Propios, diferido — Fase 2)
- **Acción:**
  1. `inventario_actual[proveedor_2, propio, *]` -= cantidad
  2. `inventario_actual[bodega, propio, lleno]` += cantidad
  3. INSERT tipo `retorno_proveedor`

### 5.4 Ajuste de Inventario (Ingreso Inicial / Correcciones)
Endpoint `POST /api/inventario/ajuste` permite establecer la cantidad de cualquier combinación directamente. Registra tipo `ajuste_inventario` en historial.

---

## 6. API Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` | Sirve `index.html` |
| GET | `/api/inventario/bodega` | Saldos actuales en bodega |
| GET | `/api/inventario/completo` | Saldos en todas las ubicaciones |
| GET | `/api/clientes` | Lista de clientes |
| POST | `/api/clientes` | Registrar nuevo cliente |
| GET | `/api/proveedores` | Lista de proveedores |
| GET | `/api/gases` | Catálogo de gases |
| POST | `/api/movimiento/despacho` | Bodega → Cliente |
| POST | `/api/movimiento/retorno` | Cliente → Bodega |
| POST | `/api/movimiento/intercambio` | Proveedor 1 (vacíos ↔ llenos arrendados) |
| POST | `/api/movimiento/envio_proveedor` | Bodega → Proveedor 2 |
| POST | `/api/movimiento/retorno_proveedor` | Proveedor 2 → Bodega |
| POST | `/api/inventario/ajuste` | Ajuste manual de saldo |
| GET | `/api/historial` | Historial de movimientos (paginado) |

---

## 7. Reglas de Arquitectura Críticas

1. **Auto-open browser:** Al iniciar, Flask lanza un hilo que espera 1 segundo y abre `http://localhost:5000` con `webbrowser.open()`.

2. **Separación de datos:** La ruta de la DB se resuelve con `os.path.join(os.path.expanduser("~"), "Documents", "JCM_Inventario", "datos.db")`. La carpeta se crea con `os.makedirs(..., exist_ok=True)`.

3. **Sin consola:** PyInstaller usa `--noconsole`. Flask corre con `app.run(debug=False)` sin `use_reloader=False` explícito (PyInstaller ya lo maneja con `sys.frozen`).

4. **Idempotencia del seeding:** Todas las inserciones iniciales usan `INSERT OR IGNORE` para no duplicar datos si el `.exe` se reemplaza.

5. **Integridad referencial:** Las constraints `FOREIGN KEY` y `CHECK` se activan con `PRAGMA foreign_keys = ON` al inicio de cada conexión.

---

## 8. Empaquetado con PyInstaller

```bash
# Instalar dependencias
pip install flask pyinstaller

# Compilar (ejecutar desde el directorio raíz del proyecto)
pyinstaller --onefile --noconsole --name "JCM_Inventario" --add-data "templates;templates" app.py
```

El `.exe` resultante queda en `dist/JCM_Inventario.exe`.  
La base de datos **nunca** se incluye en el `.exe`; se crea en `Documents\JCM_Inventario\` al primer arranque.

---

## 9. Escalabilidad

- **Nuevos gases:** `POST /api/gases` (a implementar si se requiere).
- **Nuevos clientes:** `POST /api/clientes` — añade filas a la tabla `clientes` sin alterar el esquema.
- **Nuevos proveedores:** `POST /api/proveedores` (a implementar si se requiere).
- El esquema de `inventario_actual` se expande dinámicamente con cada nuevo `gas_id` o `entidad_id`; no requiere migraciones de columnas.
