import requests
import pandas as pd
import openpyxl
import functions as f
import config as co
import mysql.connector
import time
from requests.exceptions import RequestException

# ================= CONFIG =================
url = co.url
resource_id = co.resource_id
limit = co.limit
offset = co.offset
time.sleep(1)
# ==========================================

all_rows = []

# ================= EXTRAÇÃO =================
while True:
    params = {
        "resource_id": resource_id,
        "limit": limit,
        "offset": offset
    }

    try:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
    except RequestException:
        print("Timeout... tentando novamente em 5 segundos")
        time.sleep(5)
        continue

    if r.status_code != 200:
        print("Erro:", r.status_code)
        break

    data = r.json()
    rows = data["result"]["records"]

    if not rows:
        break

    all_rows.extend(rows)
    print(f"Baixados {len(all_rows)} registros...")

    offset += limit

df = pd.DataFrame(all_rows)
print("Total de linhas:", len(df))

# ================= PADRONIZAÇÃO =================
df = f.padronizar_colunas(df)

df = df.rename(columns={
    "_ID": "ID",
    "DSCREH": "REH",
    "VLRTUSD": "TUSD",
    "VLRTE": "TE",
    "DATINICIOVIGENCIA": "DATA_INICIO",
    "DATFIMVIGENCIA": "DATA_FIM",
    "SIGAGENTE": "SIGLA_AGENTE",
    "DSCSUBGRUPO": "CLASSE_TENSAO",
    "NOMPOSTOTARIFARIO": "POSTO_TARIFARIO",
    "DSCMODALIDADETARIFARIA": "THS",
    "DSCUNIDADETERCIARIA": "UNIDADE_MEDIDA",
    "DSCSUBCLASSE": "SUB_CLASSE"
})

df["THS"] = df["THS"].astype(str).str.strip().str.title()
df["POSTO_TARIFARIO"] = df["POSTO_TARIFARIO"].astype(str).str.strip().str.title()
df["UNIDADE_MEDIDA"] = df["UNIDADE_MEDIDA"].astype(str).str.strip().str.upper()
df["DSCBASETARIFARIA"] = df["DSCBASETARIFARIA"].str.strip()
df["DSCDETALHE"] = df["DSCDETALHE"].str.strip()


# ================= EXTRAÇÃO REATIVA (Grupo B1) =================
df_reativa = df[
    (df["CLASSE_TENSAO"].str.contains("B1", na=False)) &
    (df["THS"].str.contains("Convencional", case=False, na=False)) &
    (df["SUB_CLASSE"].str.contains("RESIDENCIAL", case=False, na=False)) &
    (df["DSCBASETARIFARIA"] == "Tarifa de Aplicação") &
    (df["UNIDADE_MEDIDA"].str.contains("MWH", case=False, na=False))
][
    ["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","TE"]
].copy()

df_reativa = df_reativa.rename(columns={"TE": "REATIVA"})

df_reativa["REATIVA"] = pd.to_numeric(
    df_reativa["REATIVA"].astype(str).str.replace(",", ".", regex=False),
    errors="coerce"
)

# GARANTE 1 LINHA POR PERÍODO
df_reativa = (
    df_reativa
    .groupby(["SIGLA_AGENTE","DATA_INICIO","DATA_FIM"], as_index=False)
    .agg({"REATIVA": "first"})
)
# ================= FILTROS IMPORTANTES =================
df = df[
    (df["DSCBASETARIFARIA"] == "Tarifa de Aplicação") &
    (df["DSCDETALHE"] == "APE") &
    (df["THS"].str.contains("Azul|Verde", case=False, na=False))
]

# ================= CONVERSÃO NUMÉRICA CORRETA =================
df["TUSD"] = pd.to_numeric(
    df["TUSD"].astype(str).str.replace(",", ".", regex=False),
    errors="coerce"
)

df["TE"] = pd.to_numeric(
    df["TE"].astype(str).str.replace(",", ".", regex=False),
    errors="coerce"
)

# ================= AJUSTE POSTO =================
df["POSTO_TARIFARIO"] = df["POSTO_TARIFARIO"].replace({
    "Não Se Aplica": "Fora Ponta",
    "Nao Se Aplica": "Fora Ponta"
})

# ================= SEPARAÇÃO DEMANDA / ENERGIA =================
df_dem = df[df["UNIDADE_MEDIDA"].str.contains("KW", na=False)]
df_ene = df[df["UNIDADE_MEDIDA"].str.contains("MWH", na=False)]

# ================= PIVOT DEMANDA =================
dem = df_dem.pivot_table(
    index=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS"],
    columns="POSTO_TARIFARIO",
    values="TUSD",
    aggfunc="first"
).reset_index()

dem = dem.rename(columns={
    "Ponta":"DEM_P",
    "Fora Ponta":"DEM_FP"
})

# ================= PIVOT ENERGIA =================
ene = df_ene.pivot_table(
    index=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS","POSTO_TARIFARIO"],
    values=["TUSD","TE"],
    aggfunc="first"
).reset_index()

ene_p = ene[ene["POSTO_TARIFARIO"]=="Ponta"].rename(
    columns={"TUSD":"TUSD_ENCARGOS_P","TE":"TE_P"}
)

ene_fp = ene[ene["POSTO_TARIFARIO"]=="Fora Ponta"].rename(
    columns={"TUSD":"TUSD_ENCARGOS_FP","TE":"TE_FP"}
)

# ================= MERGE FINAL =================
tarifas = dem.merge(
    ene_p[["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS","TUSD_ENCARGOS_P","TE_P"]],
    on=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS"],
    how="left"
).merge(
    ene_fp[["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS","TUSD_ENCARGOS_FP","TE_FP"]],
    on=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS"],
    how="left"
)

# ================= MERGE REATIVA =================
tarifas = tarifas.merge(
    df_reativa,
    on=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM"],
    how="left"
)
# ================= RENOMEAÇÃO FINAL =================
tarifas = tarifas.rename(columns={
    "SIGLA_AGENTE":"DISTRIBUIDORA",
    "THS":"MOD_TARIFARIA"
})

# ================= TIPO DE TARIFA =================

tarifas["TIPO_TARIFA"] = "APE"

# ================= ORDEM COLUNAS =================
tarifas = tarifas[[
    "DISTRIBUIDORA","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","MOD_TARIFARIA","TIPO_TARIFA",
    "DEM_P","DEM_FP",
    "TUSD_ENCARGOS_P","TUSD_ENCARGOS_FP",
    "TE_P","TE_FP",
    "REATIVA"
]]

# ================= ARREDONDAMENTO =================
cols_valores = [
    "DEM_P","DEM_FP",
    "TUSD_ENCARGOS_P","TUSD_ENCARGOS_FP",
    "TE_P","TE_FP","REATIVA"
]

tarifas[cols_valores] = tarifas[cols_valores].round(2)


# ================= EXPORTAÇÃO =================
tarifas.to_excel("Tarifas_ANEEL_APE.xlsx", index=False)

# ================= EXPANSÃO MENSAL =================

# Garantir datetime
tarifas["DATA_INICIO"] = pd.to_datetime(tarifas["DATA_INICIO"])
tarifas["DATA_FIM"] = pd.to_datetime(tarifas["DATA_FIM"])

# Criar lista de meses por linha
tarifas["MES_REF"] = tarifas.apply(
    lambda row: pd.date_range(
        start=row["DATA_INICIO"],
        end=row["DATA_FIM"],
        freq="MS"   # início de cada mês
    ),
    axis=1
)

# Explodir meses
tarifas_mensal = tarifas.explode("MES_REF")

# Criar coluna ano-mês formatada
tarifas_mensal["ANO_MES"] = tarifas_mensal["MES_REF"].dt.strftime("%Y-%m")

# ================= EXPORTAÇÃO 2 =================
tarifas_mensal.to_excel("Tarifas_APE_Base_Mensal_ANEEL.xlsx", index=False)

print("Arquivo gerado com sucesso!")
