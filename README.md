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
- Agrupa coordenadas cercanas redondeando a 2 decimales.
- Usa Nominatim/OpenStreetMap para geocodificación inversa.
- Imprime CSV por stdout.

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

## Ajustar precisión

El parámetro `--coordinate-precision` controla cómo se agrupan fotos cercanas antes de geocodificar:

```bash
./get_image_locations.py "/Volumes/Bichopalo/Lightroom - Japon Mayo 2026" \
  --coordinate-precision 2
```

Valores útiles:

- `2`: agrupa más, menos llamadas al mapa, bueno para nivel ciudad/zona.
- `3`: más detalle, puede separar ubicaciones cercanas dentro de una misma zona.
- `4`: mucho más detalle, puede generar muchas llamadas.

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
