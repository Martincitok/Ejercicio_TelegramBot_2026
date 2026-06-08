from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
import logging, os, ssl, socket
import certifi
import aiomqtt

# Configuración de entorno
token = os.environ["TB_TOKEN"]
autorizados = [int(x) for x in os.environ["TB_AUTORIZADOS"].split(',')]
MQTT_BROKER = os.environ.get("MQTT_BROKER")  
MQTT_PORT = int(os.environ.get("MQTT_PORT"))  

# CORRECCIÓN 3: Ajustados los nombres para que coincidan con tu .env
MQTT_USER = os.environ.get("MQTT_USR")
MQTT_PASS = os.environ.get("MQTT_PASS")

# CORRECCIÓN 1: El tópico base DEBE ser el ID del dispositivo según la consigna
TOPICO_BASE = os.environ.get("PICO_DEVICE_ID")  # martinperret.duckdns.org

# Contexto SSL para MQTTS usando certifi
ssl_context = ssl.create_default_context(cafile=certifi.where())
# Nota: Si el broker de la materia usa IP directa y el certificado es para un dominio,
# podrías necesitar poner check_hostname = False. Por ahora lo dejamos estricto:
ssl_context.check_hostname = True
ssl_context.verify_mode = ssl.CERT_REQUIRED

logging.basicConfig(format='%(asctime)s - TelegramBot - %(levelname)s - %(message)s', level=logging.INFO)
logging.info(f"Inicializando bot. Broker MQTT: {MQTT_BROKER}:{MQTT_PORT} para el dispositivo: {TOPICO_BASE}")

async def sin_autorizacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logging.info(f"Intento de acceso no autorizado de ID: {chat_id}")
    await context.bot.send_message(chat_id=chat_id, text="🔒 No estás autorizado para usar este bot.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    nombre = user.first_name or ""
    
    mensaje = (
        f"👋 ¡Bienvenido, {nombre}!\n\n"
        f"🤖 Centro de control para el dispositivo:\n`{TOPICO_BASE}`\n\n"
        "Podés configurar el setpoint de temperatura, el período de muestreo, el modo de funcionamiento, y controlar el relé.\n\n"
        "Usá el comando /menu para comenzar. 📋"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=mensaje, parse_mode="Markdown")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text="📋 Seleccioná una opción para configurar el termostato:", 
        reply_markup=await generar_teclado_principal()
    )

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("esperando"):
        context.user_data["esperando"] = None
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Operación cancelada.", reply_markup=await generar_teclado_principal())
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="ℹ️ No hay ninguna operación activa.", reply_markup=await generar_teclado_principal())
                                       
async def generar_teclado_principal():
    keyboard = [
        [InlineKeyboardButton("🛠 Setpoint Termostato", callback_data="menu_setpoint")],
        [InlineKeyboardButton("⏱ Periodo de Muestreo", callback_data="menu_periodo")],
        [InlineKeyboardButton("⚙️ Modo (Auto/Manual)", callback_data="menu_modo")],
        [InlineKeyboardButton("🔌 Control de Relé", callback_data="menu_rele")],
        [InlineKeyboardButton("💡 Destello de Alerta", callback_data="menu_destello")],
        [InlineKeyboardButton("❌ Cerrar Menú", callback_data="cancelar_operacion")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_setpoint":
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Ingresar valor", callback_data="ingresar_setpoint")],
            [InlineKeyboardButton("🔙 Volver", callback_data="volver_menu")]
        ])
        await query.edit_message_text("🔧 *Configuración de Setpoint*:\nEstablece la temperatura objetivo.", reply_markup=reply_markup, parse_mode="Markdown")

    elif data == "ingresar_setpoint":
        context.user_data["esperando"] = "setpoint"
        await query.edit_message_text("📥 Ingresá el valor numérico del setpoint (ej. `24.5`):", parse_mode="Markdown")
        
    elif data == "menu_periodo":
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Ingresar segundos", callback_data="ingresar_periodo")],
            [InlineKeyboardButton("🔙 Volver", callback_data="volver_menu")]
        ])
        await query.edit_message_text("⏱ *Configuración de Periodo*:\nTiempo en segundos entre mediciones.", reply_markup=reply_markup, parse_mode="Markdown")

    elif data == "ingresar_periodo":
        context.user_data["esperando"] = "periodo"
        await query.edit_message_text("📥 Ingresá el tiempo en segundos (ej. `30`):", parse_mode="Markdown")

    elif data == "menu_modo":
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Manual (0)", callback_data="modo_0"),
             InlineKeyboardButton("Automático (1)", callback_data="modo_1")],
            [InlineKeyboardButton("🔙 Volver", callback_data="volver_menu")]
        ])
        await query.edit_message_text("⚙️ *Seleccioná el modo de funcionamiento*:", reply_markup=reply_markup, parse_mode="Markdown")

    elif data in ["modo_0", "modo_1"]:
        valor = "0" if data == "modo_0" else "1"
        texto = "Manual 🛠" if valor == "0" else "Automático 🤖"
        topic = f"{TOPICO_BASE}/modo"
        context.user_data["modo_actual"] = valor  
        await query.edit_message_text(f"🔄 Solicitando cambio a *Modo {texto}*", parse_mode="Markdown")
        await publish_mqtt(topic, valor, update, context)

    elif data == "menu_rele":
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Apagar Relé (0)", callback_data="rele_0"),
             InlineKeyboardButton("Encender Relé (1)", callback_data="rele_1")],
            [InlineKeyboardButton("🔙 Volver", callback_data="volver_menu")]
        ])
        await query.edit_message_text("🔌 *Control manual del Relé*:", reply_markup=reply_markup, parse_mode="Markdown")

    elif data in ["rele_0", "rele_1"]:
        valor = "0" if data == "rele_0" else "1"
        estado = "OFF 🔴" if valor == "0" else "ON 🟢"
        topic = f"{TOPICO_BASE}/rele"
        await query.edit_message_text(f"🔄 Enviando orden de Relé: *{estado}*", parse_mode="Markdown")
        await publish_mqtt(topic, valor, update, context)

    elif data == "menu_destello":
        topic = f"{TOPICO_BASE}/destello"
        await query.edit_message_text("💡 Orden de *destello* enviada a la Raspberry Pi Pico.", parse_mode="Markdown")
        await publish_mqtt(topic, "destello", update, context)

    elif data == "volver_menu":
        context.user_data["esperando"] = None
        await query.edit_message_text("📋 Seleccioná una opción:", reply_markup=await generar_teclado_principal())

    elif data == "cancelar_operacion":
        context.user_data["esperando"] = None
        await query.edit_message_text("❌ Menú cerrado. Podés reabrirlo con /menu")


async def capturar_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entrada = update.message.text.strip()
    esperando = context.user_data.get("esperando")

    if not esperando:
        await update.message.reply_text("❓ No hay ninguna acción activa. Usá /menu para interactuar.")
        return

    if esperando == "setpoint":
        # Validación básica de flotante antes de enviar por MQTT
        try:
            float(entrada)
            topic = f"{TOPICO_BASE}/setpoint"
            await update.message.reply_text(f"🚀 Publicando Setpoint: `{entrada}` °C...", parse_mode="Markdown")
            await publish_mqtt(topic, entrada, update, context)
            
            if context.user_data.get("modo_actual") == "0":
                await update.message.reply_text("⚠️ _Nota: El sistema está en Modo Manual. El cambio se guardará pero la Pico no actuará automáticamente hasta cambiar a Modo Auto._", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Error: Por favor ingresá un número válido (ej: 25 o 23.8).")
            return

    elif esperando == "periodo":
        # Validación básica de entero antes de enviar por MQTT
        if entrada.isdigit():
            topic = f"{TOPICO_BASE}/periodo"
            await update.message.reply_text(f"🚀 Publicando Periodo: `{entrada}` segundos...", parse_mode="Markdown")
            await publish_mqtt(topic, entrada, update, context)
        else:
            await update.message.reply_text("❌ Error: El período debe ser un número entero en segundos.")
            return

    context.user_data["esperando"] = None


async def publish_mqtt(topic: str, payload: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    # CORRECCIÓN 2: Captura dinámica del ID del Chat sea mensaje o callback query
    chat_id = update.effective_chat.id

    try:
        ip = socket.gethostbyname(MQTT_BROKER)
        logging.info(f"Resolución DNS exitosa: {MQTT_BROKER} → {ip}")
    except socket.gaierror:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error DNS: No se pudo resolver el broker `{MQTT_BROKER}`", parse_mode="Markdown")
        return False

    try:
        async with aiomqtt.Client(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USER,
            password=MQTT_PASS,
            tls_context=ssl_context
        ) as client:
            logging.info(f"Publicando en {topic}: {payload}")
            # Mandamos el string encodeado a bytes
            await client.publish(topic, payload=payload.encode(), qos=1)
            await context.bot.send_message(chat_id=chat_id, text="📥 ¡Comando entregado al bróker correctamente!")
            return True
    except aiomqtt.MqttError as e:
        logging.error(f"Error en la conexión/publicación MQTT: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Falló el envío MQTTS: `{e}`\nRevisá el estado del bróker y credenciales.", parse_mode="Markdown")
        return False

# Handlers de comandos directos por texto
async def setpoint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Uso: `/setpoint <valor>`", parse_mode="Markdown")
    await publish_mqtt(f"{TOPICO_BASE}/setpoint", context.args[0], update, context)

async def periodo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Uso: `/periodo <segundos>`", parse_mode="Markdown")
    await publish_mqtt(f"{TOPICO_BASE}/periodo", context.args[0], update, context)

async def modo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ["0", "1"]: return await update.message.reply_text("Uso: `/modo <0 o 1>`", parse_mode="Markdown")
    await publish_mqtt(f"{TOPICO_BASE}/modo", context.args[0], update, context)

async def rele_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0] not in ["0", "1"]: return await update.message.reply_text("Uso: `/rele <0 o 1>`", parse_mode="Markdown")
    await publish_mqtt(f"{TOPICO_BASE}/rele", context.args[0], update, context)

async def destello_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await publish_mqtt(f"{TOPICO_BASE}/destello", "destello", update, context)


def main():
    application = Application.builder().token(token).build()
    
    # 1. Filtro estricto de seguridad: Si no es usuario autorizado, se ejecuta 'sin_autorizacion'
    application.add_handler(MessageHandler(~filters.User(autorizados), sin_autorizacion))
    
    # 2. Comandos admitidos
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', menu))
    application.add_handler(CommandHandler('cancelar', cancelar))
    application.add_handler(CommandHandler('setpoint', setpoint_cmd))
    application.add_handler(CommandHandler('periodo', periodo_cmd))
    application.add_handler(CommandHandler('modo', modo_cmd))
    application.add_handler(CommandHandler('rele', rele_cmd))
    application.add_handler(CommandHandler('destello', destello_cmd))
    
    # 3. Callbacks de los botones
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # 4. Captura de texto para inputs de usuario (solo si está autorizado)
    application.add_handler(MessageHandler(filters.TEXT & filters.User(autorizados), capturar_input))
    
    application.run_polling()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Bot detenido.")