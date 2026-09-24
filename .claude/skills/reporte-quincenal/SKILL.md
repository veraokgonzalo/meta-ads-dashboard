---
name: reporte-quincenal
description: Genera un informe HTML consolidado de los últimos 15 días de TODAS las cuentas del portfolio de Meta Ads, con resumen de alto nivel por cuenta (mensajes para campañas de mensajería, clics + CTR para tráfico) y una sección de sugerencias de cambios de anuncios/creativos. Usar cuando el usuario pida "informe quincenal", "reporte del portfolio", "revisión de todas las cuentas", o sugerencias de optimización de anuncios.
---

# Reporte quincenal — portfolio Meta Ads

Genera un único `.html` autocontenido (datos y miniaturas horneados en base64,
Chart.js por CDN) con la performance de los últimos 15 días de **todas** las cuentas
del portfolio, más **sugerencias de anuncios/creativos**. Sale a `reports/quincenales/`.

A diferencia de `reporte-cuenta` (un informe mensual por cuenta, para enviar al
cliente), esto es una **revisión interna del portfolio** para decidir cambios.

## Flujo en 3 pasos
Las sugerencias las redacta Claude (no son reglas automáticas), así que el flujo
separa la extracción de datos del análisis:

1. **fetch** (Python): consulta la Graph API de todas las cuentas y vuelca los datos.
2. **análisis** (Claude): lee el JSON de datos, interpreta y completa el JSON de
   sugerencias (resumen ejecutivo + por cuenta + sugerencias por anuncio).
3. **build** (Python): hornea el HTML consolidado.

Requiere `META_TOKEN` en el `.env` del proyecto.

### Paso 1 — fetch
```
python .claude/skills/reporte-quincenal/generate_quincenal.py fetch
```
Opcionales: `--days N` (ventana, default 15), `--no-thumbs` (no descargar miniaturas).

La ventana son los N días que terminan **ayer** (el día en curso es parcial).
Escribe:
- `data/quincenal_<hasta>.json` (+ `data/quincenal_latest.json`) — datos crudos.
- `data/quincenal_<hasta>_sugerencias.json` — esqueleto editable (no pisa uno existente).

Solo se incluyen cuentas con inversión > 0 en el período. Cada anuncio se clasifica
en `mensajes` o `trafico` según el **objetivo de la campaña** (fallback: si tiene
mensajes > 0 → mensajes; si no → tráfico).

### Paso 2 — análisis (Claude)
Leer el JSON de datos. Como las miniaturas base64 lo hacen enorme, extraer una vista
compacta (sin el campo `thumb`) con un one-liner de Python antes de analizar.

Completar `data/quincenal_<hasta>_sugerencias.json` con:
- `summary`: resumen ejecutivo del portfolio (acepta HTML simple, ej. `<strong>`).
- `accounts[<id>].summary`: lectura de alto nivel de la cuenta.
- `accounts[<id>].suggestions[]`: cada una con
  - `severity`: `alta` (acción urgente: pausar/reemplazar), `media` (optimizar/renovar
    pronto), `baja` (mantener/escalar/monitorear) — colorea la etiqueta en el HTML.
  - `ad`: nombre del anuncio/campaña al que refiere (opcional; se muestra en negrita).
  - `text`: la recomendación concreta.

Señales útiles para las sugerencias (presentes en el JSON por anuncio):
- **CTR bajo** → revisar creativo/segmentación.
- **Costo por resultado alto** vs. el promedio de la cuenta → candidato a pausa/renovación.
- **Frecuencia ≥ 3** → fatiga de creativo (en la tabla se marca en rojo).
- **Muchos clics, pocos mensajes** → desconexión entre clic y conversación (CTA/flujo).
- **Campañas de evento ya finalizado** (Cyber, Día del Padre, etc.) → pausar.
- **Cuenta dependiente de un solo creativo** → sumar variantes.

### Paso 3 — build
```
python .claude/skills/reporte-quincenal/generate_quincenal.py build
```
Opcionales: `--data <archivo.json>`, `--suggestions <archivo.json>` (por defecto usa
`quincenal_latest.json` y el `_sugerencias.json` que le corresponde por fecha).

Si falta el JSON de sugerencias, igual genera el HTML con placeholders.
Escribe `reports/quincenales/quincenal_<hasta>.html`.

## Monedas
La inversión **no se suma entre monedas distintas**. El portfolio suele mezclar ARS y
CLP; el informe muestra la inversión agrupada por moneda y etiqueta la moneda de cada
cuenta en el gráfico de barras. Los KPIs globales (cuentas, mensajes, clics, CTR
promedio) sí son agregables.

## Componentes del informe
Encabezado (portfolio + logo) · Resumen ejecutivo · 4 KPIs globales (cuentas activas,
mensajes, clics, CTR promedio) · inversión por moneda · 1 gráfico de barras (inversión
por cuenta, moneda local) · una sección por cuenta con: KPIs propios (según sea de
mensajes y/o tráfico), análisis, lista de sugerencias por severidad y tabla por anuncio
(miniatura, campaña, inversión, resultado, costo, CTR, frecuencia) · pie de Referencia.

Para mantener el HTML liviano y evitar cuelgues de render, el informe usa **un solo
canvas** (el resto es tabla/números), a diferencia de `reporte-cuenta`.

## Estructura
- `generate_quincenal.py` — subcomandos `fetch` y `build`.
- `report_template.html` — plantilla; se auto-renderiza desde `DATA` inyectado en el
  marcador `/*__DATA__*/{}`.
