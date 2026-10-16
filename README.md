# ytdown

Descargador de YouTube de escritorio para Windows, con conversor de audio e
historial. Interfaz en español, construido sobre [yt-dlp], [FFmpeg] y
[customtkinter].

Nace de un problema concreto: los descargadores sueltos bajan video que pide
instalar códecs y que se reproduce **sin sonido**, repiten lo que ya tenías y
no te dejan elegir qué canciones de una playlist quieres. Esto resuelve esas
tres cosas.

---

## Qué hace

### ⬇ Descargar

| | |
|---|---|
| **Audio** | MP3, M4A, Opus, FLAC o WAV, con calidad de 96 a 320 kbps |
| **Video** | De 360p a 4K · MP4/MKV/WEBM · **con o sin sonido** |
| **Códec** | *Compatible* (H.264 + AAC), *Eficiente* (VP9 + Opus) o *Máxima calidad* |
| **Playlists** | Se despliegan con ▸ y eliges qué videos entran, uno a uno |
| **Sin repetir** | Recuerda lo descargado por video **y formato**; si borras el archivo, vuelve a permitirlo |
| **Velocidad** | Turbo / Normal / Seguro, más límite de ancho de banda |
| **Extras** | Metadatos, portada, subtítulos, marcas de capítulo, saltar patrocinios, igualar volumen |

El perfil **Compatible** es el que está por defecto, y es importante: pide
H.264 y AAC explícitamente. Un aviso en la propia ventana explica, en cada
combinación que elijas, qué archivo va a salir y si te va a dar problemas.

### 🎛 Convertir

Archivos sueltos o una carpeta entera a MP3, M4A, Opus, OGG, FLAC o WAV.
Conserva etiquetas y portada, iguala volumen, puede repetir la estructura de
subcarpetas y decide qué hacer si el archivo ya existe (renombrar, omitir o
sobrescribir). Saca también el audio de los videos.

Reconoce 39 extensiones de audio y, en los contenedores ambiguos (`.webm`,
`.mp4`, `.mkv`…), mira **dentro** del archivo: si solo trae audio, entra.

### 🗂 Historial

Qué bajaste y de qué enlace. Buscador por título o URL, copiar enlaces,
exportar a `.txt` o `.csv`, devolver algo a la cola o mandar el archivo al
conversor.

---

## Instalación

Lo más fácil es **`iniciar.bat`**: comprueba Python, pip, tkinter, las
librerías, FFmpeg y el runtime JS, y **pregunta antes de instalar** lo que
falte.

```
iniciar.bat          revisa y arranca
iniciar.bat /y       instala lo que falte sin preguntar
iniciar.bat /check   solo revisa y sale
iniciar.bat /debug   arranca con registro detallado
```

A mano:

```sh
pip install -r requirements.txt
python main1_new.py
```

### FFmpeg (obligatorio)

**No viene en el repo**: los binarios pasan de 200 MB cada uno (GitHub no
acepta archivos de más de 100 MB) y la build habitual es GPL. Pon `ffmpeg.exe`
y `ffprobe.exe` junto a la app —así queda portable— o instálalos:

```sh
winget install Gyan.FFmpeg
```

### Deno o Node (opcional, recomendado)

yt-dlp los usa para resolver los *challenges* de YouTube: con uno de los dos
hay todos los formatos disponibles y bastantes menos bloqueos, sin cookies.

```sh
winget install DenoLand.Deno
```

### Requisitos

Windows 10 u 11 · Python 3.10+ con tkinter · FFmpeg y ffprobe

---

## Problemas típicos

**El video pide instalar un códec, o se ve pero no se oye.**
YouTube ofrece por defecto VP9 o AV1 con audio Opus. Windows no descodifica
AV1 de serie, y el Opus dentro de un MP4 no lo oye casi ningún reproductor: el
sonido está ahí, pero es ilegible. Usa el códec **Compatible** (el que viene
puesto), que pide H.264 + AAC.

**Quiero 4K compatible y no me deja.**
YouTube no ofrece H.264 por encima de 1080p; arriba solo hay VP9 y AV1. Tienes
tres salidas: quedarte en 1080p, usar **MKV** (aguanta esos códecs) o marcar
**Recodificar**, que lo pasa a H.264 + AAC (tarda, pero se reproduce donde sea).

**Error 429 / «YouTube está limitando tu IP».**
Pon la velocidad en **Seguro** y, si hace falta, un límite en el campo
*Límite* (`500K`). Es temporal.

**Video con restricción de edad, privado o de miembros.**
Usa *Exportar cookies*. Hay que **cerrar el navegador por completo** antes
(incluido el icono de la bandeja) o la base de datos está bloqueada. El
archivo de cookies se borra al cerrar la app.

**El conversor «solo detecta MP3».**
Casi siempre es que en esa carpeta no hay otro audio. Al elegir carpeta, la
app dice cuántos archivos descartó y por qué (`45 que no son audio: 44 .webp,
1 .part`). Si lo que tienes son videos, marca *Sacar el audio de los videos*.

**Me sobran archivos `.webp` y `.part`.**
Son restos de descargas que fallaron a medias. El botón **Limpiar sobras**
(junto a la carpeta de destino) los encuentra y los borra, preguntando antes.
No toca audio, video ni tus `.jpg`.

**La playlist no devuelve nada.**
Si es privada o de pago, necesita cookies.

---

## Estructura

```
main1_new.py        punto de entrada: comprueba dependencias y arranca
iniciar.bat         lanzador que verifica e instala requisitos
ytdown/
  app.py            la ventana: las tres pestañas y su lógica
  queue_model.py    la cola (grupos de playlist, selección, dedup), sin UI
  downloader.py     opciones de yt-dlp y descarga con rotación de clientes
  converter.py      conversión con FFmpeg, escaneo de carpetas
  analyzer.py       metadatos en segundo plano, con peticiones espaciadas
  history.py        historial persistente: no repetir descargas
  info.py           metadatos y normalización de URLs
  cookies.py        exportar cookies del navegador
  deps.py           qué falta y cómo instalarlo
  jsruntime.py      detección de Deno/Node
  network.py        impersonación TLS (curl_cffi)
  updater.py        auto-actualización de yt-dlp
  widgets.py        widgets reutilizables (filas recicladas, avisos)
  constants.py      paleta, rutas y formatos
  config.py         carga y guardado de ajustes
```

Dos decisiones que explican buena parte del código: la cola y la lista de
archivos **no saben nada de tkinter**, y las filas de la interfaz se
**reciclan** en vez de recrearse (reconstruir una playlist de 200 canciones en
cada evento de progreso dejaba la ventana inservible).

---

## Aviso

Las Condiciones de Servicio de YouTube no permiten descargar contenido sin
autorización, y descargar material con derechos de autor puede ser ilegal en
tu país. Esta herramienta es para contenido propio, de dominio público o con
licencia que lo permita. Úsala bajo tu responsabilidad.

[customtkinter]: https://github.com/TomSchimansky/CustomTkinter
[yt-dlp]: https://github.com/yt-dlp/yt-dlp
[FFmpeg]: https://ffmpeg.org
