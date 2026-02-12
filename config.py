
from sqlalchemy import create_engine
from urllib.parse import quote_plus

# ================= URL ETL - TARIFAS =================
url = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search"
resource_id = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
limit = 500
offset = 0

# ================= DADOS BD =================
USER = 'servico_gestao'
PASSWORD = quote_plus('ServicoGest@o')
DATABASE = 'SERVICO_GESTAO'
HOST = 'database-1-instance-1.ct2xbxhjm2gi.us-east-1.rds.amazonaws.com'

def get_engine():
    return create_engine(
        f"mysql+pymysql://{USER}:{PASSWORD}@{HOST}:3306/{DATABASE}", 
        pool_pre_ping=True
    )