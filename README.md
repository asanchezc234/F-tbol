# App de reservas de fútbol (MVP)

Una app web simple para gestionar **reservas/listas** de fútbol (fut5/fut6/fut7), con:

- **Cupo automático**: 10/12/14 (según fut5/fut6/fut7).
- **Lista de apuntados**: alta manual y vista del partido.
- **Pagos**: marcar quién pagó / quién debe.
- **Cancelaciones**: registrar cancelaciones para analíticas.
- **Analíticas**: historial por jugador (tasa de cancelación, tasa de pago, “riesgo” básico).
- **Modo WhatsApp (sin integración)**: pegas texto exportado/copiado del grupo y la app arma la lista.

> Nota sobre WhatsApp: la API oficial (WhatsApp Business Cloud) **no permite** leer mensajes de un grupo “normal” de WhatsApp de manera directa. Este MVP resuelve el flujo con “pegar chat exportado” o con “link” para apuntarse.

## Requisitos

- Python 3.11+

## Cómo correr en local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Abrí `http://localhost:8000`.

## Flujo recomendado (WhatsApp)

- Creás el partido en la app (o usando “Modo WhatsApp” pegando el chat exportado).
- La app te muestra la lista y te deja marcar **pagó / canceló**.
- (Opcional) Compartís un link `/join/...` para que cada uno se apunte sin tener que copiar/pegar.