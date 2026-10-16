# ARQ2 — Clúster local de inferencia de visión

Proyecto académico con dos laptops Linux, cámara integrada de la ProBook, modelo YOLO11n en OpenVINO y procesamiento CPU distribuido con Celery/Redis. La operación puede realizarse sin Internet una vez instalados los paquetes y almacenado el modelo en ambas laptops. La aceptación del hardware de laptops se basa en la aclaración docente comunicada en la conversación y debe respaldarse con su evidencia; el PDF original restringe el hardware. pfSense se omite por indicación del usuario.

## Arquitectura implementada

| Componente | ProBook / `arq2-master` | Pavilion / `arq2-worker` |
| --- | --- | --- |
| IP Ethernet estática | `192.168.100.10/24` | `192.168.100.11/24` |
| Interfaz | `enp2s0` | `enxec9a0c000d47` |
| Conexión | Ethernet a switch Gigabit | USB–RJ45 a switch Gigabit |
| Funciones | Cámara, coordinador, Redis, worker local, panel | Worker remoto y agente de telemetría |
| Modelo | `yolo11n_openvino_model/` | Copia del mismo modelo |

El switch dedicado no necesita conexión al router. No se configura gateway para la subred del clúster. El Wi-Fi es opcional durante preparación; se desactiva en ambas laptops para demostrar operación aislada de Internet.

### Servicios y protocolos

| Servicio | Dirección/puerto | Dependencias y alcance |
| --- | --- | --- |
| Redis en master | `127.0.0.1:6379/TCP` | Broker y resultados de Celery, sin exposición directa a la LAN |
| SSH en worker | `192.168.100.11:22/TCP` | Túnel inverso iniciado desde master; requiere servicio SSH de Pavilion activo |
| Redis visto desde worker | `127.0.0.1:6379/TCP` | Puerto remoto del túnel que reenvía al Redis local del master |
| Agente de telemetría | `192.168.100.11:8765/TCP`, HTTP | Solo interfaz privada; lectura de métricas mediante psutil |
| Panel de monitoreo | `127.0.0.1:8766/TCP`, HTTP | Acceso desde navegador local de ProBook |
| Cola de inferencia | `cluster` | Los dos workers consumen de la misma cola con concurrencia 1 y prefetch 1 |

El coordinador publica fotogramas JPEG codificados en base64. El worker devuelve detecciones, su hostname y tiempos. Redis se alcanza por loopback en cada laptop, pero en Pavilion ese acceso depende del túnel SSH. El túnel debe mantenerse abierto; no depende de Internet.

## Archivos importantes

- `src/cluster_tasks.py`: aplicación Celery y tarea `arq2.infer_frame`.
- `src/coordinator.py`: cámara, envío de tareas, visualización y CSV; admite `--queue`.
- `src/send_test.py`: prueba de una imagen.
- `src/local_camera.py`: baseline local utilizado durante el desarrollo.
- `src/monitor.py`: agente/panel de monitoreo, gráficas y eventos.
- `resultados/`: CSV de pruebas y análisis Markdown.
- `config/`: inventarios de dependencias, configuración y huellas del modelo cuando se capturen.
- `.venv/`: entorno de cada laptop, no se copia entre equipos ni se versiona.
- `yolo11n_openvino_model/`: modelo exportado, disponible localmente en ambas laptops. Está excluido del Git según la configuración inicial: respaldarlo aparte.

## Dependencias y reproducibilidad

Ambos equipos usan entornos Python 3.12 independientes. En las pruebas, la ProBook fue preparada con uv y Python 3.12.15; Pavilion usa Python 3.12.3 de Ubuntu 24.04. Capturar las versiones reales instaladas, sin asumir que sus paquetes son idénticos.

### Inventario en ProBook

Ejecutar en una terminal LOCAL de ProBook:

```bash
cd ~/arq2-camera
mkdir -p config
~/.local/bin/uv pip freeze --python .venv/bin/python > config/requirements-master.txt
.venv/bin/python --version > config/python-master.txt
ssh user@192.168.100.11 '/home/user/arq2-camera/.venv/bin/python -m pip freeze' > config/requirements-worker.txt
ssh user@192.168.100.11 '/home/user/arq2-camera/.venv/bin/python --version' > config/python-worker.txt
sha256sum yolo11n_openvino_model/*.xml yolo11n_openvino_model/*.bin > config/model-sha256.txt
```

Si cualquier comando falla, detenerse y resolverlo antes de versionar archivos vacíos o incompletos. Los manifiestos de `freeze` registran versiones, pero no incluyen binarios de paquetes ni aseguran que una instalación futura sin Internet pueda descargarlos. Para reinstalar offline se necesitaría un repositorio local de paquetes/wheels, además del modelo. La demostración operativa offline no requiere reinstalar el entorno.

Las dependencias principales son PyTorch CPU, torchvision, Ultralytics 8.4.177, OpenVINO, OpenCV, NumPy, Celery con soporte Redis, psutil y pandas para análisis. Instalar las variantes CPU de PyTorch desde su índice oficial; los manifiestos con versiones `+cpu` pueden requerir dicho índice al restaurar un entorno. El sistema también necesita Redis en master y OpenSSH en worker; esos servicios no quedan incluidos en `pip freeze`.

## Arranque desde equipos encendidos

Mantener cada proceso indicado en una terminal distinta. Los comandos siguientes no son instrucciones para lanzar duplicados de workers ya activos. Si se retoma una sesión, comprobar primero con `pgrep -af 'celery.*src.cluster_tasks'` en cada equipo.

### 1. Comprobaciones de red y Redis — ProBook local

```bash
ip -br address show dev enp2s0
ping -c 4 192.168.100.11
redis-cli ping
```

Redis debe responder `PONG`. Si el servicio no está activo, iniciarlo con `sudo systemctl start redis-server` y repetir la comprobación. Si la red falla, revisar direcciones, cables, switch e interfaces antes de iniciar inferencias.

### 2. Worker Pavilion mediante túnel — terminal de ProBook

```bash
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -R 127.0.0.1:6379:127.0.0.1:6379 user@192.168.100.11
```

El prompt cambia a `user@arq2-worker`. Dentro de esa sesión:

```bash
cd ~/arq2-camera
source .venv/bin/activate
celery -A src.cluster_tasks:app worker --loglevel=INFO --concurrency=1 --hostname=pavilion@%h --queues=cluster --prefetch-multiplier=1
```

Esperar `ready`. Dejar esta terminal abierta: mantiene el túnel y el worker. Un error al reservar el puerto remoto 6379 indica que hay otra sesión o servicio ocupándolo; no continuar con un segundo worker sin revisar.

### 3. Worker ProBook — otra terminal LOCAL

```bash
cd ~/arq2-camera
source .venv/bin/activate
celery -A src.cluster_tasks:app worker --loglevel=INFO --concurrency=1 --hostname=master@%h --queues=cluster --prefetch-multiplier=1
```

Esperar `ready` y mantener abierto.

### 4. Agente de monitoreo — terminal directa en Pavilion

```bash
cd ~/arq2-camera
source .venv/bin/activate
python -m src.monitor agent --interface enxec9a0c000d47
```

Abrirlo directamente en Pavilion permite que el agente sobreviva al cierre de la sesión SSH de Celery. Mantener abierto.

### 5. Panel — otra terminal LOCAL de ProBook

```bash
cd ~/arq2-camera
source .venv/bin/activate
python -m src.monitor dashboard --interface enp2s0
```

Abrir `http://127.0.0.1:8766` en el navegador de ProBook. Verificar que responden ambos agentes. El contador de procesos Celery no equivale a una prueba de que los workers estén listos; verificar también sus terminales o resultados.

### 6. Coordinador — otra terminal LOCAL de ProBook

```bash
cd ~/arq2-camera
source .venv/bin/activate
QT_QPA_PLATFORM=xcb python -m src.coordinator --queue cluster --timeout 30 --max-pending 2
```

Mantener foco en la ventana de cámara y pulsar `q` para terminar. Las métricas se guardan en `resultados/cluster_FECHA_HORA.csv`. Los datos de inferencia deben mostrar `arq2-master` y `arq2-worker`.

Durante las primeras tareas, cargar el modelo puede tardar mucho más que una inferencia con el modelo listo. Dejar calentar antes de medir. `QT_QPA_PLATFORM=xcb` selecciona el backend gráfico usado en las pruebas; el aviso de Wayland observado no impidió la ejecución.

## Medición y evidencia sin Internet

1. Completar descargas y comprobar presencia del modelo en ambos equipos.
2. Iniciar los servicios por las IP Ethernet anteriores, con el switch sin conexión al router.
3. Desactivar Wi-Fi en ambas laptops. Mantener todos los cables del clúster conectados.
4. Ejecutar la cámara y comprobar resultados de ambos nodos y panel local.
5. Para el caso de uso del PDF, mostrar productos apropiados (por ejemplo botellas, tazas o libros) y conservar capturas con sus etiquetas. Los CSV actuales solo cuentan detecciones; no incluyen las clases ni permiten calcular precisión.
6. Marcar Inicio/Fin en el panel y guardar captura/video del funcionamiento, conexiones y estado Wi-Fi. El CSV de inferencia por sí solo no acredita aislamiento de Internet.

El software suministrado de monitoreo no carga librerías web externas. El modelo exportado se abre desde disco. Revisar otras aplicaciones y ajustes de bibliotecas si se quiere documentar ausencia de intentos de comunicación externa; con enlaces externos físicamente deshabilitados, su comunicación no puede salir por la red del clúster.

## Prueba de fallo y recuperación

1. Calentar ambos modelos y verificar resultados de los dos workers.
2. Para la prueba de video ante ausencia del worker se utilizó `--timeout 5 --max-pending 2`, después del calentamiento. Un límite corto puede descartar tareas lentas; no usarlo como tiempo de arranque del modelo.
3. Marcar Inicio; registrar el estado normal. Marcar Desconecto Pavilion al retirar únicamente su cable Ethernet.
4. Verificar que continúan resultados de `arq2-master`, no solo que se actualiza una imagen local de la cámara.
5. Marcar Reconecto Pavilion al reconectar el cable. El agente de monitoreo puede recuperarse si continúa ejecutándose localmente.
6. Si se perdió el SSH, revisar en Pavilion `pgrep -af 'celery.*src.cluster_tasks'` antes de abrir otro worker. Restablecer el túnel y el worker del paso 2 si es necesario. No afirmar que esta recuperación sea automática.
7. Confirmar que regresan resultados de `arq2-worker`. Marcar Fin y terminar la cámara con `q`.

La configuración de tareas incluye confirmación tardía y rechazo si se pierde un proceso de worker. Una pérdida de conectividad de un nodo completo no equivale a matar un hijo de Celery; la recuperación de tareas no confirmadas depende del tiempo de visibilidad de Redis (habitualmente 1 hora salvo configuración distinta). El coordinador registra resultados exitosos y puede descartar tareas vencidas. La prueba realizada demuestra continuidad de inferencia y reincorporación, no entrega garantizada de cada fotograma.

Master/coordinador, Redis y switch únicos siguen siendo puntos únicos de fallo. El alcance de la tolerancia demostrada es ausencia de un worker remoto, no alta disponibilidad completa de todos los componentes.

## Resultados obtenidos

### Continuidad — `cluster_20261016_121746.csv`

- 1,274 resultados: 1,032 de master y 242 de worker.
- Intervalo de 52.863787 s sin resultados de Pavilion.
- Dentro de ese intervalo, master completó 790 inferencias (14.94 resultados/s), sin separaciones mayores de 154.966 ms entre resultados observados.
- Se registraron resultados de Pavilion después del intervalo. La medición es una ausencia observada de resultados, no la duración exacta del corte físico.
- Análisis guardado por el usuario: `resultados/prueba-continuidad-20261016.md`.

### Carga monitorizada — `cluster_20261016_142136.csv`

- Ventana Inicio/Fin: 355.243 s; 6,561 resultados, 18.47 resultados/s.
- Master: 3,890 tareas; inferencia media 17.83 ms; RTT medio 40.78 ms; CPU media 21.04 %; RAM media 35.21 %.
- Worker: 2,671 tareas; inferencia media 45.34 ms; RTT medio 84.04 ms; CPU media 52.55 %; RAM media 27.77 %.
- Ambos agentes respondieron 355/355 muestras del intervalo y reportaron 1000 Mb/s de enlace.
- Análisis guardado por el usuario: `resultados/prueba-carga-monitorizada-20261016.md`.

Estos resultados no constituyen una comparación controlada de velocidad contra un nodo único. No equiparar los resultados/s entregados con FPS del modelo, ni RTT HTTP de telemetría con RTT de inferencia. El monitor incluye CPU de todo el equipo, no solo del modelo; no mide vatios ni consumo energético.

Los CSV nuevos del monitor contienen UTC y hora local con desplazamiento. Los CSV del coordinador usan hora local sin zona; al relacionarlos, usar la zona del master y documentarla. En el ensayo monitorizado se verificó el desplazamiento −06:00.

## Cierre y conservación

- Terminar cámara con `q`; marcar Fin en el panel; detener panel/agente con Ctrl+C cuando se hayan guardado las evidencias.
- Detener workers con Ctrl+C una sola vez y esperar el cierre; después ejecutar `exit` en la terminal SSH para cerrar el túnel.
- Guardar código, manifiestos de configuración, CSV y análisis en Git. No añadir `.venv/`, cachés ni copias `.bak` de trabajo.
- Git local no es una copia externa. Mantener una copia adicional del repositorio y del modelo en otro medio antes de una reinstalación o pérdida del disco.
- No publicar secretos, claves privadas, contraseñas ni datos de cámaras con información personal.

Guía preparada a partir de los comandos, código y evidencias de la conversación. No se ha validado aún el arranque completo desde un reinicio de ambas laptops siguiendo este documento; realizar un ensayo antes de la defensa.
