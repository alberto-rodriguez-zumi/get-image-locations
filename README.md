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
- Usa Nominatim/OpenStreetMap para geocodificación inversa.
- Imprime CSV por stdout.
- Muestra el progreso por stderr para no mezclarlo con el CSV.

## Exportar a CSV

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --output locations.csv
```

El script imprime siempre el resultado por stdout y, si pasas `--output`, también escribe el archivo.

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

## Notas sobre Nominatim/OpenStreetMap

El script usa el endpoint público de Nominatim con una pausa de 1 segundo entre consultas nuevas, tal como recomienda el servicio. Para muchas fotos, la primera ejecución puede tardar; las siguientes serán más rápidas gracias a la caché.

Si vas a procesar miles de coordenadas a menudo, conviene usar un servicio propio o un proveedor comercial de geocodificación inversa.
