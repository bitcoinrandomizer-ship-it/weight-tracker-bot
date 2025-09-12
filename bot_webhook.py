import os
import json
import logging
from datetime import datetime, timedelta

import pytz
import gspread
from google.oauth2.service_account import Credentials

from aiohttp import web
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# --- Logging setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Config ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS')
SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Tracciamento Peso')
TIMEZONE = pytz.timezone('Europe/Rome')
PORT = int(os.environ.get('PORT', '10000'))
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- Bot Core ---
class WeightTrackerBot:
    def __init__(self):
        self.setup_google_sheets()

    def setup_google_sheets(self):
        try:
            creds_dict = json.loads(GOOGLE_CREDENTIALS)
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            self.gc = gspread.authorize(creds)

            try:
                self.spreadsheet = self.gc.open(SPREADSHEET_NAME)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.gc.create(SPREADSHEET_NAME)
                logger.info(f"Creato nuovo foglio: {SPREADSHEET_NAME}")

            try:
                self.weight_sheet = self.spreadsheet.worksheet('Pesi')
            except gspread.WorksheetNotFound:
                self.weight_sheet = self.spreadsheet.add_worksheet(
                    title='Pesi', rows=1000, cols=10)
                self.weight_sheet.update('A1:E1', [[
                    'User ID', 'Username', 'Data', 'Peso (kg)', 'Timestamp'
                ]])
                logger.info("Creato nuovo worksheet 'Pesi'")
            
            try:
                self.notif_sheet = self.spreadsheet.worksheet('Notifiche')
            except gspread.WorksheetNotFound:
                self.notif_sheet = self.spreadsheet.add_worksheet(
                    title='Notifiche', rows=1000, cols=5
                )
                self.notif_sheet.update('A1:C1', [[
                    'User ID', 'Username', 'Attivo'
                ]])
                logger.info("Creato nuovo worksheet 'Notifiche'")


            logger.info("✅ Google Sheets configurato")
        except Exception as e:
            logger.error(f"❌ Errore Google Sheets: {e}")
            raise

    # --- Command handlers ---
    def start(self, update: Update, context: CallbackContext):
        user = update.effective_user
        welcome_message = (
            f"Ciao {user.first_name}! 👋\n\n"
            "Sono il tuo assistente per il tracciamento del peso corporeo.\n\n"
            "📊 **Comandi disponibili:**\n"
            "• `/peso [valore]` - Registra il tuo peso (es: /peso 75.5)\n"
            "• `/media` - Mostra la media della settimana precedente\n"
            "• `/storico` - Mostra gli ultimi 7 pesi registrati\n"
            "• `/help` - Mostra questo messaggio di aiuto\n\n"
            "Inizia registrando il tuo peso oggi!"
        )
        update.message.reply_text(welcome_message, parse_mode='Markdown')

    def help_command(self, update: Update, context: CallbackContext):
        self.start(update, context)

    def register_weight(self, update: Update, context: CallbackContext):
        user = update.effective_user
        if not context.args:
            update.message.reply_text(
                "❌ Specifica il peso.\nEsempio: `/peso 75.5`",
                parse_mode='Markdown'
            )
            return
        try:
            weight = float(context.args[0].replace(',', '.'))
            if weight < 20 or weight > 300:
                update.message.reply_text(
                    "❌ Peso deve essere tra 20 e 300 kg.")
                return

            now = datetime.now(TIMEZONE)
            date_str = now.strftime('%Y-%m-%d')
            timestamp_str = now.strftime('%Y-%m-%d %H:%M:%S')
            all_records = self.weight_sheet.get_all_records()
            today_row = None
            for idx, record in enumerate(all_records, start=2):
                if (str(record.get('User ID')) == str(user.id) and
                        record.get('Data') == date_str):
                    today_row = idx
                    break

            data = [
                str(user.id), user.username or user.first_name,
                date_str, weight, timestamp_str
            ]
            if today_row:
                self.weight_sheet.update(f'A{today_row}:E{today_row}', [data])
                msg = f"✅ Peso aggiornato: **{weight} kg**"
            else:
                self.weight_sheet.append_row(data)
                msg = f"✅ Peso registrato: **{weight} kg**"
            update.message.reply_text(msg, parse_mode='Markdown')

        except ValueError:
            update.message.reply_text(
                "❌ Formato non valido.\nEsempio: `/peso 75.5`",
                parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Errore: {e}")
            update.message.reply_text("❌ Errore nel salvare il peso.")

    def weekly_average(self, update: Update, context: CallbackContext):
        user = update.effective_user
        try:
            today = datetime.now(TIMEZONE).date()
            days_since_monday = today.weekday()
            last_monday = today - timedelta(days=days_since_monday + 7)
            last_sunday = last_monday + timedelta(days=6)

            all_records = self.weight_sheet.get_all_records()
            weekly = []
            for rec in all_records:
                if str(rec.get('User ID')) == str(user.id):
                    try:
                        d = datetime.strptime(rec.get('Data'), '%Y-%m-%d').date()
                        if last_monday <= d <= last_sunday:
                            weekly.append({'data': d, 'peso': float(rec.get('Peso (kg)', 0))})
                    except (ValueError, TypeError):
                        continue

            if not weekly:
                update.message.reply_text(
                    f"📊 **Media settimanale**\n"
                    f"Periodo: {last_monday.strftime('%d/%m')} - {last_sunday.strftime('%d/%m')}\n"
                    f"❌ Nessun peso registrato",
                    parse_mode='Markdown'
                )
                return

            weights = [w['peso'] for w in weekly]
            average = sum(weights) / len(weights)
            weekly.sort(key=lambda x: x['data'])
            msg = (
                f"📊 **Media settimanale**\n"
                f"Periodo: {last_monday.strftime('%d/%m')} - {last_sunday.strftime('%d/%m')}\n\n"
                f"**Media: {average:.1f} kg**\n"
                f"Registrazioni: {len(weekly)}\n\n"
                f"**Dettaglio:**\n"
            )
            for w in weekly:
                msg += f"• {w['data'].strftime('%d/%m')}: {w['peso']:.1f} kg\n"
            update.message.reply_text(msg, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Errore media: {e}")
            update.message.reply_text("❌ Errore nel calcolare la media.")

    def history(self, update: Update, context: CallbackContext):
        user = update.effective_user
        try:
            all_records = self.weight_sheet.get_all_records()
            user_records = []
            for rec in all_records:
                if str(rec.get('User ID')) == str(user.id):
                    try:
                        d = datetime.strptime(rec.get('Data'), '%Y-%m-%d').date()
                        user_records.append({'data': d, 'peso': float(rec.get('Peso (kg)', 0))})
                    except (ValueError, TypeError):
                        continue

            if not user_records:
                update.message.reply_text("📈 Nessun peso registrato.")
                return

            user_records.sort(key=lambda x: x['data'], reverse=True)
            last7 = user_records[:7]
            msg = "📈 **Storico pesi (ultimi 7)**\n\n"
            for i, rec in enumerate(last7):
                weight_str = f"{rec['peso']:.1f} kg"
                if i < len(last7) - 1:
                    diff = rec['peso'] - last7[i + 1]['peso']
                    if diff > 0:
                        msg += f"📈 {rec['data'].strftime('%d/%m/%Y')}: **{weight_str}** (+{diff:.1f})\n"
                    elif diff < 0:
                        msg += f"📉 {rec['data'].strftime('%d/%m/%Y')}: **{weight_str}** ({diff:.1f})\n"
                    else:
                        msg += f"➡️ {rec['data'].strftime('%d/%m/%Y')}: **{weight_str}** (=)\n"
                else:
                    msg += f"📊 {rec['data'].strftime('%d/%m/%Y')}: **{weight_str}**\n"
            if len(last7) > 1:
                weights = [r['peso'] for r in last7]
                avg = sum(weights)/len(weights)
                msg += f"\n📊 Media: **{avg:.1f} kg**"
                if weights[0] < weights[-1]:
                    msg += f"\n📉 Trend: -{weights[-1] - weights[0]:.1f} kg"
                elif weights[0] > weights[-1]:
                    msg += f"\n📈 Trend: +{weights[0] - weights[-1]:.1f} kg"

            update.message.reply_text(msg, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Errore storico: {e}")
            update.message.reply_text("❌ Errore nel recuperare lo storico.")
    
    def toggle_notifica(self, update: Update, context: CallbackContext):
        user = update.effective_user
        if not context.args:
            update.message.reply_text("❌ Usa: `/notifica on` oppure `/notifica off`", parse_mode='Markdown')
            return

        stato = context.args[0].lower()
        if stato not in ["on", "off"]:
            update.message.reply_text("❌ Usa: `/notifica on` oppure `/notifica off`", parse_mode='Markdown')
            return

        all_records = self.notif_sheet.get_all_records()
        user_row = None
        for idx, record in enumerate(all_records, start=2):
            if str(record.get('User ID')) == str(user.id):
                user_row = idx
                break

        if stato == "on":
            data = [str(user.id), user.username or user.first_name, "TRUE"]
            if user_row:
                self.notif_sheet.update(f"A{user_row}:C{user_row}", [data])
            else:
                self.notif_sheet.append_row(data)
            update.message.reply_text("🔔 Notifiche attivate!")
        else:
            if user_row:
                self.notif_sheet.update(f"C{user_row}", [["FALSE"]])
            update.message.reply_text("🔕 Notifiche disattivate!")

    def send_daily_notifications(self, context: CallbackContext):
        try:
            all_records = self.notif_sheet.get_all_records()
            for rec in all_records:
                if str(rec.get('Attivo')).upper() == "TRUE":
                    chat_id = int(rec.get('User ID'))
                    context.bot.send_message(
                        chat_id=chat_id,
                        text="⏰ Buongiorno! Ricordati di registrare il tuo peso con il comando /peso 💪"
                    )
        except Exception as e:
            logger.error(f"Errore invio notifiche: {e}")

# --- Main entrypoint ---
def main():
    if not TELEGRAM_TOKEN or not GOOGLE_CREDENTIALS:
        logger.error("❌ Variabili d'ambiente mancanti!")
        return

    logger.info("🚀 Avvio bot con webhook…")
    bot = WeightTrackerBot()

    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Handlers
    dp.add_handler(CommandHandler("start", bot.start))
    dp.add_handler(CommandHandler("help", bot.help_command))
    dp.add_handler(CommandHandler("peso", bot.register_weight))
    dp.add_handler(CommandHandler("media", bot.weekly_average))
    dp.add_handler(CommandHandler("storico", bot.history))
    dp.add_handler(CommandHandler("notifica", bot.toggle_notifica))

    # --- Webhook support ---
    if RENDER_EXTERNAL_URL:
        updater.start_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
        )
        logger.info(f"✅ Webhook configurato: {RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}")
    else:
        updater.start_polling()
        logger.info("✅ Polling avviato")
    
    # Job giornaliero alle 08:00
    job_queue = updater.job_queue
    job_queue.run_daily(
        bot.send_daily_notifications,
        time=datetime.time(hour=8, minute=0, tzinfo=TIMEZONE)
    )

    updater.idle()

if __name__ == '__main__':
    main()
