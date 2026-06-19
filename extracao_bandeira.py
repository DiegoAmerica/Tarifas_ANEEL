# ================================================================
#  bandeira_tarifaria.py — download + upsert BANDEIRA_TARIFARIA
# ================================================================

import requests
import pandas as pd
import numpy as np
import time
import mysql.connector
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import config as co


# ----------------------------------------------------------------
# DOWNLOAD
# ----------------------------------------------------------------
def _criar_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def baixar_bandeiras() -> pd.DataFrame:
    session     = _criar_session()
    all_records = []
    offset      = 0

    print("Iniciando download — Bandeira Tarifária...")

    while True:
        params = {
            "resource_id": "0591b8f6-fe54-437b-b72b-1aa2efd46e42",
            "limit":       co.PAGE_SIZE,
            "offset":      offset,
            "fields":      "DatCompetencia,NomBandeiraAcionada,VlrAdicionalBandeira",
        }

        try:
            r = session.get(co.ANEEL_URL_BASE, params=params, timeout=co.TIMEOUT)

            if not r.ok:
                print(f"  Erro {r.status_code} no offset {offset}: {r.text[:200]}")
                break

            data = r.json()

            if not data.get("success"):
                print(f"  API retornou erro: {data.get('error')}")
                break

            records = data["result"]["records"]

            if not records:
                print(f"  Sem mais registros no offset {offset}. Concluído.")
                break

            all_records.extend(records)
            total_api = data["result"].get("total", "?")
            print(f"  Offset {offset:>6} → {len(all_records):>6} registros / {total_api} total")

            if len(records) < co.PAGE_SIZE:
                print("  Última página atingida.")
                break

            offset += co.PAGE_SIZE
            time.sleep(0.3)

        except Exception as e:
            print(f"  Erro na requisição (offset {offset}): {e}")
            print("  Aguardando 15s para retomar...")
            time.sleep(15)
            continue   # retenta o mesmo offset

    df = pd.DataFrame(all_records)
    print(f"\nTotal baixado: {len(df)} registros")
    return df


# ----------------------------------------------------------------
# TRANSFORMAÇÃO
# ----------------------------------------------------------------
def transformar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().upper() for c in df.columns]

    df = df.rename(columns={
        "DATCOMPETENCIA":      "DATA",
        "NOMBANDEIRAACIONADA": "BANDEIRA",
        "VLRADICIONALBANDEIRA":"VALOR_ADICIONAL",
    })

    df["DATA"] = pd.to_datetime(df["DATA"], errors="coerce").dt.date

    df["VALOR_ADICIONAL"] = pd.to_numeric(
        df["VALOR_ADICIONAL"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce"
    )

    return df[["DATA","BANDEIRA","VALOR_ADICIONAL"]]


# ----------------------------------------------------------------
# UPSERT
# ----------------------------------------------------------------
SQL = """
INSERT INTO BANDEIRA_TARIFARIA (
    DATA,
    BANDEIRA,
    VALOR_ADICIONAL
)
VALUES (%s,%s,%s)
ON DUPLICATE KEY UPDATE
    VALOR_ADICIONAL = VALUES(VALOR_ADICIONAL);
"""

COLUNAS   = ["DATA","BANDEIRA","VALOR_ADICIONAL"]
DATE_COLS = ["DATA"]


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.replace({np.nan: None})
    df = df.where(df.notna(), other=None)
    return df


def _verificar_nat(valores: list[tuple]) -> bool:
    encontrou = False
    for i, row in enumerate(valores):
        for j, val in enumerate(row):
            if type(val).__name__ == "NaTType":
                print(f"  ⚠ NaT: linha {i}, coluna {j} ({COLUNAS[j]})")
                encontrou = True
    return encontrou


def upsert(df: pd.DataFrame):
    print(f"\n{'='*55}")
    print(f"  BANDEIRA_TARIFARIA — {len(df)} linhas")
    print(f"{'='*55}")

    df = _sanitize_df(df)
    valores = [tuple(row[c] for c in COLUNAS) for _, row in df.iterrows()]

    if _verificar_nat(valores):
        print("  ABORTADO: NaT encontrado nos dados.")
        return

    conn = mysql.connector.connect(
        host               = co.DB_HOST,
        port               = co.DB_PORT,
        user               = co.DB_USER,
        password           = co.DB_PASSWORD,
        database           = co.DB_DATABASE,
        connection_timeout = 30,
        autocommit         = False,
    )
    cursor    = conn.cursor()
    inseridos = 0

    try:
        for i in range(0, len(valores), co.BATCH_INSERT):
            lote = valores[i : i + co.BATCH_INSERT]
            cursor.executemany(SQL, lote)
            inseridos += len(lote)
            print(f"  BANDEIRA_TARIFARIA: {inseridos:>6} / {len(valores)} linhas inseridas")
        conn.commit()
        print(f"  ✅ {inseridos} linhas confirmadas.")
    except Exception as e:
        conn.rollback()
        print(f"  ❌ Erro — rollback executado: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


# ----------------------------------------------------------------
# EXECUÇÃO
# ----------------------------------------------------------------
df_raw     = baixar_bandeiras()

if df_raw.empty:
    raise SystemExit("Nenhum registro baixado. Verifique a conexão ou o resource_id.")

bandeiras  = transformar(df_raw)

print(f"\nRegistros após transformação: {len(bandeiras)}")
print(bandeiras.head())

# Exportação Excel local
bandeiras.to_excel("bandeira_tarifaria_ANEEL.xlsx", index=False)
print("\nExcel exportado: bandeira_tarifaria_ANEEL.xlsx")

# Upsert no MySQL
upsert(bandeiras)