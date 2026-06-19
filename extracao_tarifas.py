# ================================================================
#  extracao.py — download ANEEL + transformação
#
#  Exporta:
#    tarifas         → base anual  "Não se aplica"
#    tarifas_ape     → base anual  APE
#    tarifas_mensal  → base mensal "Não se aplica"  (gerada do banco)
#    tarifas_ape_mensal → base mensal APE            (gerada do banco)
#
#  A expansão mensal é feita aqui apenas para exportação Excel local.
#  O upsert mensal no banco é gerado a partir de TARIFAS / TARIFAS_APE
#  já gravadas, via upsert.py.
# ================================================================

import requests
import pandas as pd
import numpy as np
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import config as co


# ----------------------------------------------------------------
# 1. DOWNLOAD COMPLETO DA API
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


def baixar_tarifas_aneel() -> pd.DataFrame:
    """Baixa todos os registros da API ANEEL e retorna DataFrame bruto."""
    session  = _criar_session()
    all_records = []
    offset   = 0

    print("Iniciando download da API ANEEL...")

    while True:
        params = {
            "resource_id": co.ANEEL_RESOURCE_ID,
            "limit":       co.PAGE_SIZE,
            "offset":      offset,
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
            print(f"  Offset {offset:>7} → {len(all_records):>7} registros / {total_api} total")

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
    print(f"\nTotal baixado: {len(df)} registros\n")
    return df


# ----------------------------------------------------------------
# 2. PADRONIZAÇÃO DO DATAFRAME BRUTO
# ----------------------------------------------------------------
def _padronizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().upper() for c in df.columns]

    df = df.rename(columns={
        "_ID":                    "ID",
        "DSCREH":                 "REH",
        "VLRTUSD":                "TUSD",
        "VLRTE":                  "TE",
        "DATINICIOVIGENCIA":      "DATA_INICIO",
        "DATFIMVIGENCIA":         "DATA_FIM",
        "SIGAGENTE":              "SIGLA_AGENTE",
        "DSCSUBGRUPO":            "CLASSE_TENSAO",
        "NOMPOSTOTARIFARIO":      "POSTO_TARIFARIO",
        "DSCMODALIDADETARIFARIA": "THS",
        "DSCUNIDADETERCIARIA":    "UNIDADE_MEDIDA",
        "DSCSUBCLASSE":           "SUB_CLASSE",
    })

    df["THS"]              = df["THS"].astype(str).str.strip().str.title()
    df["POSTO_TARIFARIO"]  = df["POSTO_TARIFARIO"].astype(str).str.strip().str.title()
    df["UNIDADE_MEDIDA"]   = df["UNIDADE_MEDIDA"].astype(str).str.strip().str.upper()
    df["DSCBASETARIFARIA"] = df["DSCBASETARIFARIA"].astype(str).str.strip()
    df["DSCDETALHE"]       = df["DSCDETALHE"].astype(str).str.strip()

    df["DATA_INICIO"] = pd.to_datetime(df["DATA_INICIO"], errors="coerce")
    df["DATA_FIM"]    = pd.to_datetime(df["DATA_FIM"],    errors="coerce")

    return df


# ----------------------------------------------------------------
# 3. EXTRAÇÃO REATIVA (Grupo B1) — comum às duas bases
# ----------------------------------------------------------------
def _extrair_reativa(df: pd.DataFrame) -> pd.DataFrame:
    mask = (
        df["CLASSE_TENSAO"].str.contains("B1", na=False) &
        df["THS"].str.contains("Convencional", case=False, na=False) &
        df["SUB_CLASSE"].str.contains("RESIDENCIAL", case=False, na=False) &
        (df["DSCBASETARIFARIA"] == "Tarifa de Aplicação") &
        df["UNIDADE_MEDIDA"].str.contains("MWH", case=False, na=False)
    )
    df_r = df.loc[mask, ["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","TE"]].copy()
    df_r = df_r.rename(columns={"TE": "REATIVA"})
    df_r["REATIVA"] = pd.to_numeric(
        df_r["REATIVA"].astype(str).str.replace(",", ".", regex=False), errors="coerce"
    )
    return (
        df_r.groupby(["SIGLA_AGENTE","DATA_INICIO","DATA_FIM"], as_index=False)
            .agg({"REATIVA": "first"})
    )


# ----------------------------------------------------------------
# 4. TRANSFORMAÇÃO PRINCIPAL — recebe filtro de DSCDETALHE
# ----------------------------------------------------------------
def _transformar(df: pd.DataFrame, detalhe: str, tipo_tarifa: str) -> pd.DataFrame:
    df_r = _extrair_reativa(df)

    # Filtros
    df = df[
        (df["DSCBASETARIFARIA"] == "Tarifa de Aplicação") &
        (df["DSCDETALHE"]       == detalhe) &
        df["THS"].str.contains("Azul|Verde", case=False, na=False)
    ].copy()

    # Conversão numérica
    for col in ["TUSD", "TE"]:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", ".", regex=False), errors="coerce"
        )

    # Ajuste posto
    df["POSTO_TARIFARIO"] = df["POSTO_TARIFARIO"].replace({
        "Não Se Aplica": "Fora Ponta",
        "Nao Se Aplica": "Fora Ponta",
    })

    df_dem = df[df["UNIDADE_MEDIDA"].str.contains("KW",  na=False)]
    df_ene = df[df["UNIDADE_MEDIDA"].str.contains("MWH", na=False)]

    # Pivot demanda
    dem = df_dem.pivot_table(
        index=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS"],
        columns="POSTO_TARIFARIO", values="TUSD", aggfunc="first"
    ).reset_index()
    for col, alias in [("Ponta","DEM_P"), ("Fora Ponta","DEM_FP")]:
        if col not in dem.columns:
            dem[col] = None
    dem = dem.rename(columns={"Ponta":"DEM_P","Fora Ponta":"DEM_FP"})

    # Pivot energia
    ene = df_ene.pivot_table(
        index=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS","POSTO_TARIFARIO"],
        values=["TUSD","TE"], aggfunc="first"
    ).reset_index()

    ene_p  = ene[ene["POSTO_TARIFARIO"]=="Ponta"].rename(
        columns={"TUSD":"TUSD_ENCARGOS_P","TE":"TE_P"})
    ene_fp = ene[ene["POSTO_TARIFARIO"]=="Fora Ponta"].rename(
        columns={"TUSD":"TUSD_ENCARGOS_FP","TE":"TE_FP"})

    for col in ["TUSD_ENCARGOS_P","TE_P"]:
        if col not in ene_p.columns:
            ene_p[col] = None
    for col in ["TUSD_ENCARGOS_FP","TE_FP"]:
        if col not in ene_fp.columns:
            ene_fp[col] = None

    keys = ["SIGLA_AGENTE","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","THS"]
    tarifas = (
        dem
        .merge(ene_p [keys + ["TUSD_ENCARGOS_P","TE_P"]],  on=keys, how="left")
        .merge(ene_fp[keys + ["TUSD_ENCARGOS_FP","TE_FP"]], on=keys, how="left")
        .merge(df_r,  on=["SIGLA_AGENTE","DATA_INICIO","DATA_FIM"], how="left")
    )

    tarifas = tarifas.rename(columns={"SIGLA_AGENTE":"DISTRIBUIDORA","THS":"MOD_TARIFARIA"})
    tarifas["TIPO_TARIFA"] = tipo_tarifa

    cols_ordem = [
        "DISTRIBUIDORA","DATA_INICIO","DATA_FIM","CLASSE_TENSAO","MOD_TARIFARIA","TIPO_TARIFA",
        "DEM_P","DEM_FP","TUSD_ENCARGOS_P","TUSD_ENCARGOS_FP","TE_P","TE_FP","REATIVA"
    ]
    tarifas = tarifas[cols_ordem]

    cols_num = ["DEM_P","DEM_FP","TUSD_ENCARGOS_P","TUSD_ENCARGOS_FP","TE_P","TE_FP","REATIVA"]
    tarifas[cols_num] = tarifas[cols_num].round(2)

    return tarifas


# ----------------------------------------------------------------
# 5. EXPANSÃO MENSAL (para exportação Excel local)
# ----------------------------------------------------------------
def _expandir_mensal(tarifas: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for _, row in tarifas.iterrows():
        if pd.isna(row["DATA_INICIO"]) or pd.isna(row["DATA_FIM"]):
            continue
        meses = pd.date_range(
            start=row["DATA_INICIO"].to_period("M").to_timestamp(),
            end=row["DATA_FIM"], freq="MS"
        )
        for mes in meses:
            fim_mes   = mes + pd.offsets.MonthEnd(1)
            nova_linha = row.to_dict()
            nova_linha["MES_REF"]     = mes
            nova_linha["DATA_INICIO"] = max(row["DATA_INICIO"], mes)
            nova_linha["DATA_FIM"]    = min(row["DATA_FIM"], fim_mes)
            linhas.append(nova_linha)

    df_m = pd.DataFrame(linhas)
    df_m["ANO_MES"] = df_m["MES_REF"].dt.strftime("%Y-%m")
    return df_m


# ----------------------------------------------------------------
# 6. EXECUÇÃO
# ----------------------------------------------------------------
df_bruto = baixar_tarifas_aneel()

if df_bruto.empty:
    raise SystemExit("Nenhum registro baixado. Verifique a conexão.")

df_bruto = _padronizar(df_bruto)

# Base "Não se aplica"
tarifas         = _transformar(df_bruto, detalhe="Não se aplica", tipo_tarifa="-")
tarifas_mensal  = _expandir_mensal(tarifas)

# Base APE
tarifas_ape         = _transformar(df_bruto, detalhe="APE", tipo_tarifa="APE")
tarifas_ape_mensal  = _expandir_mensal(tarifas_ape)

print(f"Tarifas          : {len(tarifas):>6} linhas")
print(f"Tarifas APE      : {len(tarifas_ape):>6} linhas")
print(f"Tarifas mensal   : {len(tarifas_mensal):>6} linhas")
print(f"Tarifas APE mens.: {len(tarifas_ape_mensal):>6} linhas")

# Exportação Excel local (opcional)
tarifas.to_excel("Tarifas_ANEEL.xlsx",              index=False)
tarifas_ape.to_excel("Tarifas_ANEEL_APE.xlsx",      index=False)
tarifas_mensal.to_excel("Tarifas_Base_Mensal.xlsx",         index=False)
tarifas_ape_mensal.to_excel("Tarifas_APE_Base_Mensal.xlsx", index=False)

print("\nExportação Excel concluída.")