# Prueba de inferencia distribuida con monitoreo — 2026-10-16

## Fuentes

- `cluster_20261016_142136.csv`: 7,011 resultados de inferencia; fuente conservada en `/workspace/attachments/baafc465-c7fb-4454-aa85-ab715a0c9589/cluster_20261016_142136.csv`.
- `monitor_20261016_141955_915224.csv`: 1,276 filas de telemetría; fuente conservada en `/workspace/attachments/9d8b8a4c-6502-4924-99eb-b4e395126b1c/monitor_20261016_141955_915224.csv`.
- `monitor_eventos_20261016_141955_915224.csv`: marcas de Inicio y Fin; fuente conservada en `/workspace/attachments/51e66659-2758-4841-aebc-a00d14e1d1b3/monitor_eventos_20261016_141955_915224.csv`.

## Método y ventana de análisis

Se analizó exclusivamente el intervalo entre las marcas de Inicio y Fin. El coordinador inició antes de Inicio y continuó después de Fin; esos resultados se excluyen de las estadísticas de la ventana. El inicio del CSV precede la marca aproximadamente 21 segundos, por lo que se evita incluir el arranque inicial en el intervalo seleccionado; no se garantiza un protocolo formal de calentamiento idéntico al de otros ensayos.

| Marca | Hora local con desplazamiento | UTC |
| --- | --- | --- |
| Inicio | 2026-10-16 14:21:58.319 −06:00 | 2026-10-16 20:21:58.319 +00:00 |
| Fin | 2026-10-16 14:27:53.562 −06:00 | 2026-10-16 20:27:53.562 +00:00 |

Duración: **355.243 segundos (5 minutos y 55.243 segundos)**.

El CSV del coordinador no contiene zona horaria. Para cruzarlo con la telemetría se interpreta como hora local de la ProBook usando el desplazamiento −06:00 documentado en `timestamp_local` del archivo de eventos. Los valores de la marca UTC se utilizan como límites exactos; la marca local de Fin difiere en 1 ms porque ambos campos se capturan consecutivamente.

Se seleccionan filas cuyos timestamps estén dentro del intervalo, incluidos sus límites. CPU, RAM y red se agregan usando las muestras de telemetría dentro del mismo intervalo. P95 usa interpolación lineal sobre los valores ordenados, con índice `(n − 1) × 0.95`.

## Inferencia distribuida

| Medida | Resultado |
| --- | ---: |
| Resultados dentro de la ventana | 6,561 |
| Resultados ProBook (`arq2-master`) | 3,890 (59.3 %) |
| Resultados Pavilion (`arq2-worker`) | 2,671 (40.7 %) |
| Frecuencia de entrega observada | **18.47 resultados/s** |
| Mayor intervalo entre resultados consecutivos dentro de la ventana | **201 ms**, redondeado |
| Cola registrada | `cluster` |
| IDs duplicados en el CSV completo | 0 |

Frecuencia de entrega: `6561 / 355.243` resultados por segundo de la ventana. Se trata de resultados recogidos por el coordinador, no del FPS nativo de captura ni de la velocidad aislada del modelo.

| Nodo | RTT inferencia medio | RTT inferencia P95 | Tiempo dentro del worker medio | Inferencia media | Inferencia P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ProBook | 40.78 ms | 68.42 ms | 21.88 ms | 17.83 ms | 22.97 ms |
| Pavilion | 84.04 ms | 133.58 ms | 54.35 ms | 45.34 ms | 71.19 ms |

RTT de inferencia incluye envío, colas, procesamiento y recogida del resultado por el coordinador, según su instrumentación. No confundirlo con RTT HTTP del monitor. Ambas laptops procesaron tareas; el reparto no tiene por qué ser exactamente 50/50 con hardware de distinta capacidad y una cola compartida.

## Recursos y red en la misma ventana

| Métrica | ProBook | Pavilion |
| --- | ---: | ---: |
| Muestras respondidas / intentos | 355 / 355 | 355 / 355 |
| CPU media del equipo completo | **21.04 %** | **52.55 %** |
| CPU P95 | 26.10 % | 59.13 % |
| CPU máxima | 30.70 % | 73.00 % |
| RAM media | **35.21 %** | **27.77 %** |
| RAM máxima | 36.30 % | 28.20 % |
| Recepción Ethernet media | 4.21 Mb/s | 4.03 Mb/s |
| Envío Ethernet medio | 3.93 Mb/s | 4.21 Mb/s |
| Enlace negociado en las muestras | 1000 Mb/s | 1000 Mb/s |
| Estado del enlace en las muestras | UP | UP |
| Procesos Celery registrados | 2 | 2 |
| RTT HTTP medio del agente | No aplica, muestra local | 2.65 ms |
| RTT HTTP P95 | No aplica, muestra local | 5.34 ms |

Las interfaces registradas son `enp2s0` en ProBook y `enxec9a0c000d47` en Pavilion. En cada nodo se cuentan procesos padre/hijo de Celery; dos procesos no significan dos workers independientes ni prueban su estado ready. La ejecución de tareas sí está demostrada por los resultados del coordinador.

La disponibilidad observada de las muestras de telemetría es 100 % para ambos agentes dentro de esta ventana. En el archivo completo hay una consulta local fallida al arrancar el monitor, con mensaje «Recogiendo primera muestra»; está fuera de la ventana seleccionada. No interpretarla como una caída del clúster durante la prueba.

## Texto propuesto para el informe

Durante una prueba de 355.243 segundos con la cola compartida `cluster`, el coordinador recibió 6,561 resultados, equivalentes a 18.47 resultados por segundo. La ProBook procesó 3,890 tareas y la Pavilion 2,671. La utilización media de CPU fue de 21.04 % y 52.55 %, respectivamente; la RAM media fue de 35.21 % y 27.77 %. Los agentes de telemetría respondieron en las 355 muestras de cada equipo y registraron enlaces Ethernet de 1000 Mb/s. La inferencia media fue de 17.83 ms en ProBook y 45.34 ms en Pavilion; el RTT medio desde el coordinador fue de 40.78 ms y 84.04 ms. Los resultados demuestran procesamiento en ambos nodos y monitoreo dinámico de recursos durante la carga, sin fallos de consulta observados en el intervalo.

## Qué no demuestra esta prueba

- No hay marcas de desconexión/reconexión: es un ensayo de carga monitorizada, no otra demostración instrumentada de fallo. La prueba de continuidad previa está documentada por separado.
- No acredita aislamiento de Internet; el CSV no contiene estados Wi-Fi ni rutas externas. Conservar evidencia offline separada.
- La columna `detections` cuenta objetos, pero no registra sus clases, etiquetas, imágenes ni ground truth. Estos archivos no permiten verificar que fueran productos ni medir precisión, recall o mAP. Conservar capturas/video de los productos y etiquetas.
- No compara de forma controlada throughput o eficiencia contra un único nodo. Las pruebas previas difieren en carga, muestras y condiciones. No afirmar que dos nodos aceleran la inferencia sin una comparación equivalente.
- Las filas son resultados exitosos; no contienen todas las tareas enviadas, errores o expiraciones. Cero IDs duplicados entre resultados registrados no prueba entrega exactamente una vez.
- Los porcentajes de CPU/RAM son del equipo completo, no solo del proceso de inferencia. El monitor y otras aplicaciones forman parte de esa carga.
- El tráfico Ethernet incluye SSH, Redis, telemetría y otras comunicaciones de esa interfaz. No equivale exclusivamente al tamaño de las imágenes ni es una medida de potencia eléctrica.
- No se midieron vatios, consumo energético ni disponibilidad de master/Redis/switch ante sus propias caídas.

## Conservación

Guardar este análisis y los tres CSV originales en el repositorio de la ProBook, junto con las capturas del monitor y de las detecciones. Este archivo se conserva en el espacio de esta conversación; no se ha copiado automáticamente a las laptops ni se ha realizado un commit.
