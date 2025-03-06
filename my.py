import time
import requests
import sqlite3
import sys
import os
from flask import Flask, render_template
from dotenv import load_dotenv

try:
    import tronpy
    from tronpy import Tron
    from tronpy.keys import PrivateKey
except ModuleNotFoundError:
    print("Ошибка: модуль 'tronpy' не установлен. Установите его командой: pip install tronpy")
    sys.exit(1)

# Загрузка переменных окружения
load_dotenv()

# Настройки главного кошелька
MAIN_WALLET = os.getenv("MAIN_WALLET")
MAIN_PRIVATE_KEY = os.getenv("MAIN_PRIVATE_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ENERGY_AMOUNT = int(os.getenv("ENERGY_AMOUNT", 100000))  # Количество энергии для делегирования
INITIAL_WALLET_COUNT = int(os.getenv("INITIAL_WALLET_COUNT", 5))  # Количество кошельков при старте

if not MAIN_PRIVATE_KEY:
    print("Ошибка: Приватный ключ главного кошелька не задан! Укажите его в .env файле.")
    sys.exit(1)

client = Tron()
app = Flask(__name__)

# Настройка базы данных
def init_db():
    conn = sqlite3.connect("wallets.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT UNIQUE,
            private_key TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT,
            amount REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            webhook_sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn, cursor

conn, cursor = init_db()

# Функция для генерации нового TRX кошелька
def generate_wallet():
    priv_key = PrivateKey.random()
    wallet = priv_key.public_key.to_base58check_address()
    cursor.execute("INSERT INTO wallets (address, private_key) VALUES (?, ?)", (wallet, str(priv_key)))
    conn.commit()
    return wallet, priv_key

# Функция для генерации начального пула кошельков
def ensure_wallets():
    cursor.execute("SELECT COUNT(*) FROM wallets")
    wallet_count = cursor.fetchone()[0]
    if wallet_count < INITIAL_WALLET_COUNT:
        for _ in range(INITIAL_WALLET_COUNT - wallet_count):
            generate_wallet()
        print(f"Создано {INITIAL_WALLET_COUNT - wallet_count} новых кошельков.")

# Функция для получения всех кошельков из БД
def get_wallets():
    cursor.execute("SELECT address, private_key FROM wallets")
    return cursor.fetchall()

# Функция для проверки баланса
def get_balance(address):
    return client.get_account_balance(address)

# Функция отправки вебхука
def send_webhook(wallet, amount):
    payload = {"wallet": wallet, "amount": amount}
    response = requests.post(WEBHOOK_URL, json=payload)
    if response.status_code == 200:
        cursor.execute("UPDATE transactions SET webhook_sent = 1 WHERE wallet = ? AND amount = ?", (wallet, amount))
        conn.commit()

# Функция делегирования энергии
def delegate_energy(from_private_key, to_wallet, amount):
    owner = PrivateKey(from_private_key)
    txn = (
        client.trx.asset_delegate_bandwidth(
            owner.public_key.to_base58check_address(), to_wallet, amount
        ).build().sign(owner)
    )
    return txn.broadcast().wait()

# Функция перевода TRX на главный кошелек
def send_to_main_wallet(from_private_key, from_wallet):
    priv_key = PrivateKey(from_private_key)
    balance = get_balance(from_wallet)
    if balance > 0:
        txn = client.trx.transfer(from_wallet, MAIN_WALLET, balance).build().sign(priv_key)
        txn.broadcast().wait()
        cursor.execute("INSERT INTO transactions (wallet, amount) VALUES (?, ?)", (from_wallet, balance))
        conn.commit()

# Flask-админка
@app.route("/")
def dashboard():
    cursor.execute("SELECT * FROM wallets")
    wallets = cursor.fetchall()
    cursor.execute("SELECT * FROM transactions ORDER BY timestamp DESC")
    transactions = cursor.fetchall()
    return render_template("dashboard.html", wallets=wallets, transactions=transactions)

# Основной цикл проверки баланса
def monitor_wallets():
    while True:
        wallets = get_wallets()
        for wallet, priv_key in wallets:
            balance = get_balance(wallet)
            if balance > 0:
                print(f"Получен платеж: {wallet} - {balance} TRX")
                send_webhook(wallet, balance)
                delegate_energy(MAIN_PRIVATE_KEY, wallet, ENERGY_AMOUNT)
                send_to_main_wallet(priv_key, wallet)
        time.sleep(30)  # Проверяем раз в 30 секунд

if __name__ == "__main__":
    ensure_wallets()
    from threading import Thread
    Thread(target=monitor_wallets, daemon=True).start()
    app.run(debug=True)
