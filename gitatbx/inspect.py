from pathlib import Path
import polars as pl


def run_inspect(args) -> int:
    path = Path(args.parquet)
    if not path.exists():
        print(f"Error: file not found: {path}")
        return 1

    df = pl.read_parquet(path)
    print(f"File: {path}")
    print(f"Shape: {df.shape[0]} rows x {df.shape[1]} cols")
    print(f"Columns: {df.columns}")
    print("\nDtypes:")
    for col, dtype in zip(df.columns, df.dtypes):
        print(f"  {col}: {dtype}")
    print("\nPreview:")
    print(df.head(args.rows))
    return 0
