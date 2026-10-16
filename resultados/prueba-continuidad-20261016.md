# Prueba de continuidad con ausencia y reincorporación del worker

## Fuente y alcance

- CSV aportado por el usuario: `cluster_20261016_121746.csv`.
- Fuente conservada en esta conversación: `/workspace/attachments/8deb7ce8-8af2-4a67-98cd-7f0b1547e1da/cluster_20261016_121746.csv`.
- Análisis realizado: 2026-10-16.
- El usuario informó que desconectó y reconectó Ethernet de la Pavilion durante la ejecución y observó que el video seguía actualizándose.
- Los tiempos siguientes son los del coordinador en el CSV; no incluyen zona horaria. No se convierten a UTC ni a hora de Guatemala.
- Configuración de la prueba indicada en la conversación: cola compartida `cluster`, dos workers de concurrencia 1 y prefetch 1, coordinador con `--timeout 5 --max-pending 2`, comunicación cableada por switch y túnel SSH inverso para Redis.

## Resultados

| Medida | Resultado |
| --- | ---: |
| Filas/resultados exitosos registrados | 1,274 |
| Resultados de `arq2-master` | 1,032 |
| Resultados de `arq2-worker` | 242 |
| Primer resultado | 2026-10-16 12:17:47.457196 |
| Último resultado | 2026-10-16 12:19:22.718243 |
| Duración entre primer y último resultado | 95.261047 s |
| IDs de tarea duplicados en el CSV | 0 |
| Cola de todas las filas | `cluster` |
| Mayor intervalo sin resultados de ningún nodo en toda la prueba | 203.241 ms |

### Intervalo principal sin resultados de la Pavilion

| Evento/medida | Resultado |
| --- | --- |
| Último resultado de Pavilion antes de la ausencia | 12:18:22.446435 |
| Primer resultado de Pavilion después de la ausencia | 12:19:15.310222 |
| Intervalo entre ambos resultados de Pavilion | 52.863787 s |
| Resultados de ProBook estrictamente dentro de ese intervalo | 790 |
| Primer resultado de ProBook dentro del intervalo | 12:18:22.481566 |
| Último resultado de ProBook dentro del intervalo | 12:19:15.258171 |
| Frecuencia observada de entrega durante el intervalo | 14.944 resultados/s |
| Mayor separación entre resultados dentro del intervalo, incluyendo sus límites | 154.966 ms |
| Tiempo ida/vuelta medio de tareas de ProBook en ese intervalo | 70.878 ms |
| Tiempo medio dentro del worker ProBook en ese intervalo | 41.019 ms |
| Inferencia media ProBook en ese intervalo | 33.858 ms |

Las últimas filas vuelven a mostrar resultados de ambos nodos. El CSV evidencia reincorporación al procesamiento, pero no identifica si ocurrió por reconexión automática o reinicio manual del worker/túnel.

## Interpretación para el informe

Durante un intervalo de 52.86 segundos sin resultados del nodo `arq2-worker`, el nodo `arq2-master` continuó entregando 790 resultados de inferencia, con una frecuencia observada de 14.94 resultados por segundo. La mayor separación entre resultados durante dicho intervalo fue de 154.97 ms. Posteriormente se registraron nuevamente resultados de `arq2-worker`. La secuencia es consistente con la desconexión y reconexión reportadas por el usuario y respalda la continuidad del servicio de inferencia ante la ausencia del worker, así como su posterior reincorporación.

## Método reproducible de cálculo

1. Leer las filas del CSV y convertir `timestamp` a fecha/hora sin zona; ordenar por ese campo.
2. Contar filas por `worker` y comprobar duplicados de `task_id` entre los resultados registrados.
3. Para Pavilion, calcular la separación temporal entre cada par de resultados consecutivos y seleccionar la mayor.
4. Seleccionar resultados de ProBook con timestamp estrictamente entre los dos extremos de ese intervalo.
5. Calcular frecuencia como `790 / 52.863787`.
6. Calcular el máximo intervalo entre resultados consecutivos de ProBook dentro del tramo, añadiendo como extremos los resultados de Pavilion que delimitan el tramo. El resultado es 0.154966 s.
7. Para el máximo global, calcular diferencias consecutivas de todas las filas ordenadas, independientemente del worker.

## Límites de la evidencia

- 52.86 s es un intervalo sin resultados del worker remoto; no una medición independiente y exacta de cuánto estuvo físicamente desconectado el cable.
- El CSV registra tareas completadas correctamente, no todas las enviadas, expiradas, perdidas o fallidas. No demuestra recuperación de cada fotograma pendiente, ausencia de pérdida de tareas ni ejecución exactamente una vez.
- El timestamp corresponde a la recogida del resultado por el coordinador, no al instante de desconexión ni necesariamente al fin exacto de inferencia en el worker.
- La frecuencia observada de resultados no es el FPS nativo de captura de cámara ni mide precisión del modelo.
- La continuidad de resultados no implica latencia cero ni alta disponibilidad completa: master, Redis y switch únicos siguen siendo puntos únicos de fallo.
- El CSV no prueba por sí solo aislamiento de Internet ni velocidad física Gigabit. Esas condiciones requieren evidencias separadas.
- El retorno de resultados de Pavilion no prueba recuperación automática del proceso o del túnel SSH.
- No comparar rendimiento global de este ensayo con un benchmark normal de dos nodos: la disponibilidad de nodos cambia durante la prueba.

## Evidencia adicional recomendada

- Conservar el CSV original junto con este análisis en el repositorio de la ProBook.
- Video o registro de eventos que marque desconexión/reconexión y muestre el nodo que responde.
- Para la entrega, repetir la demostración aislada de Internet, con cámara y productos adecuados al caso de uso.
- Documentar por separado los pasos necesarios para reincorporar un worker y las limitaciones del túnel SSH temporal.

Este informe está guardado en el espacio de esta conversación; no se ha copiado automáticamente al repositorio de las laptops ni se ha realizado un commit.
