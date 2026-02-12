import pandas as pd


def padronizar_colunas(df):
    df.columns = (
        df.columns
        .str.strip()
        .str.upper()
        .str.normalize('NFKD')
        .str.encode('ascii', errors='ignore')
        .str.decode('utf-8')
        .str.replace(" ", "_")
    )
    return df