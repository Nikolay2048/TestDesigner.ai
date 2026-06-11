# Warehouse service

Install dependencies and start the service from this directory:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

The service stores all state in memory. `POST /mock/reset` restores catalog data,
inventory, counters, orders, reservations, payments, shipments, deliveries, and
returns.
