# ytdown

Descargador de YouTube de escritorio para Windows, con conversor de audio e
historial. Interfaz en español, hecha con [customtkinter] sobre [yt-dlp] y
[FFmpeg].

## Qué hace

**Descargar**
- Audio en MP3, M4A, Opus, FLAC o WAV, con calidad elegible.
- Video con resolución (360p–4K), contenedor (MP4/MKV/WEBM), códec y la opción
  de bajarlo **con o sin sonido**.
- El perfil de códec *Compatible* pide H.264 + AAC explícitamente, que es lo
  que evita el clásico "necesitas instalar un códec" y el video que se ve pero
  no se oye. Un aviso en la propia ventana explica qué va a salir con la
  combinación elegida.
- Playlists plegables: se ven todos los videos y eliges cuáles bajar.
- No repite lo ya descargado (historial por video + formato; si borras el
  archivo, vuelve a permitirlo).
- Tres modos de velocidad y límite de ancho de banda, para cuando YouTube
  empieza a devolver errores 429.

**Convertir** — archivos sueltos o una carpeta entera a MP3, M4A, Opus, OGG,
FLAC o WAV. Conserva etiquetas y portada, iguala volumen, respeta la
estructura de subcarpetas y decide qué hacer si el archivo ya existe.

**Historial** — qué bajaste y de qué enlace. Buscador, copiar enlaces,
exportar a `.txt` o `.csv`, y volver a poner algo en la cola.

## Instalación

Lo más fácil es ejecutar **`iniciar.bat`**: comprueba Python, pip, tkinter, las
librerías, FFmpeg y el runtime JS, y **pregunta antes de instalar** lo que
falte.

```
iniciar.bat          revisa y arranca
iniciar.bat /y       instala lo que falte sin preguntar
iniciar.bat /check   solo revisa
iniciar.bat /debug   arranca con registro detallado
```

A mano:

```sh
pip install -r requirements.txt
python main1_new.py
```

### FFmpeg

**No viene en el repo**: los binarios pasan de 200 MB cada uno (GitHub no
acepta más de 100 MB por archivo) y la build habitual es GPL. Pon `ffmpeg.exe`
y `ffprobe.exe` junto a la app —así queda portable— o instálalos:

```sh
winget install Gyan.FFmpeg
```

### Deno o Node (opcional, recomendado)

yt-dlp los usa para resolver los *challenges* de YouTube: con uno de los dos
hay todos los formatos disponibles y bastantes menos bloqueos, sin necesidad de
cookies.

```sh
winget install DenoLand.Deno
```

## Requisitos

- Windows 10 u 11
- Python 3.10 o superior, con tkinter
- FFmpeg (y ffprobe)

## Aviso

Las Condiciones de Servicio de YouTube no permiten descargar contenido sin
autorización, y descargar material con derechos de autor puede ser ilegal en tu
país. Esta herramienta es para contenido propio, de dominio público o con
licencia que lo permita. Úsala bajo tu responsabilidad.

[customtkinter]: https://github.com/TomSchimansky/CustomTkinter
[yt-dlp]: https://github.com/yt-dlp/yt-dlp
[FFmpeg]: https://ffmpeg.org
