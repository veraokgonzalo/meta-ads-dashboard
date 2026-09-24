---
name: reporte-cuenta
description: Genera un informe HTML estatico de performance mensual de Meta Ads para una cuenta comercial. Usar cuando el usuario pida "armar/generar un reporte", "informe del mes", "reporte de la cuenta X", o un entregable HTML de resultados de anuncios. Soporta dos tipos -- "mensajes" (campanas de mensajeria) y "trafico" (campanas de clics).
---

# Reporte de cuenta — informe HTML de Meta Ads

Genera un `.html` autocontenido (datos horneados, logo y miniaturas en base64,
Chart.js por CDN) con la performance mensual de una cuenta. Pensado para enviar
al cliente.

## Cuando usar
El usuario quiere un informe/entregable de resultados de una cuenta para un mes.
NO es el dashboard live (`index.html`); esto produce un archivo estatico bajo `reports/`.

## Parametros necesarios
Antes de generar, asegurate de tener (preguntar si falta):
- **account**: ID de la cuenta (con o sin prefijo `act_`).
- **month**: mes en formato `YYYY-MM` (ej. `2026-05`).
- **type**: `mensajes` o `trafico`.
  - `mensajes` → KPI/charts/tabla sobre **Mensajes** (`onsite_conversion.messaging_conversation_started_7d`).
  - `trafico` → sobre **Clics** (campo `clicks`, todos los clics; consistente con el CTR general).

Si no sabes el ID de la cuenta, listalo con la tool MCP `ads_get_ad_accounts`.

## Como generar
Requiere `META_TOKEN` en el `.env` del proyecto (lo lee el script, igual que `server.py`).

```
python .claude/skills/reporte-cuenta/generate_report.py --account <ID> --month <YYYY-MM> --type <mensajes|trafico>
```

Opcionales:
- `--name "Nombre"`: fuerza el nombre a mostrar (util cuando la cuenta no tiene
  `name` configurado en Meta y la API devuelve el ID).
- `--logo archivo.png`: logo del encabezado (archivo en `assets/`). Default el de
  trasmedia (`logo_tras_amarillo.jpg`).

Para cuentas fijas (nombre y/o logo propio) no hace falta pasar los flags cada mes:
ver **Overrides por cuenta** mas abajo.

### Reporte por campanas (evento puntual, ej. Cyber/Black)
Para un informe acotado a campanas especificas y a un rango libre de fechas (en vez
del mes completo de cuenta):
- `--since YYYY-MM-DD --until YYYY-MM-DD`: rango de fechas (reemplaza a `--month`).
- `--campaigns "ID1,ID2,..."`: IDs de campana; el informe (KPIs, charts, demografia,
  tabla) se filtra solo a esas campanas. Sacar los IDs con la tool MCP
  `ads_get_ad_entities` (level=campaign).
- `--label "Cyber 2026"`: texto de periodo que se muestra en el encabezado.

En este modo se omiten la grafica de tendencia mensual y los seguidores de IG (son
metricas mensuales de cuenta, no aplican a un evento). El archivo sale como
`reports/reporte_<cuenta>_<label>_<tipo>.html`.

Ejemplo:
```
python .claude/skills/reporte-cuenta/generate_report.py --account 4100755920144855 \
  --since 2026-05-28 --until 2026-06-08 --type mensajes --label "Cyber 2026" \
  --campaigns "120245872098750327,120246033693790327,120246034000830327"
```

El script:
1. Lee el token del `.env`.
2. Consulta la Graph API v21.0: datos de cuenta, serie diaria (`time_increment=1`),
   nivel anuncio y miniaturas de creatividades.
3. Embebe logo (`assets/logo_tras_amarillo.jpg`) y miniaturas en base64.
4. Genera un borrador de conclusiones a partir de los datos.
5. Escribe `reports/reporte_<cuenta>_<mes>_<tipo>.html`.

## Despues de generar
- Abri el HTML para verificar (preview/navegador).
- El **resumen del mes** es un borrador automatico: revisalo/edita el texto
  (placeholder `[Editar: ...]`) con conclusiones cualitativas reales antes de enviar.

## PDF (una sola pagina, sin cortes)
Una vez verificado y editado el HTML, generar el PDF con `html_to_pdf.py`. Produce
un PDF de **una sola pagina continua** (ancho tabloid 11", alto = el del contenido),
asi los graficos quedan a tamaño completo y el reporte no se parte en hojas.

```
python .claude/skills/reporte-cuenta/html_to_pdf.py <input.html> [output.pdf] [--width-in 11]
```

- Maneja Chrome/Edge headless por CDP: renderiza, espera fuentes + charts, mide el
  alto real del contenido y emite una pagina a esa medida.
- Requiere el paquete `websocket-client` (`pip install websocket-client`) y
  Chrome o Edge instalado.
- El tamaño de pagina lo fija este script, no el CSS (la plantilla solo trae
  `@page { margin: 0 }` y ajustes de `@media print`).

## Organizacion de archivos
Cada mes se guarda en `reports/<YYYY-MM>/` con dos subcarpetas:
- `reports/<mes>/html/` — los `.html` generados.
- `reports/<mes>/pdf/`  — los `.pdf` de una pagina.

## Subida a Google Drive (rclone)
**IMPORTANTE — pedir confirmacion explicita del usuario ANTES de subir a Drive.**
Las carpetas estan compartidas con los clientes: nunca subir sin que el usuario
confirme expresamente. Mostrar el mapeo (cuenta -> carpeta -> nombre del PDF) y
esperar el OK antes de correr rclone.

Cada cliente tiene en Drive una subcarpeta **META ADS** donde va su informe. La
subida se hace con **rclone** (remote `metadrive`, tipo drive, scope drive).

- Mapeo cuenta -> `folder_id` de META ADS y token de nombre: `drive_folders.json`.
- Convencion de nombre del PDF en Drive: **`AAAAMM_<CLIENTE>_METAADS.pdf`**
  (ej. `202606_TABOO_METAADS.pdf`). El `<CLIENTE>` es el campo `name` del mapeo.
- Subir un PDF a la carpeta de un cliente:

```
rclone copyto "reports/<mes>/pdf/<archivo>.pdf" "metadrive:AAAAMM_<CLIENTE>_METAADS.pdf" \
  --drive-root-folder-id <folder_id>
```

Notas:
- El conector MCP de Drive NO sirve para esto (sube el archivo como base64 inline,
  tope ~30-40 KB; los PDFs pesan ~700 KB). Por eso se usa rclone.
- `folder_id: null` en el mapeo = ese cliente aun no tiene subcarpeta META ADS;
  crearla en Drive y completar el ID. (Ej. Carolina Varela la sube el usuario;
  Fermin Larranaga: crear la subcarpeta.)
- Config de rclone una sola vez: `rclone authorize "drive"` (login en el navegador
  con la cuenta que administra las carpetas) y luego
  `rclone config create metadrive drive token '<TOKEN>' scope drive`.

## Mensajes de entrega (WhatsApp)
El ultimo paso es generar un mensaje por cliente con el link de Drive de su informe.
**El envio es manual**: el usuario copia cada mensaje y lo pega en el WhatsApp del
cliente. (No hay integracion automatica con WhatsApp.)

1. Obtener el link de cada PDF subido (id del archivo en su carpeta META ADS):

```
rclone lsf metadrive: --drive-root-folder-id <folder_id> --files-only --format "ip"
```

   Devuelve `id;nombre`; el link es `https://drive.google.com/file/d/<id>/view`.

2. Armar un mensaje por cliente con esta **plantilla** (incluye el nombre del cliente):

```
¡Hola *<CLIENTE>*! 👋 Te compartimos el informe de tus campañas de Meta Ads de *<MES AÑO>*.
📊 <link de Drive>
Quedamos a disposición por cualquier consulta. ¡Saludos! 🙌
```

- `<CLIENTE>` = nombre del cliente (campo `client` de `drive_folders.json`).
- `<MES AÑO>` = ej. "junio 2026".
- El link funciona para quien ya tiene acceso a la carpeta del cliente. Si un cliente
  no tiene acceso, hay que compartir el archivo (o poner "cualquiera con el enlace").

## Componentes del informe
Encabezado (nombre cuenta + logo) · Resumen del mes · 3 KPIs (Inversion,
Mensajes/Clics, Costo por mensaje/clic) · 2 graficas de barras (metrica/dia e
inversion/dia) · demografia (metrica principal por genero y por edad, via
breakdowns=gender/age) · seguidores de Instagram (KPI + evolucion mensual, si la
cuenta tiene IG vinculado) · tabla por anuncio (miniatura, descripcion del anuncio
+ tipo reel/placa/carrusel derivados del nombre del anuncio, inversion, mensajes,
costo por mensaje, clics, costo por clic, CTR — ambas metricas siempre, sin importar
el tipo) · pie de Referencia (glosario).

## Overrides por cuenta (nombre y logo fijos)
Constante `ACCOUNT_OVERRIDES` en `generate_report.py`, indexada por ID de cuenta
(sin `act_`). Permite fijar el **nombre a mostrar** y/o el **logo** de cuentas
puntuales sin pasar `--name`/`--logo` en cada corrida:

```python
ACCOUNT_OVERRIDES = {
    "1372445320756173": {"name": "JMChef"},              # sin nombre en Meta
    "3314437138860175": {"name": "CI Textil"},           # sin nombre util en Meta
    "4100755920144855": {"logo": "logo_realoaded.png"},  # Carolina Varela -> Reloaded
}
```

Precedencia: flag CLI (`--name`/`--logo`) > override de la cuenta > default
(nombre de Meta / logo trasmedia). Para sumar una cuenta, agregá su ID con `name`
y/o `logo` (archivo en `assets/`).

## Grafica de tendencia mensual (opcional, por cuenta)
Algunas cuentas muestran, al lado de los KPIs (apilados en vertical), una grafica
combinada de evolucion mensual: barras = Leads, linea = Inversion (en millones).
Se configura en la constante `MONTHLY_HISTORY` de `generate_report.py`, indexada
por ID de cuenta:
- `rows`: meses historicos fijos (`label`, `spend`, `leads`).
- `trend_start`: primer mes (YYYY-MM) que se trae **en vivo** desde la API; ese mes
  y los siguientes (hasta el mes del reporte) se consultan y se anexan al historico.

Asi el historico viejo queda congelado y cada corrida nueva agrega el mes en curso.
Si la cuenta no esta en `MONTHLY_HISTORY`, el informe usa el layout clasico de 3 KPIs
en fila (sin grafica de tendencia).

## Estructura
- `generate_report.py` — fetch + render en un solo comando.
- `report_template.html` — plantilla generica; se auto-renderiza desde el objeto
  `DATA` que el script inyecta en el marcador `/*__DATA__*/{}`.

## Seguidores de Instagram
Se usa el mismo `META_TOKEN` del `.env` (necesita permisos `instagram_basic`,
`pages_show_list`, `pages_read_engagement`). Si la cuenta tiene un IG vinculado,
el informe agrega una **4ta KPI "Seguidores IG"** y una **seccion de evolucion
mensual** de seguidores.

Como funciona:
- El script resuelve la cuenta de IG sola: `act_{id}/promote_pages` -> pagina ->
  `instagram_business_account{followers_count}`. Si la cuenta no tiene IG
  vinculado, omite la KPI y el grafico (no falla).
- Meta **no expone el total historico** de seguidores (solo el actual). Por eso se
  toma un **snapshot mensual** y se guarda en `data/ig_followers.json`
  (`{ig_id: {"YYYY-MM": seguidores}}`). El grafico arranca con el mes en curso y se
  va llenando mes a mes. No se pisan snapshots ya guardados al regenerar.
- Para sembrar historico manual (si se tienen los numeros), usar la constante
  `IG_SEED` en `generate_report.py` (`{ig_id: {"YYYY-MM": seguidores}}`).

Si la cuenta no tiene IG vinculado (o el token no puede resolverlo), el informe se
genera igual sin la seccion de seguidores.
