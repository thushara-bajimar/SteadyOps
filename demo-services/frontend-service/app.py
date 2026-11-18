"""frontend-service: the customer-facing web page of the demo shop.

It serves a tiny HTML page and proxies order lookups to api-service, so a
user-visible journey exists: browser -> frontend -> api -> (database, inventory).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from demo_common import Uptime, log, run  # noqa: E402

API_URL = os.environ.get("API_SERVICE_URL", "http://127.0.0.1:8102")
uptime = Uptime()
app = FastAPI(title="frontend-service")
client = httpx.Client(timeout=3.0)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Demo Shop</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#1e293b}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #e2e8f0;padding:.5rem;text-align:left}
.err{color:#b91c1c}</style></head><body>
<h1>Demo Shop</h1><p>A workload monitored by SteadyOps. Each row is fetched through api-service.</p>
<table><thead><tr><th>Order</th><th>SKU</th><th>Total</th><th>In stock</th></tr></thead><tbody id="rows"></tbody></table>
<script>
async function load(){const rows=document.getElementById('rows');rows.innerHTML='';
for(let i=1;i<=6;i++){const tr=document.createElement('tr');
try{const r=await fetch('/proxy/orders/'+i);const j=await r.json();
tr.innerHTML=r.ok?`<td>#${i}</td><td>${j.sku}</td><td>$${j.total}</td><td>${j.in_stock}</td>`
:`<td>#${i}</td><td class=err colspan=3>Error ${r.status}: ${j.detail||''}</td>`;}
catch(e){tr.innerHTML=`<td>#${i}</td><td class=err colspan=3>unreachable</td>`}
rows.appendChild(tr);}}
load();setInterval(load,5000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/proxy/orders/{order_id}")
def proxy(order_id: int):
    try:
        r = client.get(f"{API_URL}/api/orders/{order_id}")
        return JSONResponse(r.json(), status_code=r.status_code)
    except (httpx.HTTPError, ValueError) as exc:
        log("ERROR", "api-service request failed", dependency="api-service", error=type(exc).__name__)
        return JSONResponse({"detail": "api-service unavailable"}, status_code=502)


@app.get("/health")
def health():
    try:
        r = client.get(f"{API_URL}/health", timeout=2.5)
        api = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except httpx.HTTPError as exc:
        api = f"unreachable ({type(exc).__name__})"
    # The frontend itself is up even if the API is degraded; report both.
    return {"status": "ok", "service": "frontend-service", "checks": {"api": api}, "uptime_s": uptime.seconds()}


if __name__ == "__main__":
    run(app)
