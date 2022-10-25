from __future__ import annotations

import json
import time
from datetime import datetime

import mintapi
import pandas as pd
import plotly
import plotly.express as px
from selenium.common.exceptions import WebDriverException

import net_worth_tracker as nwt

MINT_DATA_FOLDER = "mint_data"


def get_mint() -> mintapi.Mint:
    email = nwt.utils.get_password("email", "mint")
    password = nwt.utils.get_password("password", "mint")
    mint = mintapi.Mint(email, password)  # Takes about ≈1m30s
    return mint


def update_data(mint: mintapi.Mint, folder: str = MINT_DATA_FOLDER) -> None:
    # Get account information
    account_data = mint.get_account_data()
    # Get transactions
    transaction_data = mint.get_transaction_data(include_investment=True)
    # Get budget information
    budget_data = mint.get_budget_data()

    for name, data in [
        ("account_data", account_data),
        ("transaction_data", transaction_data),
        ("budget_data", budget_data),
    ]:
        prefix = f"{name}."
        fname = nwt.utils.fname_from_date(folder, prefix=prefix)
        with fname.open("w") as f:
            json.dump(data, f, indent=4)


def update(n_tries: int = 5) -> mintapi.Mint:
    mint = nwt.mint.get_mint()
    for _ in range(n_tries):
        try:
            nwt.mint.update_data(mint)
            print("Successfully updated data")
            return mint
        except WebDriverException:  # This error seems to randomly occur
            print("WebDriverException, retrying in 5 seconds...")
            time.sleep(5)


def load_latest_data(folder: str = MINT_DATA_FOLDER) -> dict[str, pd.DataFrame]:
    data = {}
    for name in ["account_data", "transaction_data", "budget_data"]:
        fname = nwt.utils.latest_fname(folder, prefix=f"{name}.")
        with fname.open("r") as f:
            df = pd.read_json(f)
            df = _convert_dates(df)
            if name == "budget_data":
                df = _parse_budget_data(df)
            elif name == "transaction_data":
                df = _parse_transaction_data(df)
                investment_data = df[df.type == "InvestmentTransaction"].copy()
                data["investments"] = _parse_investment_data(investment_data)
                df = df[df.type != "InvestmentTransaction"].copy()
            data[name] = df
    return data


def _convert_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert date string columns to datetimes."""
    date_cols = list(df.columns[df.columns.str.contains("Date")])
    if "date" in df.columns:
        date_cols.append("date")
    for col in date_cols:
        df[col] = pd.to_datetime(df[col])
    return df


def _expand_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Expand columns in a dataframe."""
    for col in columns:
        df = df.join(pd.json_normalize(df[col]).add_prefix(f"{col}."))
    return df


def _parse_budget_data(budget_data: pd.DataFrame) -> pd.DataFrame:
    df = budget_data
    df = _expand_columns(df, ["category"])
    return df


def _parse_transaction_data(transaction_data: pd.DataFrame) -> pd.DataFrame:
    df = transaction_data
    df = _expand_columns(df, ["accountRef", "category", "fiData"])

    # Make Shopping category with Amazon subcategory
    shopping = df[df["category.name"] == "Shopping"]
    amazon = shopping[shopping["description"].str.contains("Amazon")]
    df.loc[shopping.index, "category.parentName"] = "Shopping"
    df.loc[amazon.index, "category.name"] = "Amazon"

    return df


def _parse_investment_data(
    investment_data: pd.DataFrame, ignore_before: str | datetime = "2022-02-01"
) -> pd.DataFrame:
    investment_data.sort_values(by="date", inplace=True)
    investment_data["amount_cumsum"] = investment_data.amount.cumsum()
    # Do not consider transactions before ignore_before
    investment_data = investment_data[investment_data.date >= ignore_before]
    first = investment_data.iloc[0]
    investment_data["ndays"] = (investment_data.date - first.date).dt.days
    investment_data["daily_investments"] = (
        investment_data.amount_cumsum / investment_data.ndays
    )
    return investment_data


def plot_budget_spending(budget_data: pd.DataFrame) -> plotly.graph_objs.Figure:
    budget_data = budget_data[budget_data["category.name"] != "Income"]
    budget_data = (
        budget_data.groupby(["category.parentName", "category.name"])["amount"]
        .sum()
        .reset_index()
    )
    return px.sunburst(
        budget_data, path=["category.parentName", "category.name"], values="amount"
    )


def plot_categories(transaction_data):
    gb = (
        transaction_data.groupby(["category.parentName", "category.name"])["amount"]
        .sum()
        .reset_index()
    )
    for i, row in transaction_data.iterrows():
        sel = (gb["category.parentName"] == row["category.parentName"]) & (
            row["category.name"] == gb["category.name"]
        )
        transaction_data.loc[i, "amount_tot"] = gb[sel].amount.item()
    df = transaction_data[transaction_data.amount_tot < 0].copy()
    df = df[
        (df["category.name"] != "Transfer")
        & (df["category.parentName"] != "Transfer")
        & (df["category.parentName"] != "Investments")
    ]
    df["pct"] = df["amount"] / df["amount"].sum() * 100

    df["amount"] = -df["amount"]
    return px.sunburst(
        df,
        path=[
            "category.parentName",
            "category.name",
            # "description",
        ],
        values="amount",
    )
