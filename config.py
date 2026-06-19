# ================================================================
#  config.py — credenciais e constantes do projeto tarifas_aneel
# ================================================================
import os
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.getenv("DB_HOST")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_USER     = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DATABASE = os.getenv("DB_DATABASE")

ANEEL_RESOURCE_ID = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
ANEEL_URL_BASE    = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search"

PAGE_SIZE    = 1000
TIMEOUT      = 180
BATCH_INSERT = 500