import os
import json
import datetime
import requests
from collections import defaultdict
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

WHATSAPP_URL = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
DATA_FILE = "gastos.json"

app = Flask(__name__)

# -----------------------------
# DATA
# -----------------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# -----------------------------
# WHATSAPP
# -----------------------------
def send_whatsapp(to, text):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    requests.post(WHATSAPP_URL, headers=headers, json=payload)

# -----------------------------
# GEMINI
# -----------------------------
def analyze_message(text):
    prompt = f"""
Respondé SOLO JSON válido.

Campos:
- tipo: gasto | pago | fiado | balance | desconocido
- monto: number o null
- categoria: string o null

Mensaje:
"{text}"
"""
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )
    try:
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw)
    except Exception:
        return {"tipo": "desconocido", "monto": None, "categoria": None}

# -----------------------------
# BALANCE LOGIC
# -----------------------------
def generate_balance(data):
    monthly = defaultdict(lambda: {"gasto": 0, "pago": 0, "fiado": 0})
    deudas = defaultdict(int)
    pagos = defaultdict(int)

    for r in data:
        date = datetime.date.fromisoformat(r["fecha"])
        key = f"{date.strftime('%B %Y')}"
        monto = r["monto"] or 0

        monthly[key][r["tipo"]] += monto

        if r["tipo"] == "fiado":
            deudas[r["categoria"]] += monto
        elif r["tipo"] == "pago":
            pagos[r["categoria"]] += monto

    response = "📊 RESUMEN FINANCIERO\n\n"

    total_deuda = 0

    for month, vals in monthly.items():
        deuda_mes = vals["fiado"] - vals["pago"]
        total_deuda += max(deuda_mes, 0)

        response += (
            f"📅 {month}\n"
            f"• Gastos: ${vals['gasto']}\n"
            f"• Pagos: ${vals['pago']}\n"
            f"• Fiados: ${vals['fiado']}\n"
            f"• Deuda pendiente: ${max(deuda_mes,0)}\n\n"
        )

    response += "💳 CUENTAS PENDIENTES\n"
    pendientes = []

    for cat, monto in deudas.items():
        pendiente = monto - pagos.get(cat, 0)
        if pendiente > 0:
            pendientes.append((cat, pendiente))

    pendientes.sort(key=lambda x: x[1], reverse=True)

    if not pendientes:
        response += "• No hay deudas pendientes\n"
    else:
        for i, (cat, monto) in enumerate(pendientes, 1):
            response += f"{i}. {cat} — ${monto}\n"

    response += "\n✅ CUENTAS PAGADAS\n"
    pagadas = [cat for cat in pagos if pagos[cat] >= deudas.get(cat, 0)]

    if not pagadas:
        response += "• Ninguna\n"
    else:
        for cat in pagadas:
            response += f"• {cat}\n"

    response += f"\n📌 TOTAL DE DEUDA ACTUAL: ${total_deuda}"

    return response

# -----------------------------
# WEBHOOKS
# -----------------------------
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = msg["from"]
        text = msg["text"]["body"]
    except Exception:
        return "ok"

    result = analyze_message(text)
    registros = load_data()

    if result["tipo"] in ["gasto", "pago", "fiado"]:
        registros.append({
            "fecha": datetime.date.today().isoformat(),
            "tipo": result["tipo"],
            "monto": result["monto"],
            "categoria": result["categoria"],
            "from": sender
        })
        save_data(registros)
        send_whatsapp(sender, f"{result['tipo'].capitalize()} registrado ✔")

    elif result["tipo"] == "balance":
        resumen = generate_balance(registros)
        send_whatsapp(sender, resumen)

    else:
        send_whatsapp(sender, "No entendí el mensaje. Ej: 'fiado supermercado 12000'")

    return "ok"
