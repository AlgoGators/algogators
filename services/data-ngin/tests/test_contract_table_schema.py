from data_ngin.infrastructure.db_models import ContractOHLCV


def test_contract_ohlcv_table_columns_and_keys():
    assert ContractOHLCV.__tablename__ == "contract_ohlcv_1d"
    assert ContractOHLCV.__table_args__["schema"] == "futures_data"
    
    col_names = set(ContractOHLCV.__table__.columns.keys())
    expected_cols = {
        "instrument_id",
        "date",
        "root",
        "raw_symbol",
        "expiration",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "loaded_at",
    }
    assert expected_cols.issubset(col_names)
    assert ContractOHLCV.__table__.primary_key.columns.keys() == ["instrument_id", "date"]
