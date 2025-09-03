import os
import logging
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
import json
from aiohttp import web

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurazione
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS')
SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Tracciamento Peso')
TIMEZONE = pytz.timezone('Europe/Rome')
PORT = int(os.environ.get('PORT', 10000))
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

class WeightTrackerBot:
    def __init__(self):
        """Inizializza il bot e la connessione a Google Sheets"""
        self.setup_google_sheets()
    
    def setup_google_sheets(self):
        """Configura la connessione a Google Sheets"""
        try:
            creds_dict = json.loads(GOOGLE_CREDENTIALS)
            scope = ['https://spreadsheets.google.com/feeds',
                    'https://www.googleapis.com/auth/drive']
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            self.gc = gspread.authorize(creds)
            
            try:
                self.spreadsheet = self.gc.open(SPREADSHEET_NAME)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.gc.create(SPREADSHEET_NAME)
                logger.info(f"Creato nuovo foglio: {SPREADSHEET_NAME}")
            
            try:
                self.worksheet = self.spreadsheet.worksheet('Pesi')
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(title='Pesi', rows=1000, cols=10)
                self.worksheet.update('A1:E1', [['User ID', 'Username', 'Data', 'Peso (kg)', 'Timestamp']])
                logger.info("Creato nuovo worksheet 'Pesi'")
                
            logger.info("✅ Google Sheets configurato")
            
        except Exception as e:
            logger.error(f"❌ Errore Google Sheets: {e}")
            raise
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per il comando /start"""
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
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.start(update, context)
    
    async def register_weight(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per registrare il peso"""
        user = update.effective_user
        
        if not context.args:
            await update.message.reply_text(
                "❌ Specifica il peso.\nEsempio: `/peso 75.5`",
                parse_mode='Markdown'
            )
            return
        
        try:
            weight = float(context.args[0].replace(',', '.'))
            
            if weight < 20 or weight > 300:
                await update.message.reply_text("❌ Peso deve essere tra 20 e 300 kg.")
                return
            
            now = datetime.now(TIMEZONE)
            date_str = now.strftime('%Y-%m-%d')
            timestamp_str = now.strftime('%Y-%m-%d %H:%M:%S')
            
            all_records = self.worksheet.get_all_records()
            today_record_row = None
            
            for idx, record in enumerate(all_records, start=2):
                if (str(record.get('User ID')) == str(user.id) and 
                    record.get('Data') == date_str):
                    today_record_row = idx
                    break
            
            data = [
                str(user.id),
                user.username or user.first_name,
                date_str,
                weight,
                timestamp_str
            ]
            
            if today_record_row:
                self.worksheet.update(f'A{today_record_row}:E{today_record_row}', [data])
                message = f"✅ Peso aggiornato: **{weight} kg**"
            else:
                self.worksheet.append_row(data)
                message = f"✅ Peso registrato: **{weight} kg**"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except ValueError:
            await update.message.reply_text(
                "❌ Formato non valido.\nEsempio: `/peso 75.5`",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Errore: {e}")
            await update.message.reply_text("❌ Errore nel salvare il peso.")
    
    async def weekly_average(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Calcola la media settimanale"""
        user = update.effective_user
        
        try:
            today = datetime.now(TIMEZONE).date()
            days_since_monday = today.weekday()
            last_monday = today - timedelta(days=days_since_monday + 7)
            last_sunday = last_monday + timedelta(days=6)
            
            all_records = self.worksheet.get_all_records()
            weekly_weights = []
            
            for record in all_records:
                if str(record.get('User ID')) == str(user.id):
                    try:
                        record_date = datetime.strptime(record.get('Data'), '%Y-%m-%d').date()
                        if last_monday <= record_date <= last_sunday:
                            weekly_weights.append({
                                'data': record_date,
                                'peso': float(record.get('Peso (kg)', 0))
                            })
                    except (ValueError, TypeError):
                        continue
            
            if not weekly_weights:
                await update.message.reply_text(
                    f"📊 **Media settimanale**\n"
                    f"Periodo: {last_monday.strftime('%d/%m')} - {last_sunday.strftime('%d/%m')}\n"
                    f"❌ Nessun peso registrato",
                    parse_mode='Markdown'
                )
                return
            
            weights_only = [w['peso'] for w in weekly_weights]
            average = sum(weights_only) / len(weights_only)
            weekly_weights.sort(key=lambda x: x['data'])
            
            message = (
                f"📊 **Media settimanale**\n"
                f"Periodo: {last_monday.strftime('%d/%m')} - {last_sunday.strftime('%d/%m')}\n\n"
                f"**Media: {average:.1f} kg**\n"
                f"Registrazioni: {len(weekly_weights)}\n\n"
                f"**Dettaglio:**\n"
            )
            
            for w in weekly_weights:
                message += f"• {w['data'].strftime('%d/%m')}: {w['peso']:.1f} kg\n"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Errore media: {e}")
            await update.message.reply_text("❌ Errore nel calcolare la media.")
    
    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mostra lo storico"""
        user = update.effective_user
        
        try:
            all_records = self.worksheet.get_all_records()
            user_records = []
            
            for record in all_records:
                if str(record.get('User ID')) == str(user.id):
                    try:
                        user_records.append({
                            'data': datetime.strptime(record.get('Data'), '%Y-%m-%d').date(),
                            'peso': float(record.get('Peso (kg)', 0))
                        })
                    except (ValueError, TypeError):
                        continue
            
            if not user_records:
                await update.message.reply_text("📈 Nessun peso registrato.")
                return
            
            user_records.sort(key=lambda x: x['data'], reverse=True)
            recent_records = user_records[:7]
            
            message = "📈 **Storico pesi (ultimi 7)**\n\n"
            
            for i, record in enumerate(recent_records):
                weight_str = f"{record['peso']:.1f} kg"
                
                if i < len(recent_records) - 1:
                    diff = record['peso'] - recent_records[i + 1]['peso']
                    if diff > 0:
                        diff_str = f" (+{diff:.1f})"
                        emoji = "📈"
                    elif diff < 0:
                        diff_str = f" ({diff:.1f})"
                        emoji = "📉"
                    else:
                        diff_str = " (=)"
                        emoji = "➡️"
                else:
                    diff_str = ""
                    emoji = "📊"
                
                message += f"{emoji} {record['data'].strftime('%d/%m/%Y')}: **{weight_str}**{diff_str}\n"
            
            if len(recent_records) > 1:
                weights = [r['peso'] for r in recent_records]
                avg = sum(weights) / len(weights)
                message += f"\n📊 Media: **{avg:.1f} kg**"
                
                if weights[0] < weights[-1]:
                    message += f"\n📉 Trend: -{weights[-1] - weights[0]:.1f} kg"
                elif weights[0] > weights[-1]:
                    message += f"\n📈 Trend: +{weights[0] - weights[-1]:.1f} kg"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Errore storico: {e}")
            await update.message.reply_text("❌ Errore nel recuperare lo storico.")

async def main():
    """Funzione principale con webhook"""
    
    if not TELEGRAM_TOKEN or not GOOGLE_CREDENTIALS:
        logger.error("❌ Variabili d'ambiente mancanti!")
        return
    
    logger.info("🚀 Avvio bot con webhook...")
    
    # Inizializza bot
    bot = WeightTrackerBot()
    
    # Crea applicazione
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Aggiungi handler
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("peso", bot.register_weight))
    application.add_handler(CommandHandler("media", bot.weekly_average))
    application.add_handler(CommandHandler("storico", bot.history))
    
    # Inizializza
    await application.initialize()
    await application.start()
    
    # Configura webhook
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
        await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
        logger.info(f"✅ Webhook configurato: {webhook_url}")
    
    # Crea server web
    async def handle(request):
        if request.match_info.get('token') == TELEGRAM_TOKEN:
            data = await request.read()
            update = Update.de_json(json.loads(data), application.bot)
            await application.update_queue.put(update)
            return web.Response(text="OK")
        return web.Response(status=403)
    
    async def health(request):
        return web.Response(text="Bot is running!")
    
    app = web.Application()
    app.router.add_post(f"/{TELEGRAM_TOKEN}", handle)
    app.router.add_get("/", health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"✅ Server web avviato su porta {PORT}")
    
    # Mantieni attivo
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await application.stop()
        await application.shutdown()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())