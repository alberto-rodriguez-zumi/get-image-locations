# Get Image Locations

Script de CLI para leer coordenadas GPS de fotos y videos organizados en subcarpetas, convertirlas en nombres de lugares y mostrar un resumen por carpeta.

Ejemplo de salida:

```csv
"2026-06-02";"Matsumoto, Azumino"
```

## Dependencias

No requiere paquetes de Python externos. Usa solo la biblioteca estándar de Python.

Necesitas:

- Python 3.10 o superior.
- `exiftool`, para leer metadatos GPS de HEIC, JPEG, MOV, RAW, etc.
- Conexión a internet si quieres convertir coordenadas en nombres de lugares.

### Instalar en macOS

```bash
brew install exiftool
```

Python 3 suele venir instalado en macOS. Si necesitas instalarlo:

```bash
brew install python
```

### Instalar en Debian/Ubuntu

```bash
sudo apt update
sudo apt install libimage-exiftool-perl python3
```

## Uso

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026"
```

Aviso de privacidad: este modo envía coordenadas GPS redondeadas a Nominatim/OpenStreetMap para convertirlas en nombres de lugares. Si quieres trabajar solo en local, usa `--no-geocode`.

Por defecto:

- Lee las subcarpetas inmediatas de la carpeta raíz.
- Busca GPS en HEIC, HEIF, JPG, JPEG, PNG, TIFF, DNG, varios RAW, MOV y MP4.
- Agrupa coordenadas cercanas con un radio de 1000 metros antes de geocodificar.
- Permite ocultar ubicaciones con pocas fotos.
- Usa Nominatim/OpenStreetMap para geocodificación inversa con detalle medio.
- Imprime CSV por stdout.
- Muestra el progreso por stderr para no mezclarlo con el CSV.

## Exportar a CSV

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --output locations.csv
```

El script imprime siempre el resultado por stdout y, si pasas `--output`, también escribe el archivo.

## Generar GPX para Lightroom

Puedes generar un GPX por subcarpeta usando las coordenadas GPS y la hora de captura:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --gpx-output-dir gpx
```

Esto mantiene el CSV por stdout y escribe ficheros como `gpx/2026-06-02.gpx`. Si solo quieres generar GPX sin buscar nombres de ubicaciones ni imprimir CSV:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --gpx-output-dir gpx \
  --gpx-only
```

Para respetar limites de puntos al importar en Lightroom:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --gpx-output-dir gpx \
  --gpx-only \
  --gpx-max-points 500
```

Antes de aplicar ese limite, el script simplifica tramos de puntos muy cercanos: si varios puntos consecutivos estan dentro de la distancia y tiempo indicados, conserva solo el primero y el ultimo del tramo.

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --gpx-output-dir gpx \
  --gpx-only \
  --gpx-simplify-distance-meters 25 \
  --gpx-simplify-time-seconds 300
```

Valores por defecto:

- `--gpx-simplify-distance-meters 25`
- `--gpx-simplify-time-seconds 300`
- `--gpx-max-points 0`, sin limite duro

## Ignorar metadatos incorrectos

El script descarta por defecto puntos claramente sospechosos:

- Coordenadas fuera de rango.
- Coordenadas `0,0`, tipicas de metadatos rotos.
- Fechas anteriores a `2000-01-01`.
- En carpetas con nombre `YYYY-MM-DD`, fotos cuya fecha de captura se aleje mas de 2 dias de esa fecha.

Si tus carpetas no tienen fecha en el nombre, ese ultimo filtro no se aplica. Solo se activa cuando el nombre de la carpeta es exactamente una fecha como `2026-06-02`.

Puedes ajustar estos filtros:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --min-capture-date 2020-01-01 \
  --folder-date-tolerance-days 5
```

Para desactivar la comprobacion contra la fecha de carpeta:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --folder-date-tolerance-days -1
```

Si de verdad quieres conservar coordenadas `0,0`:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --allow-zero-coordinates
```

## Probar una sola carpeta

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --folder 2026-06-02
```

Puedes pasar varias carpetas:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --folder 2026-06-01 \
  --folder 2026-06-02
```

## Ver coordenadas sin usar internet

Esto sirve para comprobar que los metadatos GPS se leen bien:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --folder 2026-06-02 \
  --no-geocode
```

Ejemplo:

```csv
"2026-06-02";"36.047,138.119, 36.048,138.122"
```

## Caché de ubicaciones

Las llamadas al servicio de mapas se guardan en `.geocode-cache.json` para no repetir consultas.

Puedes cambiar la ruta:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --cache cache-japon.json
```

## Progreso

Mientras procesa, el script muestra la carpeta actual y el numero de archivos analizados:

```text
Processing 2026-06-02: 100/248 files analyzed
```

El progreso se escribe en stderr, no en stdout. Asi puedes redirigir el CSV sin contaminarlo:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --output locations.csv
```

Para ocultar el progreso:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --no-progress
```

## Agrupar puntos cercanos

Para reducir llamadas al servicio de mapas, las coordenadas GPS cercanas se agrupan antes de geocodificar. Por defecto se agrupan puntos a menos de 1000 metros.

Puedes ajustar el radio:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --cluster-radius-meters 2500
```

Para desactivar esta agrupacion:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --cluster-radius-meters 0
```

## Ocultar ubicaciones con pocas fotos

Puedes pedir que una ubicacion solo aparezca si tiene un minimo de fotos o videos con GPS dentro del grupo:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --min-photos-per-location 3
```

Esto es util para descartar paradas logisticas, hoteles o tiendas con una o dos fotos sueltas. El filtro se aplica despues de agrupar por distancia, asi que una ubicacion se queda fuera antes de llamar al servicio de mapas.

## Ajustar precisión

El parametro `--coordinate-precision` controla cuantos decimales se muestran cuando usas `--no-geocode`:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --coordinate-precision 2
```

Valores útiles:

- `2`: salida compacta, buena para inspeccion rapida.
- `3`: mas detalle en coordenadas impresas.
- `4`: mucho mas detalle si necesitas auditar puntos concretos.

## Idioma

Puedes pedir nombres localizados:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --language es
```

Por defecto usa `en`, que suele dar nombres más estables para exportaciones.

## Nivel de detalle de los nombres

El script pide a Nominatim nombres de detalle medio por defecto. Intenta evitar direcciones postales como `Ginza 2`, `Kuramae 2-chome` o `Oshiage 1`, pero sigue priorizando puntos turisticos o historicos si OpenStreetMap los devuelve con nombre.

Puedes ajustar el nivel con `--geocode-zoom`, de `0` a `18`:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --geocode-zoom 12
```

Valores utiles:

- `10`: mas amplio, suele devolver provincia/comarca/municipio grande.
- `12` o `14`: equilibrio entre ciudad, pueblo y zona; `12` es el valor por defecto.
- `16`: mas especifico, puede funcionar para sitios concretos pero tambien sacar barrios numerados.
- `18`: muy concreto, puede acabar devolviendo edificios, calles o objetos cercanos.

Tambien puedes controlar que tipo de nombre se prefiere:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --name-detail specific
```

Modos:

- `balanced`: valor por defecto; evita direcciones tipo `chome`.
- `specific`: permite localidades pequeñas como `hamlet` o `locality`, pero sigue evitando direcciones.
- `address`: permite nombres de direccion, barrios numerados y `chome`.

Por defecto se intentan evitar nombres en escritura local como kanji/kana si hay una alternativa romanizada o mas amplia. Si quieres conservar nombres locales:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --allow-local-script
```

La cache incluye el zoom, el modo de nombre y la preferencia de escritura en la clave, asi que cambiar estos parametros genera nuevas consultas sin reutilizar resultados antiguos demasiado amplios.

## Notas sobre Nominatim/OpenStreetMap

El script usa el endpoint público de Nominatim con una pausa de 1 segundo entre consultas nuevas, tal como recomienda el servicio. Para muchas fotos, la primera ejecución puede tardar; las siguientes serán más rápidas gracias a la caché.

Si vas a procesar miles de coordenadas a menudo, conviene usar un servicio propio o un proveedor comercial de geocodificación inversa.
