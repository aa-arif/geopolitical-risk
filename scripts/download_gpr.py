"""
Download GPR monthly data from Caldara & Iacoviello.
Usage: python -m scripts.download_gpr
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from config.settings import DATA_DIR


GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls"
OUTPUT_PATH = DATA_DIR / "data_gpr_export.xls"


def main():
    print(f"Downloading GPR data from {GPR_URL}...")

    try:
        resp = requests.get(
            GPR_URL,
            headers={"User-Agent": "GeoRisk/1.0 (research)"},
            timeout=60,
        )
        resp.raise_for_status()

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_bytes(resp.content)
        size_mb = len(resp.content) / (1024 * 1024)
        print(f"Downloaded {size_mb:.1f} MB to {OUTPUT_PATH}")

        # Verify
        try:
            import pandas as pd
            df = pd.read_excel(OUTPUT_PATH)
            gprc_cols = [c for c in df.columns if c.startswith("GPRC_")]
            date_col = df.columns[0]
            dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
            print(f"Verified: {len(gprc_cols)} country columns, {len(df)} rows")
            print(f"Date range: {dates.min().strftime('%Y-%m')} to {dates.max().strftime('%Y-%m')}")
            print(f"Countries: {', '.join(c.replace('GPRC_', '') for c in gprc_cols[:10])}...")
        except Exception as e:
            print(f"Warning: verification failed ({e}), but file was saved")

    except Exception as e:
        print(f"Automatic download failed: {e}")
        print()
        print("To get GPR data manually:")
        print("  1. Visit https://www.matteoiacoviello.com/gpr.htm")
        print('  2. Download the "Monthly" Excel file under "Geopolitical Risk Index"')
        print(f"  3. Save it as: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
