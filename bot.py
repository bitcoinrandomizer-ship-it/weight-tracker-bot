import os
import logging
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
import json
import re
import asyncio

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurazione
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS')  # JSON delle credenziali come stringa
SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Tracciamento Peso')
TIMEZONE = pytz.timezone('Europe/Rome')
PORT = int(os.environ.get('PORT', 10000))  # Porta per Render

class WeightTrackerBot:
    def __init__(self):
        """Inizializza il bot e la connessione a Google Sheets"""
        self.setup_google_sheets()
    
    def setup_google_sheets(self):
        """Configura la connessione a Google Sheets"""
        try:
            # Parse delle credenziali JSON dalla variabile d'ambiente
            creds_dict = json.loads(GOOGLE_CREDENTIALS)
            
            # Definisci gli scope necessari
            scope = ['https://spreadsheets.google.com/feeds',
                    'https://www.googleapis.com/auth/drive']
            
            # Crea le credenziali
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            
            # Autorizza il client
            self.gc = gspread.authorize(creds)
            
            # Apri o crea il foglio di calcolo
            try:
                self.spreadsheet = self.gc.open(SPREADSHEET_NAME)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.gc.create(SPREADSHEET_NAME)
                logger.info(f"Creato nuovo foglio: {SPREADSHEET_NAME}")
            
            # Ottieni o crea il foglio di lavoro
            try:
                self.worksheet = self.spreadsheet.worksheet('Pesi')
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(title='Pesi', rows=1000, cols=10)
                # Aggiungi intestazioni
                self.worksheet.update('A1:E1', [['User ID', 'Username', 'Data', 'Peso (kg)', 'Timestamp']])
                logger.info("Creato nuovo worksheet 'Pesi' con intestazioni")
                
            logger.info("✅ Connessione a Google Sheets stabilita con successo")
            
        except Exception as e:
            logger.error(f"❌ Errore nella configurazione di Google Sheets: {e}")
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
        logger.info(f"Utente {user.id} ({user.username}) ha avviato il bot")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per il comando /help"""
        await self.start(update, context)
    
    async def register_weight(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per registrare il peso"""
        user = update.effective_user
        
        # Verifica che sia stato fornito un argomento
        if not context.args:
            await update.message.reply_text(
                "❌ Per favore specifica il tuo peso.\n"
                "Esempio: `/peso 75.5`",
                parse_mode='Markdown'
            )
            return
        
        # Estrai e valida il peso
        try:
            weight_str = context.args[0].replace(',', '.')
            weight = float(weight_str)
            
            # Verifica che il peso sia ragionevole
            if weight < 20 or weight > 300:
                await update.message.reply_text(
                    "❌ Il peso deve essere compreso tra 20 e 300 kg."
                )
                return
            
            # Ottieni la data corrente nel timezone italiano
            now = datetime.now(TIMEZONE)
            date_str = now.strftime('%Y-%m-%d')
            timestamp_str = now.strftime('%Y-%m-%d %H:%M:%S')
            
            # Cerca se esiste già un record per oggi
            all_records = self.worksheet.get_all_records()
            today_record_row = None
            
            for idx, record in enumerate(all_records, start=2):  # start=2 perché row 1 sono le intestazioni
                if (str(record.get('User ID')) == str(user.id) and 
                    record.get('Data') == date_str):
                    today_record_row = idx
                    break
            
            # Prepara i dati da salvare
            data = [
                str(user.id),
                user.username or user.first_name,
                date_str,
                weight,
                timestamp_str
            ]
            
            # Salva o aggiorna il record
            if today_record_row:
                # Aggiorna il record esistente
                self.worksheet.update(f'A{today_record_row}:E{today_record_row}', [data])
                message = f"✅ Peso aggiornato: **{weight} kg**\n📅 Data: {date_str}"
                logger.info(f"Peso aggiornato per utente {user.id}: {weight} kg")
            else:
                # Aggiungi nuovo record
                self.worksheet.append_row(data)
                message = f"✅ Peso registrato: **{weight} kg**\n📅 Data: {date_str}"
                logger.info(f"Nuovo peso registrato per utente {user.id}: {weight} kg")
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except ValueError:
            await update.message.reply_text(
                "❌ Formato peso non valido. Usa un numero.\n"
                "Esempio: `/peso 75.5`",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Errore nel salvare il peso: {e}")
            await update.message.reply_text(
                "❌ Si è verificato un errore nel salvare il peso. Riprova più tardi."
            )
    
    async def weekly_average(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Calcola e mostra la media della settimana precedente"""
        user = update.effective_user
        
        try:
            # Calcola le date della settimana precedente
            today = datetime.now(TIMEZONE).date()
            days_since_monday = today.weekday()
            
            # Lunedì della settimana scorsa
            last_monday = today - timedelta(days=days_since_monday + 7)
            # Domenica della settimana scorsa
            last_sunday = last_monday + timedelta(days=6)
            
            # Ottieni tutti i record
            all_records = self.worksheet.get_all_records()
            
            # Filtra i record dell'utente nella settimana precedente
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
                    f"📊 **Media settimanale**\n\n"
                    f"Periodo: {last_monday.strftime('%d/%m/%Y')} - {last_sunday.strftime('%d/%m/%Y')}\n\n"
                    f"❌ Nessun peso registrato nella settimana precedente.",
                    parse_mode='Markdown'
                )
                return
            
            # Calcola la media
            weights_only = [w['peso'] for w in weekly_weights]
            average = sum(weights_only) / len(weights_only)
            
            # Ordina per data
            weekly_weights.sort(key=lambda x: x['data'])
            
            # Crea il messaggio di risposta
            message = (
                f"📊 **Media settimanale**\n\n"
                f"Periodo: {last_monday.strftime('%d/%m/%Y')} - {last_sunday.strftime('%d/%m/%Y')}\n\n"
                f"**Media: {average:.1f} kg**\n"
                f"Registrazioni: {len(weekly_weights)}\n\n"
                f"**Dettaglio:**\n"
            )
            
            for w in weekly_weights:
                message += f"• {w['data'].strftime('%d/%m')}: {w['peso']:.1f} kg\n"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            logger.info(f"Media settimanale calcolata per utente {user.id}: {average:.1f} kg")
            
        except Exception as e:
            logger.error(f"Errore nel calcolare la media: {e}")
            await update.message.reply_text(
                "❌ Si è verificato un errore nel calcolare la media. Riprova più tardi."
            )
    
    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mostra gli ultimi 7 pesi registrati"""
        user = update.effective_user
        
        try:
            # Ottieni tutti i record
            all_records = self.worksheet.get_all_records()
            
            # Filtra i record dell'utente
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
                await update.message.reply_text(
                    "📈 Non hai ancora registrato nessun peso."
                )
                return
            
            # Ordina per data (più recente prima) e prendi gli ultimi 7
            user_records.sort(key=lambda x: x['data'], reverse=True)
            recent_records = user_records[:7]
            
            # Calcola la differenza con il peso precedente
            message = "📈 **Storico pesi (ultimi 7)**\n\n"
            
            for i, record in enumerate(recent_records):
                weight_str = f"{record['peso']:.1f} kg"
                
                # Calcola differenza con il record precedente
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
            
            # Aggiungi statistiche
            if len(recent_records) > 1:
                weights = [r['peso'] for r in recent_records]
                avg = sum(weights) / len(weights)
                message += f"\n📊 Media: **{avg:.1f} kg**"
                
                # Trend
                if weights[0] < weights[-1]:
                    message += f"\n📉 Trend: -{weights[-1] - weights[0]:.1f} kg"
                elif weights[0] > weights[-1]:
                    message += f"\n📈 Trend: +{weights[0] - weights[-1]:.1f} kg"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            logger.info(f"Storico visualizzato per utente {user.id}")
            
        except Exception as e:
            logger.error(f"Errore nel recuperare lo storico: {e}")
            await update.message.reply_text(
                "❌ Si è verificato un errore nel recuperare lo storico. Riprova più tardi."
            )

async def main():
    """Funzione principale per avviare il bot"""
    
    # Verifica che le variabili d'ambiente siano configurate
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN non configurato!")
        return
    
    if not GOOGLE_CREDENTIALS:
        logger.error("❌ GOOGLE_CREDENTIALS non configurato!")
        return
    
    logger.info("🚀 Avvio del bot...")
    
    # Inizializza il bot
    bot = WeightTrackerBot()
    
    # Crea l'applicazione con le nuove impostazioni per v20
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Aggiungi gli handler
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("peso", bot.register_weight))
    application.add_handler(CommandHandler("media", bot.weekly_average))
    application.add_handler(CommandHandler("storico", bot.history))
    
    # Inizializza l'applicazione
    await application.initialize()
    await application.start()
    
    logger.info("✅ Bot avviato con successo!")
    
    # Usa polling per mantenere il bot attivo
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    # Mantieni il bot in esecuzione
    try:
        # Crea un evento che non si verificherà mai per mantenere il bot attivo
        stop_event = asyncio.Event()
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("⏹️ Arresto del bot...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == '__main__':
    # Esegui il bot con asyncio
    asyncio.run(main())