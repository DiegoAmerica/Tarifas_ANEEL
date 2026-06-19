# ================================================================
#  upsert.py — insere / atualiza TARIFAS e TARIFAS_MENSAL no MySQL
#
#  PKs:
#    TARIFAS        → (DISTRIBUIDORA, DATA_INICIO, DATA_FIM,
#                      CLASSE_TENSAO, MOD_TARIFARIA, TIPO_TARIFA)
#    TARIFAS_MENSAL → (DISTRIBUIDORA, DATA_INICIO, DATA_FIM,
#                      CLASSE_TENSAO, MOD_TARIFARIA, TIPO_TARIFA, MES_REF)
#    TARIFAS_MENSAL UNIQUE → (DISTRIBUIDORA, CLASSE_TENSAO,
#                              MOD_TARIFARIA, TIPO_TARIFA, ANO_MES)
# ================================================================

import mysql.connector
import pandas as pd
import numpy as np
import time
import config as co
import extracao_tarifas as ex       # dispara download + transformação ao importar


# ----------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------
def _conectar():
    """Abre uma nova conexão com o RDS. Chamada antes de cada operação
    para evitar timeout por conexão ociosa durante o download."""
    return mysql.connector.connect(
        host               = co.DB_HOST,
        port               = co.DB_PORT,
        user               = co.DB_USER,
        password           = co.DB_PASSWORD,
        database           = co.DB_DATABASE,
        connection_timeout = 30,
        autocommit         = False,
    )


def _sanitize_df(df: pd.DataFrame, date_cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    df = df.replace({np.nan: None})
    df = df.where(df.notna(), other=None)
    return df


def _verificar_nat(valores: list[tuple], colunas: list[str]) -> bool:
    encontrou = False
    for i, row in enumerate(valores):
        for j, val in enumerate(row):
            if type(val).__name__ == "NaTType":
                print(f"  ⚠ NaT: linha {i}, coluna {j} ({colunas[j]})")
                encontrou = True
    return encontrou


def _upsert_em_lotes(cursor, sql: str, valores: list[tuple], label: str):
    total     = len(valores)
    inseridos = 0
    for i in range(0, total, co.BATCH_INSERT):
        lote = valores[i : i + co.BATCH_INSERT]
        cursor.executemany(sql, lote)
        inseridos += len(lote)
        print(f"  {label}: {inseridos:>7} / {total} linhas inseridas")
    return inseridos


def _upsert(df: pd.DataFrame, tabela: str, sql: str,
            colunas: list[str], date_cols: list[str], label: str):
    """Abre conexão própria, faz o upsert e fecha. Evita timeout."""

    print(f"\n{'='*55}")
    print(f"  {label} → {tabela} ({len(df)} linhas)")
    print(f"{'='*55}")

    df = _sanitize_df(df, date_cols)
    valores = [tuple(row[c] for c in colunas) for _, row in df[colunas].iterrows()]

    if _verificar_nat(valores, colunas):
        print("  ABORTADO: NaT encontrado nos dados.")
        return

    conn   = _conectar()
    cursor = conn.cursor()
    try:
        total = _upsert_em_lotes(cursor, sql, valores, label)
        conn.commit()
        print(f"  ✅ {total} linhas confirmadas.")
    except Exception as e:
        conn.rollback()
        print(f"  ❌ Erro — rollback executado: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


# ----------------------------------------------------------------
# EXPANSÃO MENSAL A PARTIR DO BANCO
# ----------------------------------------------------------------
def _expandir_mensal_do_banco(tipo_tarifa: str) -> pd.DataFrame:
    """Abre conexão própria, lê TARIFAS e expande por mês."""
    conn  = _conectar()
    query = "SELECT * FROM TARIFAS WHERE TIPO_TARIFA = %s"
    df    = pd.read_sql(query, conn, params=(tipo_tarifa,))
    conn.close()

    df["DATA_INICIO"] = pd.to_datetime(df["DATA_INICIO"])
    df["DATA_FIM"]    = pd.to_datetime(df["DATA_FIM"])

    linhas = []
    for _, row in df.iterrows():
        if pd.isna(row["DATA_INICIO"]) or pd.isna(row["DATA_FIM"]):
            continue
        meses = pd.date_range(
            start=row["DATA_INICIO"].to_period("M").to_timestamp(),
            end=row["DATA_FIM"], freq="MS"
        )
        for mes in meses:
            fim_mes    = mes + pd.offsets.MonthEnd(1)
            nova_linha = row.to_dict()
            nova_linha["MES_REF"]     = mes
            nova_linha["DATA_INICIO"] = max(row["DATA_INICIO"], mes)
            nova_linha["DATA_FIM"]    = min(row["DATA_FIM"], fim_mes)
            linhas.append(nova_linha)

    df_m = pd.DataFrame(linhas)
    df_m["ANO_MES"] = df_m["MES_REF"].dt.strftime("%Y-%m")
    return df_m


# ----------------------------------------------------------------
# SQLs
# ----------------------------------------------------------------
SQL_TARIFAS = """
INSERT INTO TARIFAS (
    DISTRIBUIDORA, DATA_INICIO, DATA_FIM, CLASSE_TENSAO,
    MOD_TARIFARIA, TIPO_TARIFA,
    DEM_P, DEM_FP,
    TUSD_ENCARGOS_P, TUSD_ENCARGOS_FP,
    TE_P, TE_FP, REATIVA
)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    DEM_P            = VALUES(DEM_P),
    DEM_FP           = VALUES(DEM_FP),
    TUSD_ENCARGOS_P  = VALUES(TUSD_ENCARGOS_P),
    TUSD_ENCARGOS_FP = VALUES(TUSD_ENCARGOS_FP),
    TE_P             = VALUES(TE_P),
    TE_FP            = VALUES(TE_FP),
    REATIVA          = VALUES(REATIVA);
"""

SQL_TARIFAS_MENSAL = """
INSERT INTO TARIFAS_MENSAL (
    DISTRIBUIDORA, DATA_INICIO, DATA_FIM, CLASSE_TENSAO,
    MOD_TARIFARIA, TIPO_TARIFA,
    DEM_P, DEM_FP,
    TUSD_ENCARGOS_P, TUSD_ENCARGOS_FP,
    TE_P, TE_FP, REATIVA,
    MES_REF, ANO_MES
)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
    DEM_P            = VALUES(DEM_P),
    DEM_FP           = VALUES(DEM_FP),
    TUSD_ENCARGOS_P  = VALUES(TUSD_ENCARGOS_P),
    TUSD_ENCARGOS_FP = VALUES(TUSD_ENCARGOS_FP),
    TE_P             = VALUES(TE_P),
    TE_FP            = VALUES(TE_FP),
    REATIVA          = VALUES(REATIVA);
"""

COLS_TARIFAS = [
    "DISTRIBUIDORA","DATA_INICIO","DATA_FIM","CLASSE_TENSAO",
    "MOD_TARIFARIA","TIPO_TARIFA",
    "DEM_P","DEM_FP","TUSD_ENCARGOS_P","TUSD_ENCARGOS_FP",
    "TE_P","TE_FP","REATIVA"
]

COLS_TARIFAS_MENSAL = COLS_TARIFAS + ["MES_REF","ANO_MES"]

DATE_COLS_ANUAL  = ["DATA_INICIO","DATA_FIM"]
DATE_COLS_MENSAL = ["DATA_INICIO","DATA_FIM","MES_REF"]


# ----------------------------------------------------------------
# EXECUÇÃO — cada etapa abre e fecha sua própria conexão
# ----------------------------------------------------------------
print("Iniciando upsert no MySQL...\n")

# 1. TARIFAS — "Não se aplica"
_upsert(ex.tarifas, "TARIFAS", SQL_TARIFAS, COLS_TARIFAS,
        DATE_COLS_ANUAL, label='TARIFAS ("Não se aplica")')

# 2. TARIFAS — APE
_upsert(ex.tarifas_ape, "TARIFAS", SQL_TARIFAS, COLS_TARIFAS,
        DATE_COLS_ANUAL, label='TARIFAS (APE)')

print("\nBases anuais gravadas. Gerando expansão mensal a partir do banco...")

# 3. TARIFAS_MENSAL — expandida do banco, TIPO_TARIFA = "-"
df_mensal = _expandir_mensal_do_banco(tipo_tarifa="-")
_upsert(df_mensal, "TARIFAS_MENSAL", SQL_TARIFAS_MENSAL,
        COLS_TARIFAS_MENSAL, DATE_COLS_MENSAL, label='TARIFAS_MENSAL ("Não se aplica")')

# 4. TARIFAS_MENSAL — expandida do banco, TIPO_TARIFA = "APE"
df_ape_mensal = _expandir_mensal_do_banco(tipo_tarifa="APE")
_upsert(df_ape_mensal, "TARIFAS_MENSAL", SQL_TARIFAS_MENSAL,
        COLS_TARIFAS_MENSAL, DATE_COLS_MENSAL, label='TARIFAS_MENSAL (APE)')

print("\nProcesso concluído.")