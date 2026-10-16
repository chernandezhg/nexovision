"""Monitor ARQ2 offline: agente en el worker y panel/CSV en el master.

Solo requiere psutil (ya instalado en los entornos del proyecto).
No consulta servicios externos. HTTP del agente es de solo lectura y debe
escuchar exclusivamente en la interfaz privada del laboratorio.
"""

import argparse
import csv
import json
import math
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import psutil


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Sampler:
    def __init__(self, interface, interval=1.0):
        if interface not in psutil.net_if_addrs():
            raise ValueError(
                f"No existe la interfaz {interface!r}. Disponibles: "
                + ", ".join(psutil.net_if_addrs())
            )
        self.interface = interface
        self.interval = interval
        self.lock = threading.Lock()
        self.value = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        previous = None
        first = True
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                cpu = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory()
                counters = psutil.net_io_counters(pernic=True).get(self.interface)
                link = psutil.net_if_stats().get(self.interface)
                now = time.monotonic()
                rx = tx = None
                if previous and counters:
                    elapsed = now - previous[0]
                    if elapsed > 0:
                        rx = max(0, counters.bytes_recv - previous[1]) / elapsed / 125000
                        tx = max(0, counters.bytes_sent - previous[2]) / elapsed / 125000
                previous = (now, counters.bytes_recv, counters.bytes_sent) if counters else None
                celery_pids = []
                for process in psutil.process_iter(["pid", "cmdline"]):
                    try:
                        command = " ".join(process.info["cmdline"] or [])
                        if "celery" in command and "src.cluster_tasks" in command:
                            celery_pids.append(process.info["pid"])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                value = {
                    "hostname": socket.gethostname(),
                    "sample_timestamp": utc_now(),
                    "cpu_percent": None if first else cpu,
                    "ram_percent": memory.percent,
                    "ram_used_mb": round(memory.used / 1048576, 1),
                    "uptime_s": max(0, time.time() - psutil.boot_time()),
                    "interface": self.interface,
                    "link_up": bool(link and link.isup),
                    "link_mbps": link.speed if link and link.speed > 0 else None,
                    "rx_mbps": rx,
                    "tx_mbps": tx,
                    "celery_processes": len(celery_pids),
                }
                first = False
            except Exception as error:
                value = {"error": str(error)}
            with self.lock:
                self.value = (time.monotonic(), value)
            self.stop.wait(max(0, self.interval - (time.monotonic() - started)))

    def snapshot(self):
        with self.lock:
            if self.value is None:
                raise RuntimeError("Recogiendo primera muestra")
            sampled, data = self.value
            if "error" in data:
                raise RuntimeError(data["error"])
            result = dict(data)
        result["sample_age_s"] = time.monotonic() - sampled
        if result["sample_age_s"] > max(5, self.interval * 3):
            raise RuntimeError("El muestreador no entrega datos recientes")
        return result


FIELDS = [
    "timestamp", "timestamp_local", "node", "reachable", "http_rtt_ms", "availability_samples_pct",
    "cpu_percent", "ram_percent", "ram_used_mb", "uptime_s", "interface",
    "link_up", "link_mbps", "rx_mbps", "tx_mbps", "celery_processes",
    "hostname", "sample_timestamp", "sample_age_s", "error",
]


class Dashboard:
    def __init__(self, sampler, remote, output_dir, interval):
        self.sampler = sampler
        self.remote = remote.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))
        self.interval = interval
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.csv_lock = threading.Lock()
        self.history = {"master": deque(maxlen=180), "worker": deque(maxlen=180)}
        self.counts = {name: [0, 0] for name in self.history}
        self.events = deque(maxlen=100)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.csv_path = output_dir / f"monitor_{stamp}.csv"
        self.events_path = output_dir / f"monitor_eventos_{stamp}.csv"
        self.csv_file = self.csv_path.open("x", newline="", encoding="utf-8")
        self.events_file = self.events_path.open("x", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.csv_file, fieldnames=FIELDS)
        self.writer.writeheader()
        self.events_writer = csv.writer(self.events_file)
        self.events_writer.writerow(["timestamp", "timestamp_local", "event"])
        self.csv_file.flush()
        self.events_file.flush()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def remote_snapshot(self):
        request = Request(self.remote + "/metrics", headers={"Accept": "application/json"})
        started = time.perf_counter()
        with self.opener.open(request, timeout=1.5) as response:
            result = json.loads(response.read(65536))
        rtt = (time.perf_counter() - started) * 1000
        if not isinstance(result, dict) or "cpu_percent" not in result or "sample_age_s" not in result:
            raise ValueError("Respuesta del agente no válida")
        age = result["sample_age_s"]
        if not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= 5:
            raise ValueError("El agente entregó una muestra antigua o no válida")
        return result, rtt

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            for name in self.history:
                row = {"node": name, "reachable": 0}
                try:
                    if name == "master":
                        row.update(self.sampler.snapshot())
                    else:
                        metrics, rtt = self.remote_snapshot()
                        row.update(metrics)
                        row["http_rtt_ms"] = rtt
                    row["reachable"] = 1
                except (OSError, ValueError, RuntimeError, HTTPError, URLError) as error:
                    row["error"] = str(error)
                row["timestamp"] = utc_now()
                row["timestamp_local"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
                with self.lock:
                    stats = self.counts[name]
                    stats[0] += 1
                    stats[1] += row["reachable"]
                    row["availability_samples_pct"] = 100 * stats[1] / stats[0]
                    self.history[name].append(row)
                with self.csv_lock:
                    self.writer.writerow({key: row.get(key, "") for key in FIELDS})
                    self.csv_file.flush()
            self.stop.wait(max(0, self.interval - (time.monotonic() - started)))

    def state(self):
        with self.lock:
            return {
                "history": {key: list(value) for key, value in self.history.items()},
                "events": list(self.events),
                "csv": self.csv_path.name,
                "events_csv": self.events_path.name,
            }

    def event(self, label):
        item = {
            "timestamp": utc_now(),
            "timestamp_local": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "event": label,
        }
        with self.csv_lock:
            self.events_writer.writerow([item["timestamp"], item["timestamp_local"], label])
            self.events_file.flush()
        with self.lock:
            self.events.append(item)
        return item

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        with self.csv_lock:
            self.csv_file.close()
            self.events_file.close()


PAGE = r'''<!doctype html>
<html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARQ2 · Monitor del clúster</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101822;color:#e4edf5;font:15px system-ui,sans-serif}
main{max-width:1300px;margin:auto;padding:24px}h1{font-size:25px;margin:0 0 6px}p{color:#a9b8c9;line-height:1.5}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{background:#192633;border:1px solid #344454;border-radius:14px;padding:20px}
.top{display:flex;justify-content:space-between;gap:12px;align-items:center}.state{font-weight:700;color:#5ae4b1}.bad{color:#ff9c9c}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}.stats span{display:block;color:#a9b8c9;font-size:12px}.stats b{font-size:23px}
canvas{display:block;width:100%;height:120px;margin:6px 0 14px}h2{font-size:19px;margin:0}h3{font-size:13px;color:#a9b8c9;margin:15px 0 0}.line{font-size:13px;color:#b9c9d9;line-height:1.8;overflow-wrap:anywhere}
button{background:#2d4c63;border:1px solid #67839c;color:white;padding:11px 15px;border-radius:8px;cursor:pointer;margin:4px}button:hover{background:#3b637f}
#events{max-height:150px;overflow:auto;font:13px ui-monospace,monospace;line-height:1.7;white-space:pre-wrap}footer{font-size:12px;color:#9cb0c2;margin-top:20px;overflow-wrap:anywhere}
@media(max-width:800px){.grid{grid-template-columns:1fr}.stats b{font-size:20px}main{padding:12px}}
</style>
<main><h1>ARQ2 · Monitor del clúster</h1><p>Telemetría local por Ethernet · CPU y RAM del equipo completo · historial de 180 muestras</p>
<p id="status" role="status">Conectando con el monitor local…</p><section class="grid" id="cards"></section>
<section class="card" style="margin-top:18px"><h2>Marcas de la prueba</h2><p>Registra cada acción al realizarla. La marca documenta tu clic; no detecta automáticamente cuándo retiraste el cable.</p>
<button data-event="Inicio de prueba">Inicio</button><button data-event="Desconexión de cable indicada por el usuario">Desconecto Pavilion</button><button data-event="Reconexión de cable indicada por el usuario">Reconecto Pavilion</button><button data-event="Fin de prueba">Fin</button><div id="events"></div></section>
<footer id="files"></footer><footer>RTT HTTP mide la consulta de telemetría, no ping ICMP ni latencia de inferencia. «Celery» cuenta procesos del proyecto, no confirma que estén conectados a Redis o listos. «Sin respuesta» puede significar caída del agente o de la red; no prueba que el equipo esté apagado. La disponibilidad es la proporción de muestras respondidas desde que arrancó este monitor.</footer></main>
<script>
const cards=document.getElementById('cards');
for(const [key,title] of [['master','ProBook · master'],['worker','Pavilion · worker']]){
 const el=document.createElement('article');el.className='card';el.id=key;
 el.innerHTML=`<div class="top"><h2>${title}</h2><span class="state">Esperando</span></div><div class="stats"><div><span>CPU</span><b class="cpu">—</b></div><div><span>RAM</span><b class="ram">—</b></div><div><span>RTT HTTP</span><b class="rtt">—</b></div></div><div class="line"></div><h3>CPU / RAM · 0–100 %</h3><canvas class="usage"></canvas><h3>Ethernet · recibido / enviado (Mb/s)</h3><canvas class="net"></canvas><h3>Consulta de telemetría · RTT HTTP (ms)</h3><canvas class="latency"></canvas>`;
 cards.append(el);
}
const fmt=(v,d=1)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
function uptime(s){if(typeof s!=='number')return '—';return `${Math.floor(s/3600)} h ${Math.floor(s%3600/60)} min`;}
function chart(canvas,rows,series,fixedMax){
 const w=Math.max(200,canvas.clientWidth),h=120,ratio=window.devicePixelRatio||1;canvas.width=w*ratio;canvas.height=h*ratio;
 const c=canvas.getContext('2d');c.scale(ratio,ratio);const pad=30,bottom=h-20;
 const values=rows.flatMap(r=>series.map(s=>r.reachable?r[s[0]]:null)).filter(v=>typeof v==='number'&&Number.isFinite(v));
 const max=fixedMax||Math.max(1,...values)*1.1;c.font='10px system-ui';c.fillStyle='#91a6bb';
 for(let i=0;i<=2;i++){const y=12+(bottom-12)*i/2;c.strokeStyle='#304252';c.beginPath();c.moveTo(pad,y);c.lineTo(w-5,y);c.stroke();c.fillText((max*(1-i/2)).toFixed(max>10?0:1),0,y+3);}
 series.forEach(([field,color],index)=>{c.strokeStyle=color;c.lineWidth=1.8;c.beginPath();let pen=false;
 rows.forEach((r,i)=>{const v=r.reachable?r[field]:null;if(typeof v!=='number'||!Number.isFinite(v)){pen=false;return;}const x=pad+i*(w-pad-5)/Math.max(1,rows.length-1),y=bottom-(bottom-12)*v/max;if(!pen)c.moveTo(x,y);else c.lineTo(x,y);pen=true;});c.stroke();c.fillStyle=color;c.fillText(field.replace('_percent','').replace('_mbps',''),pad+index*115,h-4);});
}
let lastSuccess=0;
async function update(){
 try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(4000)});if(!response.ok)throw Error('HTTP '+response.status);const state=await response.json();lastSuccess=Date.now();
 document.getElementById('status').textContent='Monitor actualizado: '+new Date(lastSuccess).toLocaleTimeString();
 for(const key of ['master','worker']){const el=document.getElementById(key),rows=state.history[key]||[],r=rows.at(-1)||{},online=r.reachable===1;
 const badge=el.querySelector('.state');badge.textContent=online?'Responde':'Sin respuesta';badge.classList.toggle('bad',!online);
 el.querySelector('.cpu').textContent=online?fmt(r.cpu_percent)+' %':'—';el.querySelector('.ram').textContent=online?fmt(r.ram_percent)+' %':'—';el.querySelector('.rtt').textContent=key==='master'?'Local':online?fmt(r.http_rtt_ms)+' ms':'—';
 el.querySelector('.line').textContent=online?`${r.hostname} · ${r.interface} · enlace ${r.link_up?'UP':'DOWN'} · ${fmt(r.link_mbps,0)} Mb/s\nUptime: ${uptime(r.uptime_s)} · Procesos Celery: ${r.celery_processes} · Disponibilidad de muestras: ${fmt(r.availability_samples_pct)} %`:`Sin telemetría reciente. ${r.error||'Esperando primera muestra'}`;
 chart(el.querySelector('.usage'),rows,[['cpu_percent','#58d6bc'],['ram_percent','#b4a0ff']],100);
 chart(el.querySelector('.net'),rows,[['rx_mbps','#65baff'],['tx_mbps','#ffca77']]);
 chart(el.querySelector('.latency'),rows,[['http_rtt_ms','#ffca77']]);
 }
 document.getElementById('events').textContent=state.events.slice().reverse().map(e=>e.timestamp+' · '+e.event).join('\n');
 document.getElementById('files').textContent='Archivos en resultados/: '+state.csv+' / '+state.events_csv;
 }catch(error){document.getElementById('status').textContent='No se pudo actualizar el panel. Los indicadores anteriores pueden estar obsoletos. '+error.message;for(const el of document.querySelectorAll('.state')){el.textContent='Panel sin actualizar';el.classList.add('bad');}}
 setTimeout(update,1000);
}
for(const button of document.querySelectorAll('[data-event]'))button.onclick=async()=>{button.disabled=true;try{const response=await fetch('/api/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:button.dataset.event}),signal:AbortSignal.timeout(4000)});if(!response.ok)throw Error('HTTP '+response.status);}catch(e){alert('No se guardó la marca: '+e.message);}finally{button.disabled=false;}};
update();
</script></html>'''


def handler_for(sampler, dashboard=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if dashboard and path == "/":
                    self.send(200, PAGE, "text/html; charset=utf-8")
                elif dashboard and path == "/api/state":
                    self.send(200, json.dumps(dashboard.state(), allow_nan=False))
                elif not dashboard and path == "/metrics":
                    self.send(200, json.dumps(sampler.snapshot(), allow_nan=False))
                else:
                    self.send(404, json.dumps({"error": "Ruta no disponible"}))
            except (RuntimeError, ValueError) as error:
                self.send(503, json.dumps({"error": str(error)}))

        def do_POST(self):
            if not dashboard or self.path != "/api/event":
                self.send(404, '{}')
                return
            # El navegador del panel es local: no aceptar marcas desde otros sitios.
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                self.send(403, '{}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("Tamaño no válido")
                label = json.loads(self.rfile.read(length)).get("event", "")
                if not isinstance(label, str) or not label.strip() or len(label) > 120:
                    raise ValueError("Marca no válida")
                self.send(200, json.dumps(dashboard.event(label.strip())))
            except (ValueError, AttributeError, UnicodeError) as error:
                self.send(400, json.dumps({"error": str(error)}))

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    agent = commands.add_parser("agent", help="Telemetría de la Pavilion")
    agent.add_argument("--bind", default="192.168.100.11")
    agent.add_argument("--port", type=int, default=8765)
    agent.add_argument("--interface", required=True)
    panel = commands.add_parser("dashboard", help="Panel y CSV en la ProBook")
    panel.add_argument("--bind", default="127.0.0.1")
    panel.add_argument("--port", type=int, default=8766)
    panel.add_argument("--interface", required=True)
    panel.add_argument("--remote", default="http://192.168.100.11:8765")
    panel.add_argument("--interval", type=float, default=1.0)
    panel.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "resultados")
    args = parser.parse_args()
    if args.mode == "dashboard" and (not math.isfinite(args.interval) or args.interval < 0.5):
        parser.error("--interval debe ser finito y al menos 0.5 segundos")
    sampler = Sampler(args.interface)
    dashboard = None
    server = None
    try:
        server = ThreadingHTTPServer((args.bind, args.port), handler_for(sampler))
        sampler.start()
        if args.mode == "dashboard":
            dashboard = Dashboard(sampler, args.remote, args.output_dir, args.interval)
            server.RequestHandlerClass = handler_for(sampler, dashboard)
            dashboard.thread.start()
            print(f"Panel: http://{args.bind}:{args.port}", flush=True)
            print(f"Métricas: {dashboard.csv_path.resolve()}", flush=True)
            print(f"Eventos: {dashboard.events_path.resolve()}", flush=True)
        else:
            print(f"Agente: http://{args.bind}:{args.port}/metrics", flush=True)
        print("Ctrl+C para terminar. No cierres esta terminal durante la prueba.", flush=True)
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nMonitor detenido.", flush=True)
    finally:
        sampler.stop.set()
        if dashboard:
            dashboard.close()
        if server:
            server.server_close()


if __name__ == "__main__":
    main()
